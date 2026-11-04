import asyncio
import json
from typing import Any

from bayun_brain.api import create_app
from bayun_brain.code.policy import Decision
from bayun_brain.code.tasks import (
    Announcement,
    CodeTasks,
    Question,
    SessionRequest,
    SessionResult,
    pick_option,
)
from bayun_brain.config import CodeConfig
from fastapi.testclient import TestClient

CONFIG = CodeConfig(projects={"баюн": "/tmp/bayun-dev"})


class FakeSession:
    """Сценарий Claude Code: спросить выбор, попросить push, отчитаться."""

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.release = asyncio.Event()

    async def __call__(self, request: SessionRequest) -> SessionResult:
        answers = await request.ask_user(
            [Question("Как приглушать звук?", ["через PipeWire", "через плеер"])]
        )
        self.calls.append(answers)
        decision = await request.can_use_tool(
            "Bash", {"command": "git push -u origin claude/voice-ducker"}
        )
        self.calls.append(decision)
        safe = await request.can_use_tool("Bash", {"command": "uv run pytest"})
        self.calls.append(safe)
        await self.release.wait()
        return SessionResult("sess-1", "Тест починен. Все проверки зелёные. Ветка запушена.")


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_question_permission_and_report_flow() -> None:
    session = FakeSession()
    code = CodeTasks(CONFIG, session)
    events = code.announcements.listen()

    speech = await code.run("code_task", {"project": "в баюне", "text": "почини"}, "server")
    assert speech.startswith("Отдал Клоду")
    await settle()

    question = events.get_nowait()
    assert question["expects_reply"] is True
    assert "Второе — через плеер" in question["text"]
    assert code.owns(question["conversation_id"])

    reply = await code.run("code_answer", {"text": "давай второе"}, "server")
    assert reply == "Передал Клоду: через плеер."
    assert events.get_nowait() == {"type": "resolved", "id": question["id"]}
    await settle()

    permission = events.get_nowait()
    assert "запушить ветку" in permission["text"]
    assert "git push" in permission["details"]
    assert code.answer("да, пушь", permission["conversation_id"]) == "Разрешил."
    await settle()

    assert session.calls == [["через плеер"], Decision("allow"), Decision("allow")]
    assert "работает" in code.status("")

    session.release.set()
    await settle()
    events.get_nowait()  # resolved
    report = events.get_nowait()
    assert report["text"].startswith("Готово в проекте баюн. Тест починен.")
    assert report["expects_reply"] is False
    assert code.sessions["баюн"] == "sess-1"
    assert code.status("") == "Клод сейчас ничем не занят."


async def test_unclear_answer_means_no() -> None:
    session = FakeSession()
    code = CodeTasks(CONFIG, session)
    events = code.announcements.listen()
    code.start("баюн", "почини")
    await settle()
    code.answer("первое")
    await settle()
    events.get_nowait(), events.get_nowait()
    permission = events.get_nowait()
    assert code.answer("ну не знаю", permission["conversation_id"]) == "Хорошо, не разрешаю."
    await settle()
    assert session.calls[1].verdict == "deny"
    code.cancel("")
    await settle()


async def test_cancel_closes_open_questions() -> None:
    code = CodeTasks(CONFIG, FakeSession())
    code.start("баюн", "почини")
    await settle()
    assert code.pending
    assert code.cancel("баюн") == "Остановил Клода."
    await settle()
    assert not code.pending
    assert not code.tasks


async def test_guest_device_cannot_start_tasks() -> None:
    code = CodeTasks(CONFIG, FakeSession())
    speech = await code.run("code_task", {"project": "баюн", "text": "x"}, "kitchen")
    assert "только с микрофона" in speech
    assert not code.tasks


def test_unknown_project_lists_known() -> None:
    code = CodeTasks(CONFIG, FakeSession())
    assert "баюн" in code.start("сайт", "почини")


def test_pick_option() -> None:
    options = ["через PipeWire", "через плеер"]
    assert pick_option("второй", options) == "через плеер"
    assert pick_option("давай через пайпвайр", options) == "давай через пайпвайр"
    assert pick_option("Через плеер.", options) == "через плеер"
    assert pick_option("1", options) == "через PipeWire"


def test_reply_to_announcement_skips_llm() -> None:
    async def scenario() -> tuple[str, list[Any]]:
        session = FakeSession()
        code = CodeTasks(CONFIG, session)
        code.start("баюн", "почини")
        await settle()
        conversation_id = code.pending[0].announcement.conversation_id
        speech = await code.run("code_answer", {"text": "второе"}, "server", conversation_id)
        await settle()
        code.cancel("")
        await settle()
        return speech, session.calls

    speech, calls = asyncio.run(scenario())
    assert speech == "Передал Клоду: через плеер."
    assert calls[0] == ["через плеер"]


def test_announcements_stream_replays_open_questions() -> None:
    code = CodeTasks(CONFIG, FakeSession())
    question = Announcement("Клод спрашивает: что выбрать?", expects_reply=True)
    code.announcements.publish(question)
    client = TestClient(create_app(code=code))
    with client.stream("GET", "/announcements") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        line = next(line for line in response.iter_lines() if line.startswith("data:"))
    event = json.loads(line.removeprefix("data:"))
    assert event["id"] == question.id
    assert event["conversation_id"] == f"code-{question.id}"
