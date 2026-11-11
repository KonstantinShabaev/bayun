import asyncio
from datetime import datetime

import pytest
from bayun_brain.home_quick import match as light_match
from bayun_brain.memory import Memory, quick
from bayun_brain.memory.speech import clock_words, duration_words
from bayun_brain.memory.store import Reminder

# Среда, 11 ноября 2026, 02:30 по местному времени.
NOW = datetime(2026, 11, 11, 2, 30).timestamp()


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def memory(clock: Clock) -> Memory:
    return Memory(":memory:", clock=clock)


def ask(memory: Memory, text: str) -> str | None:
    return quick.answer(text, memory, NOW)


def test_duration_words() -> None:
    assert duration_words(60) == "минуту"
    assert duration_words(300) == "пять минут"
    assert duration_words(21 * 60) == "двадцать одну минуту"
    assert duration_words(90) == "одну минуту тридцать секунд"
    assert duration_words(3600) == "час"
    assert duration_words(5400) == "один час тридцать минут"
    assert duration_words(2 * 3600) == "два часа"
    assert duration_words(45) == "сорок пять секунд"


def test_clock_words() -> None:
    assert clock_words(2, 30) == "два часа тридцать минут"
    assert clock_words(21, 1) == "двадцать один час одна минута"
    assert clock_words(0, 5) == "ноль часов пять минут"
    assert clock_words(1, 0) == "один час"


def test_time_and_date_without_llm(memory: Memory) -> None:
    assert ask(memory, "Волтэр, который час?") == "Сейчас два часа тридцать минут."
    assert ask(memory, "какое сегодня число") == "Сегодня среда, 11 ноября."


def test_timer(memory: Memory, clock: Clock) -> None:
    assert ask(memory, "Поставь таймер на 5 минут.") == "Таймер на пять минут."
    assert ask(memory, "таймер на полчаса") == "Таймер на тридцать минут."
    assert ask(memory, "засеки полторы минуты") == "Таймер на одну минуту тридцать секунд."
    assert ask(memory, "сколько осталось") == (
        "Таймер, через одну минуту тридцать секунд; таймер, через пять минут; "
        "таймер, через тридцать минут."
    )
    clock.now += 301
    due = memory.due()
    assert [r.speech() for r in due] == ["Время вышло, таймер сработал."] * 2
    assert memory.due() == []
    assert ask(memory, "отмени таймер") == "Отменил."
    assert ask(memory, "отмени таймер") == "Отменять нечего."


def test_reminders(memory: Memory) -> None:
    assert ask(memory, "напомни через 10 минут выключить плиту") == "Напомню через десять минут."
    assert ask(memory, "Напомни мне в 7:30 позвонить маме.") == "Напомню в семь тридцать."
    assert ask(memory, "напомни в 8 вечера что надо вынести мусор") == "Напомню в двадцать часов."
    texts = [r.text for r in memory.pending()]
    assert texts == ["выключить плиту", "позвонить маме", "надо вынести мусор"]
    assert memory.pending()[0].speech() == "Напоминаю: выключить плиту."


def test_reminder_survives_restart(tmp_path, clock: Clock) -> None:
    db = tmp_path / "bayun.db"
    Memory(db, clock=clock).set_reminder("проверить духовку", in_seconds=60)
    clock.now += 120
    assert [r.text for r in Memory(db, clock=clock).due()] == ["проверить духовку"]


def test_help(memory: Memory) -> None:
    assert ask(memory, "Волтэр, что ты умеешь?") == quick.HELP_SPEECH


def test_name_spellings(memory: Memory) -> None:
    for name in ["Уалтер", "Wалтер", "алтер", "Валтер", "Уолтер", "Walter", "Джарвис"]:
        assert ask(memory, f"{name}, что ты умеешь?") == quick.HELP_SPEECH, name


def test_not_quick(memory: Memory) -> None:
    for text in ["расскажи анекдот", "какая погода", "таймер", "включи свет", "напомни"]:
        assert ask(memory, text) is None, text


def test_facts(memory: Memory) -> None:
    assert ask(memory, "что ты помнишь") == "Пока ничего. Скажи «запомни», и я запомню."
    assert ask(memory, "Запомни, что ключи от дачи лежат в синей коробке.") == "Запомнил."
    assert memory.run("remember_fact", {"text": "Маша не ест мясо"}) == "Запомнил."
    assert memory.run("remember_fact", {"text": "маша не ест мясо."}) == "Запомнил."
    assert [f.text for f in memory.facts()] == [
        "ключи от дачи лежат в синей коробке",
        "Маша не ест мясо",
    ]
    prompt = memory.prompt()
    assert "[2] Маша не ест мясо" in prompt
    assert "11.11.2026 02:30, среда" in prompt
    assert memory.run("forget_fact", {"id": 2}) == "Забыл."
    assert memory.run("forget_fact", {"id": 2}) == "Такого не помню."


