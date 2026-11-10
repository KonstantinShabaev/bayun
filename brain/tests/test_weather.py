import httpx
from bayun_brain.weather import Weather, degrees, kind, speech

DATA = {
    "current": {"temperature_2m": 2.3, "apparent_temperature": -1.1, "weather_code": 3},
    "daily": {
        "temperature_2m_max": [3.2, 1.9],
        "temperature_2m_min": [1.1, -0.6],
        "weather_code": [71, 77],
        "precipitation_probability_max": [5, 85],
    },
}


def test_degrees() -> None:
    assert degrees(2.3) == "плюс два градуса"
    assert degrees(-1.6) == "минус два градуса"
    assert degrees(0.3) == "ноль градусов"
    assert degrees(21) == "плюс двадцать один градус"


def test_kind() -> None:
    assert kind("Волтэр, какая погода?") == "now"
    assert kind("погода на завтра") == "tomorrow"
    assert kind("нужен ли зонт") == "umbrella"
    assert kind("брать ли зонт завтра") == "umbrella_tomorrow"
    assert kind("какая погода в Сочи") is None
    assert kind("Волтэр, доброе утро!") == "morning"
    assert speech("morning", DATA).startswith("Доброе утро! Сейчас плюс два градуса")


def test_speech() -> None:
    assert speech("now", DATA) == (
        "Сейчас плюс два градуса, пасмурно, ощущается как минус один градус. Днём плюс три градуса."
    )
    assert speech("tomorrow", DATA) == (
        "Завтра днём плюс два градуса, ночью минус один градус, снежная крупа."
    )
    assert speech("umbrella", DATA) == "Не нужен, сегодня осадков почти не будет."
    assert speech("umbrella_tomorrow", DATA).startswith("Возьми, завтра вероятность")


async def test_answer_and_failure() -> None:
    ok = Weather(
        55.75, 37.62, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=DATA))
    )
    assert (await ok.answer("что на улице")).startswith("Сейчас плюс два")  # type: ignore[union-attr]
    assert await ok.answer("расскажи анекдот") is None
    down = Weather(55.75, 37.62, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert await down.answer("какая погода") is None
