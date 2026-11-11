"""HTTP API: /converse, /converse/stream, /speaker-hint, /health (docs/PROJECT_SPEC.md, §4.2)."""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import __version__, home_quick, intents
from .code import PROMPT as CODE_PROMPT
from .code import TOOL_NAMES as CODE_TOOL_NAMES
from .code import TOOLS as CODE_TOOLS
from .code import Announcement, Announcements, CodeTasks
from .config import Config
from .ha import TOOL, Device, HomeAssistant, devices_prompt, parse_args
from .llm import (
    AUDIO_TAGS_PROMPT,
    NO_HOME_PROMPT,
    SYSTEM_PROMPT,
    WEB_SEARCH_TOOL,
    ChatModel,
    Conversations,
    Final,
    ToolCall,
    agent_sentences,
    reply_sentences,
)
from .memory import TOOL_NAMES as MEMORY_TOOL_NAMES
from .memory import TOOLS as MEMORY_TOOLS
from .memory import Memory, quick
from .memory.store import Reminder
from .video import NAMES as VIDEO_NAMES
from .video import PROMPT as VIDEO_PROMPT
from .video import TOOLS as VIDEO_TOOLS
from .video import Video
from .weather import Weather
from .weather import kind as weather_kind
from .weather import speech as speech_weather

_LOGGER = logging.getLogger(__name__)

OFFLINE_SPEECH = "Нет связи с облаком, думать сейчас не могу. Время, таймеры и свет работают и так."


@dataclass
class Outage:
    """Облако не ответило: какое-то время не ждём его зря, а сразу говорим об этом.

    Без этого каждая фраза при лежащем VPN висит до таймаута LiteLLM с повторами
    и запасной моделью — до минуты тишины.
    """

    cooldown_s: float = 30.0
    down_until: float = 0.0
    last_error: str = ""

    def active(self, now: float) -> bool:
        return now < self.down_until

    def failed(self, now: float, error: BaseException) -> None:
        self.down_until = now + self.cooldown_s
        self.last_error = f"{type(error).__name__}: {error}"[:200]

    def ok(self) -> None:
        self.down_until = 0.0
        self.last_error = ""


_OUTAGE_NAMES = ("Connection", "Timeout", "ServiceUnavailable", "Network")


def is_outage(error: BaseException) -> bool:
    """Облако недоступно (сеть, таймаут, 503), а не ошибка в запросе или в коде."""
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    return any(name in type(error).__name__ for name in _OUTAGE_NAMES)


class ConverseRequest(BaseModel):
    text: str
    conversation_id: str | None = None
    device_id: str | None = None
    language: str = "ru"
    # Голос понимает теги эмоций вроде [laughs].
    audio_tags: bool = False


@dataclass
class TurnState:
    """Что решили по ходу ответа: слушать ли продолжение без имени."""

    follow: bool = True
    path: str = "llm"


# После этих инструментов играет видео: продолжение разговора услышит сам фильм
# («Д», «Две чем сломал во?») и модель перезапустит его. Не слушаем.
_PLAYBACK_STARTED = {"play_video", "play_result"}


class ConverseResponse(BaseModel):
    speech: str
    continue_conversation: bool = False
    conversation_id: str | None = None


class SpeakerHint(BaseModel):
    speaker: str
    confidence: float
    ts: float | None = None


@dataclass
class HintStore:
    """Последняя подсказка о говорящем от STT, сопоставляется по времени."""

    window_s: float
    last: SpeakerHint | None = None

    def put(self, hint: SpeakerHint) -> None:
        if hint.ts is None:
            hint = hint.model_copy(update={"ts": time.time()})
        self.last = hint

    def speaker_for(self, now: float) -> str:
        hint = self.last
        if hint is None or hint.ts is None or now - hint.ts > self.window_s:
            return "guest"
        return hint.speaker


async def sse_events(announcements: Announcements, ping_s: float = 15) -> AsyncIterator[str]:
    """Бесконечный поток SSE; без событий раз в `ping_s` — комментарий, чтобы связь не рвалась."""
    queue = announcements.listen()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), ping_s)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
        announcements.unlisten(queue)