def test_llm_tools(memory: Memory) -> None:
    assert memory.run("set_reminder", {"in_seconds": 120}) == "Таймер на две минуты."
    assert (
        memory.run("set_reminder", {"text": "купить хлеб", "at": "9:00"})
        == "Напомню в девять часов."
    )
    listed = memory.run("list_reminders", {"with_id": True})
    assert listed.startswith("[1] таймер, через две минуты; [2] купить хлеб, через")
    assert memory.run("cancel_reminder", {"id": 2}) == "Отменил."
    assert memory.run("cancel_reminder", {}) == "Отменил."
    assert memory.run("set_reminder", {"text": "x"}) == "Не понял, когда напомнить."


async def test_run_reminders_announces(memory: Memory, clock: Clock) -> None:
    said: list[str] = []
    memory.add_reminder("reminder", "полить цветы", NOW + 1)

    async def announce(reminder: Reminder) -> None:
        said.append(reminder.speech())

    task = asyncio.create_task(memory.run_reminders(announce, tick_s=0.01))
    await asyncio.sleep(0.03)
    assert said == []
    clock.now += 2
    await asyncio.sleep(0.05)
    task.cancel()
    assert said == ["Напоминаю: полить цветы."]


def test_reminder_describe() -> None:
    reminder = Reminder(3, "timer", "", NOW + 65)
    assert reminder.describe(NOW, with_id=True) == "[3] таймер, через одну минуту пять секунд"


def test_light_quick() -> None:
    lights = [("light.lampa_w5k", "Лампа W5K"), ("light.svet_za_divanom", "Свет за диваном")]
    on = light_match("Волтэр, включи свет")
    assert on is not None and [c["entity_id"] for c in on.calls(lights)] == [
        "light.lampa_w5k",
        "light.svet_za_divanom",
    ]
    off = light_match("выключи свет за диваном")
    assert off is not None
    assert off.calls(lights) == [{"entity_id": "light.svet_za_divanom", "action": "turn_off"}]
    dim = light_match("свет на 30 процентов")
    assert dim is not None and dim.calls(lights)[0]["brightness_pct"] == 30
    assert light_match("выключи свет на кухне").calls(lights) == []  # type: ignore[union-attr]
    assert light_match("включи видео про котиков") is None


def test_api_quick_answers_without_llm(tmp_path) -> None:
    from bayun_brain.api import create_app
    from fastapi.testclient import TestClient

    app = create_app(memory=Memory(tmp_path / "bayun.db"))
    with TestClient(app) as client:
        reply = client.post("/converse", json={"text": "поставь таймер на 2 минуты"}).json()
        assert reply["speech"] == "Таймер на две минуты."
        reply = client.post("/converse", json={"text": "расскажи анекдот"}).json()
        assert reply["speech"] == "я пока учусь и ещё ничего не умею."


def test_outage_answers_at_once_after_failure() -> None:
    from bayun_brain.api import OFFLINE_SPEECH, create_app
    from fastapi.testclient import TestClient

    class Down:
        calls = 0

        async def stream(self, messages):  # type: ignore[no-untyped-def]
            Down.calls += 1
            raise ConnectionError("VPN лежит")
            yield ""

        async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            raise AssertionError

    client = TestClient(create_app(chat=Down()))  # type: ignore[arg-type]
    for _ in range(3):
        assert (
            client.post("/converse", json={"text": "как дела"}).json()["speech"] == OFFLINE_SPEECH
        )
    assert Down.calls == 1
    assert client.get("/health").json()["llm"] == "down"


def test_review_regressions(memory: Memory) -> None:
    assert ask(memory, "напомни в 2 часа дня покормить кота") == "Напомню в четырнадцать часов."
    assert ask(memory, "напомни через час часы перевести") == "Напомню через час."
    assert sorted(r.text for r in memory.pending()) == ["покормить кота", "часы перевести"]
    assert ask(memory, "запомни меня") is None
    assert ask(memory, "полтергейст что ты умеешь") is None


def test_announcements_kept_until_voice_connects() -> None:
    from bayun_brain.code import Announcement, Announcements

    hub = Announcements()
    hub.publish(Announcement("Напоминаю: полить цветы."))
    queue = hub.listen()
    assert queue.get_nowait()["text"] == "Напоминаю: полить цветы."
    assert hub.listen().empty()


