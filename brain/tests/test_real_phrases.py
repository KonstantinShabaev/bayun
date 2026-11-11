"""Фразы из журнала 11.11.2026: что должно ловиться без LLM и как себя вести."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from bayun_brain import home_quick, intents
from bayun_brain.api import create_app
from bayun_brain.config import Config
from bayun_brain.ha import HomeAssistant
from bayun_brain.llm import Completion, Message, ToolCall
from bayun_brain.video import Video
from fastapi.testclient import TestClient

LIGHTS = [
    ("light.svet_za_divanom", "Свет за диваном"),
    ("light.lampa_w5k", "Лампа W5K"),
    ("light.lampa_w509z1", "Лампа W509Z1"),
]
GROUPS = {"light.svet_za_divanom": ("light.lampa_w5k", "light.lampa_w509z1")}
GROUP = [{"entity_id": "light.svet_za_divanom"}]


@pytest.mark.parametrize(
    ("text", "calls"),
    [
        ("Включи сзади лампы белым ярким светом.",
         [{**GROUP[0], "action": "turn_on", "brightness_pct": 100, "color_temp_kelvin": 4500}]),
        ("Включи свет", [{**GROUP[0], "action": "turn_on"}]),
        ("выключи лампы", [{**GROUP[0], "action": "turn_off"}]),
        ("Сделай свет красным", [{**GROUP[0], "action": "turn_on", "color_name": "red"}]),
        ("свет на 50", [{**GROUP[0], "action": "turn_on", "brightness_pct": 50}]),
        ("сделай свет потеплее", [{**GROUP[0], "action": "turn_on", "color_temp_kelvin": 2700}]),
        ("ярче", [{**GROUP[0], "action": "turn_on", "brightness_pct": 100}]),
        ("включи тёплый свет за диваном",
         [{**GROUP[0], "action": "turn_on", "color_temp_kelvin": 2700}]),
        ("включи лампу W5K синим", [{"entity_id": "light.lampa_w5k", "action": "turn_on",
                                      "color_name": "blue"}]),
    ],
)  # fmt: skip
def test_light_phrases(text: str, calls: list[dict[str, Any]]) -> None:
    command = home_quick.match(text)
    assert command is not None
    assert command.calls(LIGHTS, GROUPS) == calls


@pytest.mark.parametrize(
    "text", ["включи светомузыку", "включи фильм про свет и тень в космосе", "включи свет и музыку"]
)
def test_not_light(text: str) -> None:
    command = home_quick.match(text)
    assert command is None or command.calls(LIGHTS, GROUPS) == []


@pytest.mark.parametrize(
    ("text", "tool", "args"),
    [
        ("Давай первый.", "play_result", {"n": 1}),
        ("номер три", "play_result", {"n": 3}),
        ("Поставь в ВК видео Зверополис.", "play_video", {"query": "зверополис", "source": "vk"}),
        ("Включи главный экран, блядь, идиот.", "stop_video", {}),
        ("Поставь, сука, на паузу.", "control_video", {"action": "pause"}),
    ],
)
def test_video_phrases(text: str, tool: str, args: dict[str, Any]) -> None:
    assert intents.match(text) == intents.Intent(tool, args)


def test_name_noise_goodbye() -> None:
    assert intents.only_name("Wолтер.") and intents.only_name("Волтер.")
    assert not intents.only_name("ну давай")
    assert intents.is_noise("Д") and intents.is_noise("а во")
    assert not intents.is_noise("да") and not intents.is_noise("два")
    assert intents.goodbye("Пока. Пока!") == "Пока."
    assert intents.goodbye("Спасибо.") == "Пожалуйста."


class ScriptChat:
    """Модель, которая на любую фразу снова зовёт play_result — как 11.11 на «Д»."""

    def __init__(self, reply: str = "") -> None:
        self.reply = reply
        self.calls = 0

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        yield self.reply

    async def complete(self, messages: list[Message], tools: list[dict[str, Any]]) -> Completion:
        if messages[-1]["role"] == "user":
            self.calls += 1
            if self.reply:
                return Completion(self.reply)
            return Completion("", [ToolCall("c1", "play_result", json.dumps({"n": 1}))])
        return Completion("Включаю.")


def fake_tv(posts: list[str]) -> Video:
    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request.url.path)
        return httpx.Response(200, json={"results": [{"n": 1, "title": "Зверополис"}]})

    return Video("http://tv", transport=httpx.MockTransport(handler))


def stream(client: TestClient, text: str, cid: str | None = None) -> tuple[list[str], dict]:
    body = {"text": text, "conversation_id": cid}
    lines = [json.loads(x) for x in client.post("/converse/stream", json=body).text.splitlines()]
    return [x["speech"] for x in lines if "speech" in x], lines[-1]


def test_no_follow_up_after_film_starts_and_no_replay() -> None:
    posts: list[str] = []
    chat = ScriptChat()
    client = TestClient(create_app(Config(), chat, video=fake_tv(posts)))

    said, done = stream(client, "включи фильм зверополис")
    assert said == ["Нашёл 1. Первый: Зверополис. Включить или следующий?"]
    assert done["continue_conversation"]  # ждём номер

    said, done = stream(client, "Давай первый.", done["conversation_id"])
    assert said == ["Включаю."] and not done["continue_conversation"]

    # Речь из фильма: обрывок молча отбрасываем, LLM не зовём.
    said, done = stream(client, "Д", done["conversation_id"])
    assert said == [] and not done["continue_conversation"] and chat.calls == 0

    # Даже если модель решила повторить play_result — фильм не перезапускается.
    said, done = stream(client, "Две чем сломал во?", done["conversation_id"])
    assert said == ["Уже идёт."]
    assert posts.count("/youtube/play") == 1


def test_model_can_stay_silent() -> None:
    client = TestClient(create_app(Config(), ScriptChat("-"), video=fake_tv([])))
    said, done = stream(client, "и тогда он сказал что всё кончено")
    assert said == [] and not done["continue_conversation"]


def test_name_only_and_goodbye_skip_llm() -> None:
    chat = ScriptChat("не должно понадобиться")
    client = TestClient(create_app(Config(), chat))
    said, done = stream(client, "Wолтер.")
    assert said == ["Слушаю."] and done["continue_conversation"]
    said, done = stream(client, "Пока. Пока!")
    assert said == ["Пока."] and not done["continue_conversation"]
    assert chat.calls == 0


def test_quick_light_uses_group_once() -> None:
    states = [
        {"entity_id": "light.svet_za_divanom", "state": "on", "attributes": {
            "friendly_name": "Свет за диваном",
            "entity_id": ["light.lampa_w5k", "light.lampa_w509z1"]}},
        {"entity_id": "light.lampa_w5k", "state": "on",
         "attributes": {"friendly_name": "Лампа W5K"}},
        {"entity_id": "light.lampa_w509z1", "state": "on",
         "attributes": {"friendly_name": "Лампа W509Z1"}},
    ]  # fmt: skip
    sent: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/states":
            return httpx.Response(200, json=states)
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=[])

    home = HomeAssistant("http://ha", "t", transport=httpx.MockTransport(handler))
    client = TestClient(create_app(Config(), ScriptChat("x"), home))
    said, done = stream(client, "Включи сзади лампы белым ярким светом.")
    assert said == ["Готово."] and not done["continue_conversation"]
    assert sent == [
        (
            "/api/services/light/turn_on",
            {
                "entity_id": "light.svet_za_divanom",
                "brightness_pct": 100,
                "color_temp_kelvin": 4500,
            },
        )
    ]
