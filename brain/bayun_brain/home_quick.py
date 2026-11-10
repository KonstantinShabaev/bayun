"""Быстрые команды свету без LLM: «включи свет», «выключи свет за диваном», «свет на 30».

Самые частые команды дома не должны зависеть от облака и стоить 2–5 с.
"""

import re
from dataclasses import dataclass
from typing import Any

from .intents import normalize

_LIGHT = re.compile(
    r"^(?P<verb>включи|выключи|зажги|погаси|потуши)\s+(?:весь\s+|везде\s+)?свет"
    r"(?:\s+(?:везде|во\s+всем\s+доме|везде\s+в\s+доме))?(?P<where>(?:\s+\S+){0,3})$"
)
_BRIGHTNESS = re.compile(
    r"^(?:сделай\s+|поставь\s+)?(?:свет|яркость)(?:\s+(?:на|в))?\s+(?P<n>\d{1,3})(?:\s+процент\w*)?$"
)
_LEVEL = {"ярче": 100, "поярче": 100, "темнее": 30, "потемнее": 30, "приглуши свет": 30}


@dataclass
class LightCommand:
    action: str  # turn_on / turn_off
    where: str = ""
    brightness_pct: int | None = None

    def calls(self, lights: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """Вызовы control_device для ламп (entity_id, имя), которые подходят под «где»."""
        chosen = _filter(lights, self.where)
        args: dict[str, Any] = {"action": self.action}
        if self.brightness_pct is not None:
            args["brightness_pct"] = max(1, min(100, self.brightness_pct))
        return [{"entity_id": entity_id, **args} for entity_id, _ in chosen]


def _stem(word: str) -> str:
    # Четыре буквы: «кухне» и «Кухня», «диваном» и «диван» совпадают.
    return word[:4]


def _filter(lights: list[tuple[str, str]], where: str) -> list[tuple[str, str]]:
    words = [_stem(w) for w in where.split() if w not in ("в", "на", "у", "за", "над", "в")]
    if not words:
        return lights
    picked = [
        (entity_id, name)
        for entity_id, name in lights
        if all(any(_stem(n) == w for n in normalize(name).split()) for w in words)
    ]
    return picked


def match(text: str) -> LightCommand | None:
    phrase = normalize(text)
    if m := _LIGHT.match(phrase):
        on = m.group("verb") in ("включи", "зажги")
        return LightCommand("turn_on" if on else "turn_off", m.group("where").strip())
    if m := _BRIGHTNESS.match(phrase):
        return LightCommand("turn_on", brightness_pct=int(m.group("n")))
    if phrase in _LEVEL:
        return LightCommand("turn_on", brightness_pct=_LEVEL[phrase])
    return None
