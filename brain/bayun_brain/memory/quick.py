"""Быстрые фразы без LLM: время, дата, таймеры, напоминания, «запомни».

Работают и без облака: если VPN лёг, Баюн всё равно скажет время и засечёт таймер.
"""

import re
from datetime import datetime

from ..intents import normalize
from .speech import clock_words
from .store import Memory

_WORD_NUMBERS = {
    "одну": 1, "одна": 1, "один": 1, "две": 2, "два": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
    "двенадцать": 12, "пятнадцать": 15, "двадцать": 20, "тридцать": 30, "сорок": 40,
    "сорок пять": 45, "пятьдесят": 50, "девяносто": 90, "полторы": 1.5, "полтора": 1.5,
}  # fmt: skip
_UNIT_SECONDS = {"сек": 1, "мин": 60, "час": 3600}

_NUM = r"\d+(?:[.,]\d+)?|" + "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True))
_SPAN = re.compile(
    rf"\b(?:(?P<n>{_NUM})\s+)?(?P<u>секунд[уыа]?|сек|минут[уыа]?|мин|час(?:а|ов)?)\b"
    r"|\b(?P<half>полчаса|полминуты)\b"
)

_TIME = {"который час", "сколько времени", "сколько сейчас времени", "который сейчас час",
         "время", "скажи время", "подскажи время"}  # fmt: skip
_DATE = {"какое сегодня число", "какой сегодня день", "какая сегодня дата", "какое число",
         "какой день недели", "какой сегодня день недели"}  # fmt: skip
_LIST = {"сколько осталось", "сколько осталось на таймере", "сколько там осталось",
         "какие таймеры", "какие напоминания", "что с таймером",
         "какие у меня напоминания"}  # fmt: skip
_CANCEL = re.compile(
    r"^(?:отмени|сбрось|выключи|убери|удали|останови)\s+(?:все\s+)?(?:таймер\w*|напоминани\w*)$"
)
_ALARM_AT = re.compile(
    r"^(?:разбуди(?:\s+меня)?(?:\s+завтра)?|(?:поставь\s+|заведи\s+)?будильник)"
    r"\s+(?:на|в)\s+(?P<h>\d{1,2})(?:\s+(?P<m>\d{2}))?"
    r"(?:\s+(?:час\w*|утра|дня|вечера|ночи|минут\w*)){0,3}$"
)
_ALARM_IN = re.compile(r"^разбуди(?:\s+меня)?\s+через\s+(?P<span>.+)$")
# Пока будильник звенит, «стоп» — ему, а не видео.
_ALARM_STOP = {"стоп", "хватит", "встаю", "я встал", "я встала", "проснулся", "проснулась",
               "выключи будильник", "отмени будильник", "стоп будильник", "все встаю",
               "замолчи", "отстань", "ладно", "хорошо", "спасибо"}  # fmt: skip
_ALARM_CANCEL = re.compile(r"^(?:отмени|выключи|убери|удали)\s+(?:все\s+)?будильник\w*$")
_FACTS = {"что ты помнишь", "что ты запомнил", "что ты знаешь", "что ты обо мне знаешь",
          "что ты знаешь обо мне", "что ты знаешь о доме"}  # fmt: skip

_HELP = {"что ты умеешь", "что ты можешь", "что умеешь", "помощь", "как тобой пользоваться"}
HELP_SPEECH = (
    "Включаю и выключаю свет, ставлю видео и фильмы на телевизоре, ставлю таймеры "
    "и напоминания, запоминаю, что скажешь, ищу ответы в интернете и просто болтаю. "
    "Зови по имени и говори сразу, что нужно."
)

_TIMER = re.compile(
    r"^(?:поставь\s+|заведи\s+|включи\s+|засеки\s+)?(?:таймер\s+)?(?:на\s+)?(?P<span>.+)$"
)
_REMIND_IN = re.compile(r"^напомни(?:\s+мне)?\s+через\s+(?P<rest>.+)$")
_ABOUT = re.compile(r"^(?:о\s+том\s+что|что|чтобы|про|о)\s+")
_REMIND_AT = re.compile(
    r"^напомни(?:\s+мне)?\s+(?:завтра\s+)?в\s+(?P<h>\d{1,2})(?:\s+(?P<m>\d{2}))?"
    r"(?:\s+(?:час\w*|утра|дня|вечера|ночи|минут\w*)){0,3}"
    r"\s+(?:о\s+том\s+что\s+|что\s+|чтобы\s+|про\s+|о\s+)?(?P<text>.+)$"
)
# Не меньше трёх слов: «запомни меня» — не факт, пусть разбирается модель.
_REMEMBER = re.compile(r"^запомни(?:\s+пожалуйста)?\s+(?:что\s+)?(?P<text>\S+(?:\s+\S+){2,})$")

