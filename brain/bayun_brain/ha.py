"""Home Assistant: список устройств и один инструмент для управления ими.

Пока REST API HA, без MCP: Баюн видит свет, пылесосы, плееры и списки режимов
(эффекты, сцены), остальное скрыто.
Действия сверяются со списком устройств, чужие entity_id и сервисы не пройдут.
"""

import json
from dataclasses import dataclass
from typing import Any

import httpx

DOMAINS = ("light", "vacuum", "media_player", "input_select", "select")

# Действие инструмента → (домен, сервис HA).
ACTIONS: dict[str, dict[str, str]] = {
    "light": {"turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle"},
    "vacuum": {
        "start": "start",
        "stop": "stop",
        "pause": "pause",
        "return_to_base": "return_to_base",
    },
    "media_player": {
        "turn_on": "turn_on",
        "turn_off": "turn_off",
        "play": "media_play",
        "pause": "media_pause",
    },
    "input_select": {"select_option": "select_option"},
    "select": {"select_option": "select_option"},
}

TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "control_device",
        "description": (
            "Управляет устройством дома. Для света: turn_on (можно с яркостью, "
            "цветом или теплотой), turn_off, toggle. Для пылесоса: start, stop, "
            "pause, return_to_base. Для плеера: turn_on, turn_off, play, pause. "
            "Для списка режимов (эффект, сцена): select_option с option из его вариантов."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "id из списка устройств"},
                "action": {"type": "string"},
                "brightness_pct": {"type": "integer", "minimum": 1, "maximum": 100},
                "color_name": {"type": "string", "description": "CSS-цвет по-английски: red"},
                "color_temp_kelvin": {"type": "integer", "minimum": 2000, "maximum": 6500},
                "option": {"type": "string", "description": "вариант режима, ровно как в списке"},
            },
            "required": ["entity_id", "action"],
        },
    },
}


@dataclass
class Device:
    entity_id: str
    name: str
    state: str
    options: tuple[str, ...] = ()
    # Группа ламп в HA: её лампы (attributes.entity_id).
    members: tuple[str, ...] = ()

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]


class HomeAssistant:
    def __init__(self, url: str, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
            transport=transport,
        )

    async def devices(self) -> list[Device]:
        response = await self._client.get("/api/states")
        response.raise_for_status()
        return [
            Device(
                s["entity_id"],
                s["attributes"].get("friendly_name", s["entity_id"]),
                s["state"],
                tuple(s["attributes"].get("options") or ()),
                tuple(s["attributes"].get("entity_id") or ()),
            )
            for s in response.json()
            if s["entity_id"].split(".", 1)[0] in DOMAINS and s["state"] != "unavailable"
        ]

    async def control(self, devices: list[Device], args: dict[str, Any]) -> str:
        """Выполняет вызов инструмента, возвращает короткий итог для модели."""
        device = next((d for d in devices if d.entity_id == args.get("entity_id")), None)
        if device is None:
            return "Нет такого устройства."
        service = ACTIONS.get(device.domain, {}).get(str(args.get("action")))
        if service is None:
            return f"Устройство {device.name} так не умеет."
        data: dict[str, Any] = {"entity_id": device.entity_id}
        if device.domain == "light" and service == "turn_on":
            for key in ("brightness_pct", "color_name", "color_temp_kelvin"):
                if args.get(key) is not None:
                    data[key] = args[key]
        if service == "select_option":
            if args.get("option") not in device.options:
                return f"У {device.name} нет режима {args.get('option')}."
            data["option"] = args["option"]
        response = await self._client.post(f"/api/services/{device.domain}/{service}", json=data)
        if response.is_error:
            return f"Не получилось: HA ответил {response.status_code}."
        return f"Готово: {device.name}, {args['action']}."


def devices_prompt(devices: list[Device]) -> str:
    if not devices:
        return "\nУстройств в доме пока не подключено."
    lines = "\n".join(
        f"- {d.name} ({d.entity_id}): {d.state}"
        + (f"; варианты: {', '.join(d.options)}" if d.options else "")
        + (f"; группа из {', '.join(d.members)}" if d.members else "")
        for d in devices
    )
    return (
        "\nТы управляешь домом инструментом control_device. Устройства сейчас:\n"
        f"{lines}\n"
        "Если просят сделать с устройством что-то, вызови инструмент, а потом коротко "
        "скажи, что сделал. Про «весь свет» — вызови для каждой лампы, а если есть "
        "группа — только для группы. Белый — color_temp_kelvin 4500, тёплый — 2700, "
        "холодный — 6500, цвет — color_name. Пустых вызовов без entity_id не делай."
    )


def parse_args(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
