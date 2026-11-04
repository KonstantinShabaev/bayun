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

    chat = OneToolChat("play_video", {"query": "лофи"})
    video = Video(command, [*command, "--home"], search)
    client = TestClient(create_app(Config(), chat, None, video))
    client.post("/converse", json={"text": "включи лофи"})

    assert log.read_text() == "https://www.youtube.com/watch?v=abc\n"
    assert chat.seen[1][-1]["content"] == "Включено: Лофи радио."


def test_stop_video_returns_home(tmp_path: Path) -> None:
    command, log = recorder(tmp_path)
    chat = OneToolChat("stop_video", {})
    video = Video(command, [*command, "--home"])
    TestClient(create_app(Config(), chat, None, video)).post("/converse", json={"text": "хватит"})
    assert log.read_text() == "--home\n"


def test_failing_tv_is_reported() -> None:
    async def search(query: str) -> Found | None:
        return Found("https://youtu.be/x", "x")

    chat = OneToolChat("play_video", {"query": "x"})
    video = Video([sys.executable, "-c", "raise SystemExit(1)"], [], search)
    TestClient(create_app(Config(), chat, None, video)).post("/converse", json={"text": "x"})
    assert chat.seen[1][-1]["content"] == "Телевизор не отозвался."
