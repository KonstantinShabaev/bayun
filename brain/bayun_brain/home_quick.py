"""Быстрые команды свету без LLM: «включи свет», «выключи лампы за диваном», «свет на 30»,
«включи сзади лампы белым ярким светом», «сделай свет красным», «потеплее».

Самые частые команды дома не должны зависеть от облака и стоить 2–5 с.
"""

import re
from dataclasses import dataclass
from typing import Any

from .intents import normalize

# Что называют светом: «свет», «лампы», «лампу», «лампочки», «подсветку».
_NOUN = r"(?:свет(?!омузык)\w*|ламп\w*|подсветк\w*)"
_VERB = r"(?P<verb>включи|выключи|зажги|погаси|потуши|сделай|поставь|переключи)"

# Цвет по-русски (любая форма: «красный», «красным», «красную») → CSS-имя для HA.
_COLORS = {
    "красн": "red", "оранж": "orange", "желт": "yellow", "зелен": "green",
    "голуб": "deepskyblue", "син": "blue", "фиолет": "purple", "розов": "hotpink",
    "бирюз": "turquoise", "малин": "crimson", "сирен": "violet",
}  # fmt: skip
# Белый и оттенки — теплотой, а не цветом: так лампы светят ровнее.
_TEMPS = {"бел": 4500, "холодн": 6500, "дневн": 5000, "тепл": 2700, "желтоват": 2700,
          "нейтральн": 4000}  # fmt: skip
_BRIGHT = {"ярк": 100, "ярч": 100, "поярч": 100, "максимал": 100, "полн": 100,
           "тускл": 20, "темн": 30, "потемн": 30, "приглуш": 30, "слаб": 30,
           "ночн": 5, "минимал": 5}  # fmt: skip
_WARMER = ("потепл", "теплее")
_COLDER = ("похолодн", "холоднее")

# Места, которых нет в именах ламп: «сзади», «тут» — значит, весь свет.
_ANYWHERE = {"сзади", "позади", "тут", "здесь", "везде", "все", "весь", "всю", "всем",
             "доме", "комнате", "зале", "мне", "нам", "свет", "светом"}  # fmt: skip
_SKIP = {"в", "на", "у", "за", "над", "во", "и", "с", "со", "цвет", "цвета", "цветом",
         "процентов", "процента", "процент", "яркость", "яркостью", "темно", "светло"}  # fmt: skip


@dataclass
class LightCommand:
    action: str  # turn_on / turn_off
    where: str = ""
    brightness_pct: int | None = None
    color_name: str | None = None
    color_temp_kelvin: int | None = None

    def calls(self, lights: list[tuple[str, str]], groups: dict[str, tuple[str, ...]] | None = None
              ) -> list[dict[str, Any]]:  # fmt: skip
        """Вызовы control_device для ламп (entity_id, имя), которые подходят под «где».

        `groups` — группа → её лампы: если выбрана группа, её лампы отдельно не дёргаем
        (иначе Tuya получает одну команду дважды и лампы мигают вразнобой).
        """
        chosen = _filter(lights, self.where)
        members = {m for g, ms in (groups or {}).items() if g in dict(chosen) for m in ms}
        chosen = [(e, n) for e, n in chosen if e not in members]
        args: dict[str, Any] = {"action": self.action}
        if self.brightness_pct is not None:
            args["brightness_pct"] = max(1, min(100, self.brightness_pct))
        if self.color_name is not None:
            args["color_name"] = self.color_name
        if self.color_temp_kelvin is not None:
            args["color_temp_kelvin"] = self.color_temp_kelvin
        return [{"entity_id": entity_id, **args} for entity_id, _ in chosen]

    @property
    def speech(self) -> str:
        if self.action == "turn_off":
            return "Выключаю."
        if self.color_name or self.color_temp_kelvin or self.brightness_pct is not None:
            return "Готово."
        return "Включаю."


def _stem(word: str) -> str:
    # Четыре буквы: «кухне» и «Кухня», «диваном» и «диван» совпадают.
    return word[:4]


def _filter(lights: list[tuple[str, str]], where: str) -> list[tuple[str, str]]:
    words = [_stem(w) for w in where.split() if w not in _SKIP and w not in _ANYWHERE]
    if not words:
        return lights
    return [
        (entity_id, name)
        for entity_id, name in lights
        if all(any(_stem(n) == w for n in normalize(name).split()) for w in words)
    ]


def _lookup(word: str, table: dict[str, Any]) -> Any:
    return next((v for k, v in table.items() if word.startswith(k)), None)


def _modifiers(words: list[str]) -> tuple[dict[str, Any], list[str]]:
    """Вытаскивает цвет, теплоту и яркость; остальное — «где»."""
    found: dict[str, Any] = {}
    rest: list[str] = []
    for word in words:
        if word.isdigit() and int(word) <= 100:
            found["brightness_pct"] = int(word)
        elif word.startswith(_WARMER):
            found["color_temp_kelvin"] = 2700
        elif word.startswith(_COLDER):
            found["color_temp_kelvin"] = 6500
        elif (temp := _lookup(word, _TEMPS)) is not None and not (
            # «тёмно-синий» не про теплоту, «жёлтый» — цвет, а «желтоватый» — теплота
            word.startswith("желт") and not word.startswith("желтоват")
        ):
            found["color_temp_kelvin"] = temp
        elif (color := _lookup(word, _COLORS)) is not None:
            found["color_name"] = color
        elif (level := _lookup(word, _BRIGHT)) is not None and word not in ("темно",):
            found["brightness_pct"] = level
        else:
            rest.append(word)
    if "color_name" in found:
        found.pop("color_temp_kelvin", None)
    return found, rest


_PHRASE = re.compile(rf"^{_VERB}?\s*(?P<body>.*)$")


def match(text: str) -> LightCommand | None:
    phrase = normalize(text)
    words = phrase.split()
    if not words or len(words) > 9:
        return None
    if not any(re.fullmatch(_NOUN, w) for w in words) and not (
        # «ярче», «сделай потеплее», «приглуши» — тоже про свет, если больше ничего нет.
        len(words) <= 2
        and _modifiers(words[-1:])[0]
        and words[0]
        in (
            "ярче",
            "поярче",
            "темнее",
            "потемнее",
            "приглуши",
            "сделай",
            "потеплее",
            "похолоднее",
        )
    ):
        return None
    m = _PHRASE.match(phrase)
    assert m is not None
    verb = m.group("verb") or ""
    body = [w for w in m.group("body").split() if not re.fullmatch(_NOUN, w)]
    found, rest = _modifiers(body)
    if verb in ("выключи", "погаси", "потуши"):
        return LightCommand("turn_off", " ".join(rest))
    if not verb and not found:
        # «свет за диваном» без глагола — непонятно, что делать.
        return None
    # Что-то лишнее и непохожее на место («включи свет и музыку») — пусть решает модель.
    if len(rest) > 3:
        return None
    return LightCommand("turn_on", " ".join(rest), **found)
