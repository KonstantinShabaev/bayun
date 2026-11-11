import json
from typing import Any

import httpx
from bayun_brain.api import create_app
from bayun_brain.config import Config
from bayun_brain.llm import Completion, Message, ToolCall
from bayun_brain.video import Video
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


def fake_agent(calls: list[tuple[str, dict[str, Any]]], status: int = 200) -> Video:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        calls.append((request.url.path, body))
        if status != 200:
            return httpx.Response(status, json={"error": "no video on the page"})
        if request.url.path.endswith("/search"):
            results = [{"n": 1, "title": "Космос 4K"}, {"n": 2, "title": "Луна"}]
            return httpx.Response(200, json={"results": results})
        if request.url.path == "/results/select":
            n = 2 if body.get("move") == 1 else 1
            return httpx.Response(200, json={"selected": n, "title": ["Космос 4K", "Луна"][n - 1]})
        if request.url.path in ("/results/confirm", "/vk/play", "/youtube/play"):
            return httpx.Response(200, json={"title": "Луна"})
        if request.url.path == "/player" and body.get("action") == "quality":
            return httpx.Response(200, json={"quality": "hd1080"})
        return httpx.Response(200, json={"ok": True, "ms": 3})

    return Video("http://agent", transport=httpx.MockTransport(handler))


def ask(text: str, video: Video, chat: Any = None) -> str:
    client = TestClient(create_app(Config(), chat, None, video))
    return str(client.post("/converse", json={"text": text}).json()["speech"])


def test_play_video_plays_first_result() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    chat = OneToolChat("play_video", {"query": "космос"})
    assert ask("хочу посмотреть что-нибудь про космос", fake_agent(calls), chat) == (
        "Включаю: Космос 4K."
    )
    assert calls == [("/youtube/search", {"q": "космос", "n": 8}), ("/youtube/play", {"n": 1})]
    assert len(chat.seen) == 1


def test_vk_search_then_play_by_number() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    video = fake_agent(calls)
    chat = OneToolChat("search_video", {"query": "рыбалка", "source": "vk"})
    speech = ask("найди в вк рыбалку", video, chat)
    assert speech == "Нашёл 2. Первый: Космос 4K. Включить или следующий?"
    assert ask("Включи второй", video) == "Включаю: Луна."
    assert calls[-1] == ("/vk/play", {"n": 2})


def test_next_and_confirm_while_choosing() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    video = fake_agent(calls)
    ask("включи фильм про луну", video)
    # «следующий» при карточках — следующий вариант, а не следующее видео YouTube
    assert ask("следующий", video) == "Второй: Луна."
    assert calls[-1] == ("/results/select", {"move": 1})
    assert ask("включай", video) == "Включаю: Луна."
    assert calls[-1] == ("/results/confirm", {})
    # карточек больше нет: «следующий» снова про плеер
    ask("следующий", video)
    assert calls[-1] == ("/player", {"action": "next"})


def test_quality_by_voice() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    assert ask("качество 4к", fake_agent(calls)) == "4К нет, поставил 1080."
    assert calls == [("/player", {"action": "quality", "value": 2160})]


def test_agent_errors_are_spoken_in_russian() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    speech = ask("номер три", fake_agent(calls, status=404))
    assert speech.isascii() is False and "search" not in speech


def test_fast_pause_skips_llm() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    chat = OneToolChat("never", {})
    assert ask("Джарвис, пауза.", fake_agent(calls), chat) == "Пауза."
    assert chat.seen == []
    assert calls == [("/player", {"action": "pause"})]


def test_seek_and_tv_volume() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    video = fake_agent(calls)
    ask("назад на 30 секунд", video)
    ask("сделай громче на два", video)
    assert calls == [
        ("/player", {"action": "seek", "value": -30.0}),
        ("/tv/volume", {"dir": "up", "steps": 2}),
    ]


def test_no_video_is_reported() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    assert ask("пауза", fake_agent(calls, status=404)) == "Видео сейчас не играет."


class SearchChat(OneToolChat):
    async def search(self, question: str) -> str:
        return f"Ответ на «{question}»."


def test_web_search_answer_is_spoken_directly() -> None:
    chat = SearchChat("web_search", {"question": "погода в Москве"})
    client = TestClient(create_app(Config(), chat))
    speech = client.post("/converse", json={"text": "какая погода"}).json()["speech"]
    assert speech == "Ответ на «погода в Москве»."
    assert len(chat.seen) == 1
