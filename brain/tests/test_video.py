import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from bayun_brain.api import create_app
from bayun_brain.config import Config
from bayun_brain.llm import Completion, Message, ToolCall
from bayun_brain.video import Found, Video
from fastapi.testclient import TestClient


class OneToolChat:
    def __init__(self, name: str, args: dict[str, Any]) -> None:
        self.name = name
        self.args = args
        self.seen: list[list[Message]] = []

    async def stream(self, messages: list[Message]):  # type: ignore[no-untyped-def]
        yield "не должно понадобиться"

    async def complete(self, messages: list[Message], tools: list[dict[str, Any]]) -> Completion:
        self.seen.append(messages)
        if messages[-1]["role"] == "user":
            return Completion("", [ToolCall("c1", self.name, json.dumps(self.args))])
        return Completion("Включил.")


def recorder(tmp_path: Path) -> tuple[list[str], Path]:
    log = tmp_path / "opened"
    script = f"open({str(log)!r}, 'a').write(' '.join(__import__('sys').argv[1:]) + '\\n')"
    return [sys.executable, "-c", script], log


def test_play_video_opens_first_result(tmp_path: Path) -> None:
    command, log = recorder(tmp_path)

    async def search(query: str) -> Found | None:
        assert query == "лофи"
        return Found("https://www.youtube.com/watch?v=abc", "Лофи радио")

    async def scenario() -> str:
        video = Video(command, [], search)
        said = await video.run("play_video", {"query": "лофи"})
        await asyncio.gather(*video._background)
        return said

    assert asyncio.run(scenario()) == "Включаю: Лофи радио."
    assert log.read_text() == "https://www.youtube.com/watch?v=abc\n"


def test_fast_command_skips_llm(tmp_path: Path) -> None:
    command, log = recorder(tmp_path)
    chat = OneToolChat("never", {})
    video = Video(command, [], player_command=command)
    client = TestClient(create_app(Config(), chat, None, video))
    speech = client.post("/converse", json={"text": "Джарвис, пауза."}).json()["speech"]
    assert speech == "Пауза."
    assert chat.seen == []
    assert log.read_text() == "--pause\n"


def test_stop_video_returns_home(tmp_path: Path) -> None:
    command, log = recorder(tmp_path)
    chat = OneToolChat("stop_video", {})
    video = Video(command, [*command, "--home"])
    TestClient(create_app(Config(), chat, None, video)).post("/converse", json={"text": "хватит"})
    assert log.read_text() == "--home\n"


def test_failing_tv_is_reported() -> None:
    chat = OneToolChat("stop_video", {})
    video = Video([], [sys.executable, "-c", "raise SystemExit(1)"])
    client = TestClient(create_app(Config(), chat, None, video))
    speech = client.post("/converse", json={"text": "x"}).json()["speech"]
    assert speech == "Телевизор не отозвался."


def test_volume_up_presses_tv_keys(tmp_path: Path) -> None:
    command, log = recorder(tmp_path)
    chat = OneToolChat("set_volume", {"direction": "up", "steps": 2})
    video = Video(command, [], volume_command=command)
    TestClient(create_app(Config(), chat, None, video)).post(
        "/converse", json={"text": "чуть громче бы"}
    )
    assert log.read_text() == "KEYCODE_VOLUME_UP KEYCODE_VOLUME_UP\n"


def test_seek_back(tmp_path: Path) -> None:
    command, log = recorder(tmp_path)
    chat = OneToolChat("control_video", {"action": "seek", "seconds": -30})
    video = Video(command, [], player_command=command)
    TestClient(create_app(Config(), chat, None, video)).post("/converse", json={"text": "назад"})
    assert log.read_text() == "--seek -30\n"


class SearchChat(OneToolChat):
    async def search(self, question: str) -> str:
        return f"Ответ на «{question}»."


def test_web_search_answer_is_spoken_directly() -> None:
    chat = SearchChat("web_search", {"question": "погода в Москве"})
    client = TestClient(create_app(Config(), chat))
    speech = client.post("/converse", json={"text": "какая погода"}).json()["speech"]
    assert speech == "Ответ на «погода в Москве»."
    assert len(chat.seen) == 1
