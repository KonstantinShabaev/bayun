import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from bayun_brain.api import create_app
from bayun_brain.config import Config
from bayun_brain.ha import HomeAssistant
from bayun_brain.llm import Completion, Message, ToolCall
from fastapi.testclient import TestClient

STATES = [
    {"entity_id": "light.kitchen", "state": "off", "attributes": {"friendly_name": "Кухня"}},
    {"entity_id": "switch.dnd", "state": "on", "attributes": {"friendly_name": "Не беспокоить"}},
    {
        "entity_id": "input_select.sofa_effect",
        "state": "выкл",
        "attributes": {"friendly_name": "Эффект за диваном", "options": ["выкл", "светомузыка"]},
    },
]


def fake_ha(calls: list[tuple[str, dict[str, Any]]]) -> HomeAssistant:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/states":
            return httpx.Response(200, json=STATES)
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=[])

    return HomeAssistant("http://ha", "token", transport=httpx.MockTransport(handler))


class ToolChat:
    def __init__(self, call: dict[str, Any]) -> None:
        self.call = call
        self.seen: list[list[Message]] = []

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        yield "не должно понадобиться"

    async def complete(self, messages: list[Message], tools: list[dict[str, Any]]) -> Completion:
        self.seen.append(messages)
        if messages[-1]["role"] == "user":
            return Completion("", [ToolCall("c1", "control_device", json.dumps(self.call))])
        return Completion("Включил свет на кухне.")


def test_light_command_calls_home_assistant() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    chat = ToolChat({"entity_id": "light.kitchen", "action": "turn_on", "brightness_pct": 40})
    client = TestClient(create_app(Config(), chat, fake_ha(calls)))
    speech = client.post("/converse", json={"text": "включи свет на кухне"}).json()["speech"]

    assert speech == "Включил свет на кухне."
    assert calls == [
        ("/api/services/light/turn_on", {"entity_id": "light.kitchen", "brightness_pct": 40})
    ]
    system = chat.seen[0][0]["content"]
    assert "light.kitchen" in system and "switch.dnd" not in system
    assert chat.seen[1][-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "Готово: Кухня, turn_on.",
    }


def test_unknown_entity_is_refused() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    chat = ToolChat({"entity_id": "lock.front_door", "action": "turn_off"})
    TestClient(create_app(Config(), chat, fake_ha(calls))).post(
        "/converse", json={"text": "открой"}
    )
    assert calls == []
    assert chat.seen[1][-1]["content"] == "Нет такого устройства."


def test_effect_option_is_selected() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    chat = ToolChat(
        {
            "entity_id": "input_select.sofa_effect",
            "action": "select_option",
            "option": "светомузыка",
        }
    )
    TestClient(create_app(Config(), chat, fake_ha(calls))).post(
        "/converse", json={"text": "включи светомузыку"}
    )
    assert calls == [
        (
            "/api/services/input_select/select_option",
            {"entity_id": "input_select.sofa_effect", "option": "светомузыка"},
        )
    ]
    assert "варианты: выкл, светомузыка" in chat.seen[0][0]["content"]


def test_unknown_option_is_refused() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    chat = ToolChat(
        {"entity_id": "input_select.sofa_effect", "action": "select_option", "option": "диско"}
    )
    TestClient(create_app(Config(), chat, fake_ha(calls))).post("/converse", json={"text": "диско"})
    assert calls == []