def test_tool_error_is_not_outage() -> None:
    from bayun_brain.api import is_outage

    assert is_outage(ConnectionError())
    assert is_outage(type("APIConnectionError", (Exception,), {})())
    assert not is_outage(ValueError("bad args"))
    assert not is_outage(type("BadRequestError", (Exception,), {})())


def test_light_where_cases() -> None:
    lights = [("light.kitchen", "Кухня"), ("light.sofa", "Свет за диваном")]
    cmd = light_match("включи свет на кухне")
    assert cmd is not None and [c["entity_id"] for c in cmd.calls(lights)] == ["light.kitchen"]


def test_alarm(memory: Memory, clock: Clock) -> None:
    from bayun_brain.memory.speech import time_words

    assert time_words(7, 5) == "семь ноль пять"
    assert time_words(1, 0) == "один час"
    assert ask(memory, "Волтэр, разбуди меня в 7 утра") == "Разбужу в семь часов."
    assert ask(memory, "поставь будильник на 6 45") == "Разбужу в шесть сорок пять."
    assert ask(memory, "стоп") is None  # ещё не звенит — пусть идёт дальше (видео)
    clock.now = datetime(2026, 11, 11, 6, 45).timestamp()
    rang = memory.due()
    assert [r.speech(clock.now) for r in rang] == [
        "Подъём! Сейчас шесть часов сорок пять минут, пора вставать."
    ]
    assert memory.ringing() == 1
    clock.now += 61
    assert len(memory.due()) == 1  # перезванивает
    assert ask(memory, "я встал") == "Хорошего дня!"
    assert memory.ringing() == 0
    assert [r.kind for r in memory.pending()] == ["alarm"]  # на 7:00 остался
    assert ask(memory, "отмени будильник") == "Будильник отменил."


def test_alarm_gives_up(memory: Memory, clock: Clock) -> None:
    memory.set_reminder("", in_seconds=1, kind="alarm")
    rings = 0
    for _ in range(10):
        clock.now += 61
        rings += len(memory.due())
    assert rings == 5
    assert memory.pending() == []


def test_migrates_first_reminders_table(tmp_path) -> None:
    import sqlite3

    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.executescript(
        "CREATE TABLE reminders (id INTEGER PRIMARY KEY, "
        "kind TEXT NOT NULL CHECK (kind IN ('timer', 'reminder')), text TEXT NOT NULL, "
        "due_at REAL NOT NULL, created_at REAL NOT NULL, done INTEGER NOT NULL DEFAULT 0);"
        "INSERT INTO reminders VALUES (1, 'reminder', 'полить цветы', 10, 1, 0);"
    )
    old.commit()
    old.close()
    memory = Memory(db, clock=Clock())
    assert [r.text for r in memory.pending()] == ["полить цветы"]
    memory.set_reminder("", in_seconds=60, kind="alarm")


def test_dashboard_api(tmp_path) -> None:
    from bayun_brain.api import create_app
    from fastapi.testclient import TestClient

    memory = Memory(tmp_path / "bayun.db")
    memory.set_reminder("полить цветы", in_seconds=600)
    memory.remember("ключи в синей коробке")
    client = TestClient(create_app(memory=memory))
    board = client.get("/api/dashboard").json()
    assert [r["text"] for r in board["reminders"]] == ["полить цветы"]
    assert board["facts"] == [{"id": 1, "text": "ключи в синей коробке"}]
    assert board["weather"] is None
    rid = board["reminders"][0]["id"]
    assert client.delete(f"/api/reminders/{rid}").json() == {"deleted": 1}
    assert client.delete("/api/facts/1").json() == {"deleted": 1}
    assert client.get("/api/dashboard").json()["reminders"] == []


def test_day_speech(memory: Memory) -> None:
    assert memory.day_speech() == ""
    memory.set_reminder("позвонить маме", at="14:00")
    memory.set_reminder("", in_seconds=60)
    assert memory.day_speech() == "Сегодня: позвонить маме в четырнадцать часов."


def test_good_morning_briefing(tmp_path) -> None:
    from bayun_brain.api import create_app
    from fastapi.testclient import TestClient

    memory = Memory(tmp_path / "bayun.db")
    memory.set_reminder("полить цветы", in_seconds=60)
    client = TestClient(create_app(memory=memory))
    speech = client.post("/converse", json={"text": "Доброе утро"}).json()["speech"]
    assert speech.startswith("Доброе утро! Сегодня: полить цветы в")
