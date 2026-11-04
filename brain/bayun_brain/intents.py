"""Быстрые команды телевизору без LLM: пауза, перемотка, громкость, «включи видео …».

Такие фразы частые и однозначные, а круг через LLM с инструментом стоит 2–5 с.
Всё, что не узнано уверенно, уходит в LLM как обычно.
"""

import re
from dataclasses import dataclass
from typing import Any

# Имя впереди фразы STT пишет по-разному: «Джарвис», «Джарс», «Джарль».
_NAME = re.compile(r"^(джар\w*|jarvis|волтер|уолтер|баюн)[,.!\s]*", re.IGNORECASE)
_FILLER = re.compile(r"\b(пожалуйста|давай|ну|а|э+)\b", re.IGNORECASE)

_NUMBERS = {
    "одну": 1, "один": 1, "одно": 1, "две": 2, "два": 2, "три": 3, "четыре": 4,
    "пять": 5, "десять": 10, "пятнадцать": 15, "двадцать": 20, "тридцать": 30,
    "сорок": 40, "пятьдесят": 50, "полминуты": 30, "пол": 0.5,
}  # fmt: skip


@dataclass
class Intent:
    tool: str
    args: dict[str, Any]


def _normalize(text: str) -> str:
    text = _NAME.sub("", text.strip().lower().replace("ё", "е"))
    text = _FILLER.sub(" ", text)
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _number(word: str | None, default: float) -> float:
    if not word:
        return default
    if word.replace(".", "", 1).isdigit():
        return float(word)
    return float(_NUMBERS.get(word, default))


_SEEK = re.compile(
    r"^(?:перемотай|промотай|мотай|отмотай|перемотка)?\s*(?:на\s+)?"
    r"(?:(?P<n1>\S+)\s+(?P<u1>секунд\w*|минут\w*|сек|мин)\s+)?"
    r"(?P<dir>вперед|назад)"
    r"(?:\s+на\s+(?P<n2>\S+)(?:\s+(?P<u2>секунд\w*|минут\w*|сек|мин))?)?$"
)
_VOLUME = re.compile(
    r"^(?:сделай\s+)?(?:(?:телевизор|звук)\s+)?(?P<dir>громче|тише|погромче|потише)"
    r"(?:\s+на\s+(?P<n>\S+)(?:\s+делени\w*)?)?$"
)
_PLAY = re.compile(
    r"^(?:включи|поставь|найди|покажи|запусти)\s+(?:мне\s+)?(?:на\s+(?:телевизоре|ютубе)\s+)?"
    r"(?:видео|ролик|видос|фильм|клип|мультик|ютуб)\w*\s+(?:про\s+|о\s+|с\s+)?(?P<q>.+)$"
)

_FIXED: dict[str, Intent] = {}
for _words, _intent in [
    (("пауза", "на паузу", "поставь на паузу", "поставь паузу", "стоп", "останови",
      "останови видео", "пауза видео"), Intent("control_video", {"action": "pause"})),
    (("продолжи", "продолжай", "играй", "воспроизведи", "сними с паузы", "дальше смотреть",
      "плей", "включи обратно"), Intent("control_video", {"action": "play"})),
    (("следующее", "следующее видео", "следующий", "следующий ролик", "следующая серия",
      "дальше", "другое видео"), Intent("control_video", {"action": "next"})),
    (("выключи видео", "закрой видео", "убери видео", "хватит видео", "выключи ютуб"),
     Intent("stop_video", {})),
    (("выключи звук", "без звука", "замолчи телевизор", "мьют", "включи звук телевизора"),
     Intent("set_volume", {"direction": "mute"})),
    (("включи звук в видео", "включи звук видео", "включи звук в ролике", "звук в видео"),
     Intent("control_video", {"action": "volume", "level_pct": 100})),
]:  # fmt: skip
    for _w in _words:
        _FIXED[_w] = _intent


def match(text: str) -> Intent | None:
    phrase = _normalize(text)
    if not phrase or len(phrase.split()) > 10:
        return None
    if phrase in _FIXED:
        return _FIXED[phrase]
    if m := _SEEK.match(phrase):
        number = m.group("n1") or m.group("n2")
        unit = m.group("u1") or m.group("u2") or "сек"
        seconds = _number(number, 1 if unit.startswith("мин") and number else 30)
        if unit.startswith("мин"):
            seconds *= 60
        sign = -1 if m.group("dir") == "назад" else 1
        return Intent("control_video", {"action": "seek", "seconds": sign * seconds})
    if m := _VOLUME.match(phrase):
        steps = int(_number(m.group("n"), 3))
        direction = "up" if "громч" in m.group("dir") else "down"
        return Intent("set_volume", {"direction": direction, "steps": steps})
    if m := _PLAY.match(phrase):
        return Intent("play_video", {"query": m.group("q")})
    return None