_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
           "сентября", "октября", "ноября", "декабря"]  # fmt: skip


def parse_span(text: str) -> int | None:
    """«5 минут», «полтора часа», «час 30 минут» → секунды. Лишние слова — None."""
    total = 0.0
    rest = text
    for m in _SPAN.finditer(text):
        rest = rest.replace(m.group(0), " ", 1)
        if m.group("half"):
            total += 1800 if m.group("half") == "полчаса" else 30
            continue
        raw = m.group("n")
        n = 1.0 if raw is None else float(_WORD_NUMBERS.get(raw) or raw.replace(",", "."))
        total += n * _UNIT_SECONDS[m.group("u")[:3]]
    if total <= 0 or rest.replace("и", " ").strip():
        return None
    return int(total)


def _hour(h: int, text: str) -> int:
    if re.search(r"\b(вечера|дня)\b", text) and h < 12:
        return h + 12
    return h


def answer(text: str, memory: Memory, now: float) -> str | None:
    """Ответ без LLM или None, если фраза не про это."""
    phrase = normalize(text)
    if not phrase or len(phrase.split()) > 16:
        return None
    local = datetime.fromtimestamp(now)
    if phrase in _TIME:
        return f"Сейчас {clock_words(local.hour, local.minute)}."
    if phrase in _DATE:
        return f"Сегодня {_WEEKDAYS[local.weekday()]}, {local.day} {_MONTHS[local.month - 1]}."
    if phrase in _ALARM_STOP and memory.ringing():
        memory.stop_alarms(ringing_only=True)
        return "Хорошего дня!"
    if _ALARM_CANCEL.match(phrase):
        return "Будильник отменил." if memory.stop_alarms() else "Будильников нет."
    if m := _ALARM_AT.match(phrase):
        hours = _hour(int(m.group("h")), phrase)
        minutes = int(m.group("m") or 0)
        if hours < 24 and minutes < 60:
            return memory.set_reminder("", at=f"{hours}:{minutes:02d}", kind="alarm")
    if (m := _ALARM_IN.match(phrase)) and (seconds := parse_span(m.group("span"))):
        return memory.set_reminder("", in_seconds=seconds, kind="alarm")
    if phrase in _HELP:
        return HELP_SPEECH
    if phrase in _LIST:
        return memory.run("list_reminders", {})
    if _CANCEL.match(phrase):
        return memory.run("cancel_reminder", {})
    if phrase in _FACTS:
        facts = memory.facts()
        if not facts:
            return "Пока ничего. Скажи «запомни», и я запомню."
        return "Помню вот что: " + "; ".join(f.text for f in facts[-10:]) + "."
    if m := _REMIND_AT.match(phrase):
        hours = _hour(int(m.group("h")), phrase)
        minutes = int(m.group("m") or 0)
        if hours < 24 and minutes < 60:
            return memory.set_reminder(m.group("text"), at=f"{hours}:{minutes:02d}")
    if m := _REMIND_IN.match(phrase):
        # Самый длинный срок в начале: «через час 30 минут позвонить маме».
        words = m.group("rest").split()
        for cut in range(len(words) - 1, 0, -1):
            if seconds := parse_span(" ".join(words[:cut])):
                what = _ABOUT.sub("", " ".join(words[cut:]))
                return memory.set_reminder(what, in_seconds=seconds)
    timer = "таймер" in phrase or phrase.startswith("засеки")
    if timer and (m := _TIMER.match(phrase)) and (seconds := parse_span(m.group("span"))):
        return memory.set_reminder("", in_seconds=seconds)
    if m := _REMEMBER.match(phrase):
        return memory.run("remember_fact", {"text": m.group("text")})
    return None
