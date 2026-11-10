"""Погода без LLM: Open-Meteo (без ключа), ответ за полсекунды вместо поиска через модель.

«Какая погода», «что на улице», «погода на завтра», «нужен ли зонт».
Другой город или сложный вопрос — в LLM с поиском, как раньше.
"""

import logging
import re
from typing import Any

import httpx

from .intents import normalize
from .memory.speech import number_words, plural

_LOGGER = logging.getLogger(__name__)

URL = "https://api.open-meteo.com/v1/forecast"

_NOW = {"какая погода", "какая сейчас погода", "какая погода сейчас", "что на улице",
        "что с погодой", "погода", "сколько градусов", "сколько градусов на улице",
        "какая погода на улице", "холодно на улице", "тепло на улице",
        "какая там погода"}  # fmt: skip
_TOMORROW = {"какая погода завтра", "погода на завтра", "какая завтра погода",
             "что с погодой завтра", "какая будет погода завтра",
             "что завтра с погодой"}  # fmt: skip
_UMBRELLA = re.compile(r"^(?:нужен ли|брать ли|взять ли)\s+(?:мне\s+)?зонт(?:ик)?(?:\s+сегодня)?$")
_UMBRELLA_TOMORROW = re.compile(r"^(?:нужен ли|брать ли|взять ли)\s+(?:мне\s+)?зонт\w*\s+завтра$")

# WMO weather codes → как сказать.
_CODES = {
    0: "ясно", 1: "почти ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "туман с изморозью", 51: "морось", 53: "морось", 55: "сильная морось",
    56: "ледяная морось", 57: "ледяная морось", 61: "небольшой дождь", 63: "дождь",
    65: "сильный дождь", 66: "ледяной дождь", 67: "ледяной дождь", 71: "небольшой снег",
    73: "снег", 75: "сильный снег", 77: "снежная крупа", 80: "ливень", 81: "ливень",
    82: "сильный ливень", 85: "снегопад", 86: "сильный снегопад", 95: "гроза",
    96: "гроза с градом", 99: "гроза с градом",
}  # fmt: skip


def degrees(value: float) -> str:
    """-1.6 → «минус два градуса», 0.3 → «ноль градусов»."""
    n = round(value)
    sign = "минус " if n < 0 else "плюс " if n > 0 else ""
    return f"{sign}{number_words(abs(n))} {plural(abs(n), 'градус', 'градуса', 'градусов')}"


def _sky(code: Any) -> str:
    return _CODES.get(int(code or 0), "")


_MORNING = {"доброе утро", "утро доброе", "привет доброе утро", "с добрым утром"}


def kind(text: str) -> str | None:
    """now / tomorrow / umbrella / umbrella_tomorrow / morning или None."""
    phrase = normalize(text)
    if phrase in _MORNING:
        return "morning"
    if phrase in _NOW:
        return "now"
    if phrase in _TOMORROW:
        return "tomorrow"
    if _UMBRELLA_TOMORROW.match(phrase):
        return "umbrella_tomorrow"
    if _UMBRELLA.match(phrase):
        return "umbrella"
    return None


def speech(what: str, data: dict[str, Any]) -> str:
    current, daily = data.get("current", {}), data.get("daily", {})
    day = 1 if what.endswith("tomorrow") else 0
    high, low = daily["temperature_2m_max"][day], daily["temperature_2m_min"][day]
    rain = int(daily.get("precipitation_probability_max", [0, 0])[day] or 0)
    sky = _sky(daily["weather_code"][day])
    if what.startswith("umbrella"):
        when = "завтра" if day else "сегодня"
        if rain >= 50:
            return f"Возьми, {when} вероятность осадков {number_words(rain)} процентов."
        if rain >= 25:
            return f"На всякий случай возьми, {when} осадки возможны."
        return f"Не нужен, {when} осадков почти не будет."
    if what == "morning":
        return "Доброе утро! " + speech("now", data)
    if day:
        # Без «от … до …»: им нужен родительный падеж числительных.
        text = f"Завтра днём {degrees(high)}, ночью {degrees(low)}"
        return f"{text}, {sky}." if sky else f"{text}."
    text = f"Сейчас {degrees(current['temperature_2m'])}"
    if sky := _sky(current.get("weather_code")):
        text += f", {sky}"
    feels = current.get("apparent_temperature")
    if feels is not None and abs(round(feels) - round(current["temperature_2m"])) >= 3:
        text += f", ощущается как {degrees(feels)}"
    text += f". Днём {degrees(high)}"
    if rain >= 50:
        text += ", возможны осадки"
    return text + "."


class Weather:
    def __init__(
        self,
        latitude: float,
        longitude: float,
        timezone: str = "auto",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._params: dict[str, str | float] = {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,"
            "precipitation_probability_max",
            "wind_speed_unit": "ms",
            "timezone": timezone,
            "forecast_days": 2,
        }
        self._client = httpx.AsyncClient(timeout=4, transport=transport)

    async def answer(self, text: str) -> str | None:
        """Ответ про погоду или None (не про погоду, или сервис не ответил — пусть LLM)."""
        what = kind(text)
        if what is None:
            return None
        try:
            response = await self._client.get(URL, params=self._params)
            response.raise_for_status()
            return speech(what, response.json())
        except Exception as error:
            _LOGGER.warning("Open-Meteo не ответил: %s", error)
            return None