def create_app(
    config: Config | None = None,
    chat: ChatModel | None = None,
    home: HomeAssistant | None = None,
    video: Video | None = None,
    code: CodeTasks | None = None,
    memory: Memory | None = None,
    weather: Weather | None = None,
) -> FastAPI:
    """`chat=None` — заглушка без LLM (тесты, стенд без ключа). `home=None` — без дома,
    `video=None` — без телевизора, `code=None` — без задач Claude Code,
    `memory=None` — без долгой памяти и таймеров."""
    config = config or Config()
    hints = HintStore(window_s=config.speaker_hint_window_s)
    conversations = Conversations(config.assistant.history_turns)
    announcements = code.announcements if code is not None else Announcements()
    outage = Outage()
    # Каждое «Волтер, …» голос начинает новым разговором. Чтобы «а какой был третий?»
    # через минуту после поиска понималось, новый разговор продолжает недавний.
    recent: list[tuple[str, float]] = []
    recent_window_s = 180.0

    def conversation_for(request: ConverseRequest) -> str:
        now = time.monotonic()
        if request.conversation_id:
            cid = request.conversation_id
        elif recent and now - recent[0][1] < recent_window_s:
            cid = recent[0][0]
        else:
            cid = uuid.uuid4().hex
        recent[:] = [(cid, now)]
        return cid

    async def briefing() -> str:
        """Погода и дела на сегодня: после будильника и на «доброе утро»."""
        parts = []
        if weather is not None:
            with contextlib.suppress(Exception):
                parts.append(speech_weather("now", await weather.data()))
        if memory is not None and (day := memory.day_speech()):
            parts.append(day)
        return " ".join(parts)

    async def announce(reminder: Reminder) -> None:
        text = reminder.speech(time.time())
        if reminder.kind == "alarm" and reminder.rings == 0:
            # Первый звонок будильника — сразу со сводкой на день.
            text = f"{text} {await briefing()}".strip()
        # Будильник ждёт «стоп» без имени.
        announcements.publish(Announcement(text, listen=reminder.kind == "alarm"))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = None
        if memory is not None:
            task = asyncio.create_task(memory.run_reminders(announce))
        yield
        if task is not None:
            task.cancel()

    app = FastAPI(title="bayun-brain", version=__version__, lifespan=lifespan)

    async def quick_light(text: str) -> str | None:
        """«Включи свет» без LLM. None — пусть разбирается модель."""
        if home is None or (command := home_quick.match(text)) is None:
            return None
        try:
            devices = await home.devices()
        except Exception:
            _LOGGER.exception("Home Assistant не ответил")
            return "Умный дом сейчас не отвечает."
        lights = [(d.entity_id, d.name) for d in devices if d.domain == "light"]
        groups = {d.entity_id: d.members for d in devices if d.members}
        calls = command.calls(lights, groups)
        if not calls:
            return None
        try:
            outcomes = [await home.control(devices, call) for call in calls]
        except Exception:
            _LOGGER.exception("Home Assistant не ответил")
            return "Умный дом сейчас не отвечает."
        if any(o.startswith("Не получилось") for o in outcomes):
            return "Не получилось, умный дом ответил ошибкой."
        return command.speech

    async def sentences(
        request: ConverseRequest, conversation_id: str, turn: TurnState | None = None
    ) -> AsyncIterator[str]:
        turn = turn if turn is not None else TurnState()
        turn.follow = chat is not None
        speaker = hints.speaker_for(time.time())
        if intents.only_name(request.text):
            turn.path = "name"
            yield "Слушаю."
            return
        if code is not None and code.owns(request.conversation_id):
            # Ответ на вопрос Claude Code сразу после объявления: без LLM.
            yield await code.run(
                "code_answer", {"text": request.text}, request.device_id, request.conversation_id
            )
            return
        if memory is not None and (answer := quick.answer(request.text, memory, time.time())):
            _LOGGER.info("Быстрый ответ без LLM: %s", answer)
            turn.path, turn.follow = "quick", False
            conversations.remember(conversation_id, request.text, answer)
            yield answer
            return
        if intents.is_noise(request.text):
            turn.path, turn.follow = "noise", False
            _LOGGER.info("Обрывок, не отвечаю: %r", request.text)
            return
        if (answer := intents.goodbye(request.text)) is not None:
            turn.path, turn.follow = "bye", False
            conversations.remember(conversation_id, request.text, answer)
            yield answer
            return
        if weather_kind(request.text) == "morning":
            answer = f"Доброе утро! {await briefing()}".strip()
            conversations.remember(conversation_id, request.text, answer)
            yield answer
            return
        if weather is not None and (answer := await weather.answer(request.text)):
            _LOGGER.info("Погода без LLM: %s", answer)
            turn.path, turn.follow = "quick", False
            conversations.remember(conversation_id, request.text, answer)
            yield answer
            return
        if (answer := await quick_light(request.text)) is not None:
            _LOGGER.info("Свет без LLM: %s", answer)
            turn.path, turn.follow = "quick", False
            conversations.remember(conversation_id, request.text, answer)
            yield answer
            return
        if video is not None and (intent := intents.match(request.text)):
            started = time.monotonic()
            outcome = await video.run(intent.tool, intent.args)
            _LOGGER.info(
                "Быстрая команда %s %s за %.2f с: %s",
                intent.tool, intent.args, time.monotonic() - started, outcome,
            )  # fmt: skip
            conversations.remember(conversation_id, request.text, outcome)
            # «Нашёл 8, какой включить?» — ждём номер; остальное — команда выполнена.
            turn.path, turn.follow = "quick", outcome.endswith("?")
            yield outcome
            return
        if chat is None:
            name = "" if speaker == "guest" else f"{speaker}, "
            yield f"{name}я пока учусь и ещё ничего не умею."
            return

        if outage.active(time.monotonic()):
            _LOGGER.info("Облако недавно не ответило, LLM не зову")
            yield OFFLINE_SPEECH
            return

        system = SYSTEM_PROMPT.format(name=config.assistant.name)
        devices: list[Device] = []
        if home is not None:
            try:
                devices = await home.devices()
                system += devices_prompt(devices)
            except Exception:
                _LOGGER.exception("Home Assistant не ответил")
                system += "\nHome Assistant сейчас недоступен, устройствами управлять нельзя."
        else:
            system += NO_HOME_PROMPT
        tools = [TOOL] if devices else []
        search = getattr(chat, "search", None)
        if search is not None:
            tools.append(WEB_SEARCH_TOOL)
        if video is not None:
            system += VIDEO_PROMPT
            tools += VIDEO_TOOLS
        if code is not None:
            system += CODE_PROMPT + code.prompt_hint()
            tools += CODE_TOOLS
        if memory is not None:
            system += memory.prompt()
            tools += MEMORY_TOOLS
        if request.audio_tags:
            system += AUDIO_TAGS_PROMPT
        if speaker != "guest":
            system += f"\nСейчас говорит: {speaker}."
        messages = conversations.messages(conversation_id, system, request.text)
        said: list[str] = []
        started = time.monotonic()
        try:

            async def run_tool(call: ToolCall) -> str:
                args = parse_args(call.arguments)
                try:
                    outcome = await call_tool(call.name, args)
                except Exception as error:
                    # Ошибка инструмента — не ошибка облака: модель скажет, что не вышло.
                    _LOGGER.exception("Инструмент %s упал", call.name)
                    outcome = f"Не получилось: {type(error).__name__}."
                _LOGGER.info("Инструмент %s %s: %s", call.name, args, outcome)
                if call.name in _PLAYBACK_STARTED or (
                    call.name == "control_video" and args.get("action") == "play"
                ):
                    turn.follow = False
                if not args and call.arguments:
                    _LOGGER.warning("Аргументы %s не разобрались: %r", call.name, call.arguments)
                return outcome

            async def call_tool(name: str, args: dict[str, Any]) -> str:
                if name == "control_device" and home is not None:
                    outcome = await home.control(devices, args)
                    # Удалось — сразу «Готово», без второго круга модели (минус секунда).
                    if outcome.startswith("Готово"):
                        outcome = Final("Готово.")
                elif name in VIDEO_NAMES and video is not None:
                    outcome = Final(await video.run(name, args))
                elif name == "web_search" and search is not None:
                    question = str(args.get("question") or request.text)
                    outcome = Final(await search(question))
                elif name in MEMORY_TOOL_NAMES and memory is not None:
                    if name == "list_reminders":
                        outcome = memory.run(name, {"with_id": True})
                    else:
                        outcome = Final(memory.run(name, args))
                elif name in CODE_TOOL_NAMES and code is not None:
                    outcome = Final(await code.run(name, args, request.device_id, conversation_id))
                else:
                    outcome = "Нет такого инструмента."
                return outcome

            source = (
                agent_sentences(chat, messages, tools, run_tool)
                if tools
                else reply_sentences(chat, messages)
            )
            async for sentence in source:
                if sentence.strip(" .…-—") == "":
                    # Модель решила промолчать: фраза не ей (см. SYSTEM_PROMPT).
                    turn.follow = False
                    continue
                if not said:
                    _LOGGER.info("Первая фраза через %.2f с", time.monotonic() - started)
                said.append(sentence)
                yield sentence
        except Exception as error:
            _LOGGER.exception("LLM не ответила")
            if is_outage(error):
                outage.failed(time.monotonic(), error)
            if not said:
                yield OFFLINE_SPEECH
            return
        outage.ok()
        conversations.remember(conversation_id, request.text, " ".join(said))

    def log_turn(text: str, speech: str, turn: TurnState, seconds: float) -> None:
        # Без этого по журналу не понять, что Баюн ответил и почему ошибся.
        _LOGGER.info(
            "Реплика %r → %r [%s, %.2f с, %s]",
            text, speech, turn.path, seconds, "слушаю дальше" if turn.follow else "жду имя",
        )  # fmt: skip

    @app.get("/api/dashboard")
    async def dashboard() -> dict[str, Any]:
        """Для экрана Баюна: что поставлено, что запомнено, погода."""
        reminders = memory.pending() if memory is not None else []
        facts = memory.facts() if memory is not None else []
        return {
            "now": time.time(),
            "reminders": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "text": r.text,
                    "due_at": r.due_at,
                    "ringing": memory is not None and r.kind == "alarm" and r.id in ringing(),
                }
                for r in reminders
            ],
            "facts": [{"id": f.id, "text": f.text} for f in facts],
            "weather": await weather.summary() if weather is not None else None,
        }

    def ringing() -> set[int]:
        return memory.ringing_ids() if memory is not None else set()

    @app.delete("/api/reminders/{reminder_id}")
    async def delete_reminder(reminder_id: int) -> dict[str, int]:
        return {"deleted": memory.cancel(reminder_id) if memory is not None else 0}

    @app.delete("/api/facts/{fact_id}")
    async def delete_fact(fact_id: int) -> dict[str, int]:
        return {"deleted": int(memory.forget(fact_id)) if memory is not None else 0}

    @app.get("/health")
    async def health() -> dict[str, str]:
        state = {"status": "ok", "version": __version__}
        if chat is not None:
            state["llm"] = "down" if outage.active(time.monotonic()) else "ok"
            if outage.last_error:
                state["llm_error"] = outage.last_error
        return state

    @app.post("/speaker-hint")
    async def speaker_hint(hint: SpeakerHint) -> dict[str, str]:
        hints.put(hint)
        return {"status": "ok"}

    @app.get("/announcements")
    async def announcements_stream() -> StreamingResponse:
        """SSE: то, что Баюн говорит сам (docs/claude-code-voice.md, «Как Баюн говорит сам»).

        `data: {"type": "announcement", ...}` или `data: {"type": "resolved", "id": ...}`.
        """
        return StreamingResponse(sse_events(announcements), media_type="text/event-stream")

    @app.post("/converse")
    async def converse(request: ConverseRequest) -> ConverseResponse:
        conversation_id = conversation_for(request)
        turn = TurnState()
        started = time.monotonic()
        speech = " ".join([s async for s in sentences(request, conversation_id, turn)])
        log_turn(request.text, speech, turn, time.monotonic() - started)
        return ConverseResponse(
            speech=speech,
            continue_conversation=turn.follow and bool(speech),
            conversation_id=conversation_id,
        )

    @app.post("/converse/stream")
    async def converse_stream(request: ConverseRequest) -> StreamingResponse:
        """NDJSON: по строке {"speech": ...} на предложение, в конце {"done": true, ...}."""
        conversation_id = conversation_for(request)

        async def lines() -> AsyncIterator[str]:
            turn = TurnState()
            started = time.monotonic()
            said: list[str] = []
            async for sentence in sentences(request, conversation_id, turn):
                said.append(sentence)
                yield json.dumps({"speech": sentence}, ensure_ascii=False) + "\n"
            log_turn(request.text, " ".join(said), turn, time.monotonic() - started)
            done = {
                "done": True,
                "conversation_id": conversation_id,
                "continue_conversation": turn.follow and bool(said),
            }
            yield json.dumps(done) + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    return app
