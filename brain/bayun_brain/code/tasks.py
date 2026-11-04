"""Задачи Claude Code голосом: запуск в фоне, вопросы и подтверждения, итог.

Сам Claude Code запускается через `Session` (настоящая — в `sdk.py`, в тестах — фейк).
Всё, что Баюн должен сказать без вопроса пользователя, уходит в `Announcements`.
"""

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from ..config import CodeConfig
from .policy import Decision, decide

_LOGGER = logging.getLogger(__name__)


# ---------- объявления: то, что Баюн говорит сам ----------


@dataclass
class Announcement:
    text: str
    # Голос после фразы слушает ответ без имени и шлёт его в /converse
    # с этим conversation_id.
    expects_reply: bool = False
    project: str = ""
    # Полный текст для экрана (итог Claude Code, команда целиком).
    details: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def conversation_id(self) -> str:
        return f"code-{self.id}"

    def event(self) -> dict[str, Any]:
        return {
            "type": "announcement",
            "id": self.id,
            "text": self.text,
            "expects_reply": self.expects_reply,
            "conversation_id": self.conversation_id,
            "project": self.project,
            "details": self.details,
        }


class Announcements:
    """Очередь для подписчиков SSE. Вопросы, ждущие ответа, отдаются и новым подписчикам."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._open: dict[str, Announcement] = {}

    def publish(self, announcement: Announcement) -> None:
        if announcement.expects_reply:
            self._open[announcement.id] = announcement
        _LOGGER.info("Объявление: %s", announcement.text)
        self._send(announcement.event())

    def resolve(self, announcement_id: str) -> None:
        """Вопрос закрыт (ответили или задача кончилась): голос может его не говорить."""
        if self._open.pop(announcement_id, None) is not None:
            self._send({"type": "resolved", "id": announcement_id})

    def listen(self) -> asyncio.Queue[dict[str, Any]]:
        """Очередь событий для одного подписчика; открытые вопросы — сразу в ней."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for announcement in self._open.values():
            queue.put_nowait(announcement.event())
        self._subscribers.add(queue)
        return queue

    def unlisten(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def _send(self, event: dict[str, Any]) -> None:
        for queue in self._subscribers:
            queue.put_nowait(event)


# ---------- сессия Claude Code ----------


@dataclass
class Question:
    question: str
    options: list[str]


@dataclass
class SessionResult:
    session_id: str | None
    text: str
    is_error: bool = False


AskUser = Callable[[list[Question]], Awaitable[list[str]]]
CanUseTool = Callable[[str, dict[str, Any]], Awaitable[Decision]]


@dataclass
class SessionRequest:
    workdir: Path
    prompt: str
    resume: str | None
    can_use_tool: CanUseTool
    ask_user: AskUser
    on_progress: Callable[[str], None]


class Session(Protocol):
    async def __call__(self, request: SessionRequest) -> SessionResult: ...


Summarize = Callable[[str], Awaitable[str]]


async def first_sentences(text: str) -> str:
    """Сжатие без LLM: первые две фразы."""
    parts = re.split(r"(?<=[.!?…])\s+", " ".join(text.split()))
    return " ".join(parts[:2])[:300]


# ---------- задачи ----------

Kind = Literal["question", "permission"]


@dataclass
class Pending:
    kind: Kind
    project: str
    announcement: Announcement
    options: list[str]
    future: asyncio.Future[str]


@dataclass
class Task:
    project: str
    text: str
    phase: str = "работает"
    last_action: str = ""
    runner: asyncio.Task[None] | None = None


_YES = re.compile(r"^(да|ага|угу|давай|можно|разрешаю|разреши|пушь|запушь|конечно|ок|окей)\b")
_ORDINALS = {
    "перв": 0,
    "один": 0,
    "1": 0,
    "втор": 1,
    "два": 1,
    "2": 1,
    "трет": 2,
    "три": 2,
    "3": 2,
    "четв": 3,
    "четыре": 3,
    "4": 3,
}
_NUMBER_WORDS = ["Первое", "Второе", "Третье", "Четвёртое"]


def _norm(text: str) -> str:
    return re.sub(r"[^\w\s]", " ", text.lower().replace("ё", "е")).strip()


def pick_option(answer: str, options: list[str]) -> str:
    """«второе», «давай второй», «пайтест» → текст варианта; иначе — сам ответ."""
    said = _norm(answer)
    for option in options:
        if _norm(option) and _norm(option) in said:
            return option
    for word in said.split():
        for stem, index in _ORDINALS.items():
            if (word.startswith(stem) if not stem.isdigit() else word == stem) and index < len(
                options
            ):
                return options[index]
    return answer.strip()


class CodeTasks:
    def __init__(
        self,
        config: CodeConfig,
        session: Session,
        summarize: Summarize = first_sentences,
    ) -> None:
        self.config = config
        self.session = session
        self.summarize = summarize
        self.announcements = Announcements()
        self.tasks: dict[str, Task] = {}
        self.sessions: dict[str, str] = {}  # проект → session_id для продолжения
        self.pending: list[Pending] = []

    # --- что видит LLM brain ---

    def project_for(self, spoken: str) -> str | None:
        projects = list(self.config.projects)
        said = _norm(spoken)
        if not said and len(projects) == 1:
            return projects[0]
        for name in projects:
            stem = _norm(name)
            stem = stem[: max(3, len(stem) - 1)]
            if any(word.startswith(stem) for word in said.split()):
                return name
        return None

    def prompt_hint(self) -> str:
        if not self.pending:
            running = ", ".join(self.tasks) or "нет"
            return f"\nClaude Code сейчас работает над проектами: {running}."
        return (
            "\nClaude Code ждёт ответа на вопрос: «"
            + self.pending[0].announcement.text
            + "». Если реплика похожа на ответ, передай её в code_answer."
        )

    def owns(self, conversation_id: str | None) -> bool:
        """Ответ на объявление-вопрос: разбираем без LLM."""
        return any(p.announcement.conversation_id == conversation_id for p in self.pending)

    async def run(
        self,
        name: str,
        args: dict[str, Any],
        device_id: str | None,
        conversation_id: str | None = None,
    ) -> str:
        if self.config.allowed_devices and device_id not in self.config.allowed_devices:
            return "Поручать задачи Клоду можно только с микрофона ноутбука."
        if name == "code_task":
            return self.start(str(args.get("project") or ""), str(args.get("text") or ""))
        if name == "code_answer":
            return self.answer(str(args.get("text") or ""), conversation_id)
        if name == "code_status":
            return self.status(str(args.get("project") or ""))
        if name == "code_cancel":
            return self.cancel(str(args.get("project") or ""))
        return "Нет такого инструмента."

    def start(self, spoken_project: str, text: str) -> str:
        project = self.project_for(spoken_project)
        if project is None:
            known = ", ".join(self.config.projects) or "ни одного"
            return f"Не знаю такого проекта. Настроены: {known}."
        if not text.strip():
            return "А что сделать?"
        if project in self.tasks:
            return f"Клод ещё занят в проекте {project}. Дождёмся или остановить?"
        task = Task(project, text)
        task.runner = asyncio.create_task(self._run(task))
        self.tasks[project] = task
        return "Отдал Клоду. Скажу, когда закончит."

    def answer(self, text: str, conversation_id: str | None = None) -> str:
        pending = next(
            (p for p in self.pending if p.announcement.conversation_id == conversation_id),
            self.pending[0] if self.pending else None,
        )
        if pending is None:
            return "Клод сейчас ничего не спрашивает."
        self._close(pending)
        if pending.kind == "permission":
            allowed = bool(_YES.match(_norm(text)))
            pending.future.set_result("yes" if allowed else "no")
            return "Разрешил." if allowed else "Хорошо, не разрешаю."
        choice = pick_option(text, pending.options)
        pending.future.set_result(choice)
        return f"Передал Клоду: {choice}."

    def status(self, spoken_project: str) -> str:
        if not self.tasks:
            return "Клод сейчас ничем не занят."
        project = self.project_for(spoken_project) if spoken_project else None
        tasks = [self.tasks[project]] if project in self.tasks else list(self.tasks.values())
        parts = []
        for task in tasks:
            action = f", последнее: {task.last_action}" if task.last_action else ""
            parts.append(f"В проекте {task.project} Клод {task.phase}{action}.")
        return " ".join(parts)

    def cancel(self, spoken_project: str) -> str:
        project = self.project_for(spoken_project) if spoken_project else None
        targets = [project] if project in self.tasks else list(self.tasks)
        if not targets or (project is None and len(targets) > 1 and spoken_project):
            return "Не понял, какую задачу остановить."
        for name in targets:
            runner = self.tasks[name].runner
            if runner is not None:
                runner.cancel()
        return "Остановил Клода."

    # --- работа задачи ---

    async def _run(self, task: Task) -> None:
        workdir = Path(self.config.projects[task.project]).expanduser()
        prompt = task.text + VOICE_RULES.format(prefix=self.config.push_branch_prefix)

        def on_progress(action: str) -> None:
            task.last_action = action

        request = SessionRequest(
            workdir=workdir,
            prompt=prompt,
            resume=self.sessions.get(task.project),
            can_use_tool=lambda tool, data: self._can_use_tool(task, workdir, tool, data),
            ask_user=lambda questions: self._ask_user(task, questions),
            on_progress=on_progress,
        )
        try:
            result = await self.session(request)
        except Exception as error:
            _LOGGER.exception("Claude Code упал")
            result = SessionResult(None, f"Ошибка: {error}", is_error=True)
        finally:
            self._drop_pending(task.project)
            self.tasks.pop(task.project, None)
        if result.session_id:
            self.sessions[task.project] = result.session_id
        await self._report(task, result)

    async def _report(self, task: Task, result: SessionResult) -> None:
        try:
            short = await self.summarize(result.text) if result.text else ""
        except Exception:
            _LOGGER.exception("Не удалось сжать итог")
            short = await first_sentences(result.text)
        if result.is_error:
            speech = f"У Клода в проекте {task.project} не получилось. {short}"
        else:
            speech = f"Готово в проекте {task.project}. {short}"
        self.announcements.publish(
            Announcement(speech.strip(), project=task.project, details=result.text)
        )

    async def _can_use_tool(
        self, task: Task, workdir: Path, tool: str, data: dict[str, Any]
    ) -> Decision:
        decision = decide(tool, data, workdir, self.config.push_branch_prefix)
        _LOGGER.info("Клод %s %s: %s %s", tool, data, decision.verdict, decision.reason)
        if decision.verdict != "ask":
            return decision
        if self.config.push_without_asking and decision.reason.startswith("запушить"):
            return Decision("allow")
        task.phase = "ждёт разрешения"
        reply = await self._wait(
            task,
            "permission",
            f"Клод в проекте {task.project} хочет {decision.reason}. Разрешить?",
            [],
            details=_describe(tool, data),
        )
        task.phase = "работает"
        if reply == "yes":
            return Decision("allow")
        return Decision("deny", "Пользователь голосом не разрешил это действие.")

    async def _ask_user(self, task: Task, questions: list[Question]) -> list[str]:
        answers = []
        task.phase = "ждёт твоего выбора"
        for question in questions:
            options = question.options[: len(_NUMBER_WORDS)]
            spoken = " ".join(f"{_NUMBER_WORDS[i]} — {option}." for i, option in enumerate(options))
            speech = f"Клод спрашивает про {task.project}: {question.question} {spoken}"
            if options:
                speech += " Что выбрать?"
            answers.append(await self._wait(task, "question", speech, options))
        task.phase = "работает"
        return answers

    async def _wait(
        self, task: Task, kind: Kind, speech: str, options: list[str], details: str = ""
    ) -> str:
        announcement = Announcement(
            speech, expects_reply=True, project=task.project, details=details
        )
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        pending = Pending(kind, task.project, announcement, options, future)
        self.pending.append(pending)
        self.announcements.publish(announcement)
        try:
            return await asyncio.wait_for(future, self.config.answer_timeout_min * 60)
        except TimeoutError:
            self._close(pending)
            return "no" if kind == "permission" else "Ответа нет, выбери сам разумный вариант."

    def _close(self, pending: Pending) -> None:
        if pending in self.pending:
            self.pending.remove(pending)
        self.announcements.resolve(pending.announcement.id)

    def _drop_pending(self, project: str) -> None:
        for pending in [p for p in self.pending if p.project == project]:
            self._close(pending)
            if not pending.future.done():
                pending.future.cancel()


def _describe(tool: str, data: dict[str, Any]) -> str:
    if tool == "Bash":
        return str(data.get("command", ""))
    return f"{tool}: {data}"


VOICE_RULES = """

Задачу продиктовали голосом, ответ тоже прозвучит голосом.
- Если нужен выбор пользователя, спроси через AskUserQuestion: 2–4 коротких варианта, \
понятных на слух.
- Работай в отдельной ветке {prefix}<короткое-имя>. Push — только явно в неё: \
git push -u origin {prefix}<имя>. В main не пушь и не мержи.
- В конце ответь 2–3 предложениями по-русски, без markdown: что сделано, что с проверками, \
где результат."""


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "code_task",
            "description": (
                "Поручить задачу программисту Claude Code (его называют Клод) "
                "в одном из проектов на компьютере. Он работает в фоне и сам скажет итог."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "название проекта, как сказали"},
                    "text": {"type": "string", "description": "задача своими словами целиком"},
                },
                "required": ["project", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_answer",
            "description": "Ответ на вопрос Claude Code или разрешение: «второе», «да», «нет».",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "ответ как сказали"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_status",
            "description": "Что сейчас делает Claude Code.",
            "parameters": {
                "type": "object",
                "properties": {"project": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_cancel",
            "description": "Остановить задачу Claude Code.",
            "parameters": {
                "type": "object",
                "properties": {"project": {"type": "string"}},
            },
        },
    },
]

TOOL_NAMES = {tool["function"]["name"] for tool in TOOLS}

PROMPT = """
Ты умеешь поручать задачи по коду программисту Claude Code (Клоду) инструментом \
code_task. Он работает минуты, ты не ждёшь: скажи, что отдал, итог он сообщит сам."""
