"""Факты, таймеры и напоминания в SQLite (docs/PROJECT_SPEC.md, §4.2).

Фактов в одном доме немного, поэтому они целиком идут в системный промпт:
модель сама решает, что относится к вопросу. Поиск по смыслу (sqlite-vec) — позже.
Напоминания переживают перезапуск brain: срок хранится в базе, а не в asyncio.
"""

import asyncio
import contextlib
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .speech import clock_words, duration_words, plural, time_words

_LOGGER = logging.getLogger(__name__)

SCHEMA = Path(__file__).with_name("schema.sql")
# Сколько фактов показывать модели. Дальше — самые свежие.
PROMPT_FACTS = 60
# Будильник звенит, пока не скажут «стоп», но не больше стольких раз, с таким шагом.
ALARM_RINGS = 5
ALARM_EVERY_S = 60

PROMPT = """
У тебя есть долгая память. Когда просят запомнить что-то о доме или людях, \
вызови remember_fact и сформулируй факт коротко от третьего лица. Когда просят \
забыть — forget_fact с номером факта. Таймер или напоминание — set_reminder: \
через сколько секунд или к какому времени, будильник — kind alarm. \
Что поставлено — list_reminders, отменить — cancel_reminder."""


def _tool(
    name: str, description: str, props: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "remember_fact",
        "Запоминает надолго факт о доме или людях: «Маша спит во второй спальне».",
        {
            "text": {"type": "string", "description": "факт одной фразой"},
            "kind": {"type": "string", "enum": ["fact", "preference"]},
        },
        ["text"],
    ),
    _tool(
        "forget_fact",
        "Забывает факт по его номеру из списка того, что ты помнишь.",
        {"id": {"type": "integer"}},
        ["id"],
    ),
    _tool(
        "set_reminder",
        "Ставит таймер или напоминание. Укажи in_seconds (через сколько) или at "
        "(время ЧЧ:ММ сегодня или завтра). text — о чём напомнить, для таймера пусто.",
        {
            "text": {"type": "string"},
            "in_seconds": {"type": "integer", "minimum": 1},
            "at": {"type": "string", "description": "ЧЧ:ММ"},
            "kind": {"type": "string", "enum": ["timer", "reminder", "alarm"]},
        },
        [],
    ),
    _tool("list_reminders", "Перечисляет поставленные таймеры и напоминания.", {}, []),
    _tool(
        "cancel_reminder",
        "Отменяет таймер или напоминание по номеру; без номера — все.",
        {"id": {"type": "integer"}},
        [],
    ),
]
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


@dataclass
class Fact:
    id: int
    text: str
    kind: str


@dataclass
class Reminder:
    id: int
    kind: str
    text: str
    due_at: float
    rings: int = 0

    def speech(self, now: float | None = None) -> str:
        """Что сказать, когда срок пришёл."""
        if self.kind == "alarm":
            local = datetime.fromtimestamp(now if now is not None else self.due_at)
            wake = self.text or "пора вставать"
            return f"Подъём! Сейчас {clock_words(local.hour, local.minute)}, {wake}."
        if self.kind == "timer":
            return f"Таймер {self.text} готов." if self.text else "Время вышло, таймер сработал."
        return f"Напоминаю: {self.text}."

    def describe(self, now: float, with_id: bool = False) -> str:
        left = duration_words(max(1, round(self.due_at - now)))
        what = {"timer": f"таймер {self.text}".strip(), "alarm": "будильник"}.get(
            self.kind, self.text
        )
        return f"[{self.id}] {what}, через {left}" if with_id else f"{what}, через {left}"


class Memory:
    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time) -> None:
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._migrate()
        self._db.executescript(SCHEMA.read_text(encoding="utf-8"))
        self._clock = clock
        self._wake = asyncio.Event()

    def _migrate(self) -> None:
        """Первая версия таблицы reminders (11.11.2026) — без будильников и rings."""
        row = self._db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'reminders'"
        ).fetchone()
        if row is None or "rings" in row[0]:
            return
        _LOGGER.info("Обновляю таблицу напоминаний")
        self._db.executescript(
            "ALTER TABLE reminders RENAME TO reminders_v1;"
            + SCHEMA.read_text(encoding="utf-8")
            + "INSERT INTO reminders (id, kind, text, due_at, created_at, done) "
            "SELECT id, kind, text, due_at, created_at, done FROM reminders_v1;"
            "DROP TABLE reminders_v1;"
        )

    # ---------- факты ----------

    def remember(self, text: str, kind: str = "fact", about: str = "home") -> int:
        text = text.strip().rstrip(".")
        # lower() в SQLite не знает кириллицу: сравниваем в Python.
        for fact in self.facts(limit=1000):
            if fact.text.casefold() == text.casefold():
                return fact.id
        kind = kind if kind in ("fact", "preference") else "fact"
        cursor = self._db.execute(
            "INSERT INTO facts (kind, about, text, source) VALUES (?, ?, ?, 'voice')",
            (kind, about, text),
        )
        self._db.commit()
        return int(cursor.lastrowid or 0)

    def forget(self, fact_id: int) -> bool:
        cursor = self._db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        self._db.commit()
        return cursor.rowcount > 0

    def facts(self, limit: int = PROMPT_FACTS) -> list[Fact]:
        rows = self._db.execute(
            "SELECT id, text, kind FROM facts "
            "WHERE expires_at IS NULL OR expires_at > datetime('now') "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [Fact(*row) for row in reversed(rows)]

    def prompt(self) -> str:
        facts = self.facts()
        text = PROMPT
        if facts:
            lines = "\n".join(f"[{f.id}] {f.text}" for f in facts)
            text += f"\nЧто ты помнишь (номер в скобках — для forget_fact):\n{lines}"
        else:
            text += "\nПока ты ничего не запомнил."
        now = datetime.fromtimestamp(self._clock())
        return text + f"\nСейчас {now:%d.%m.%Y %H:%M}, {_WEEKDAYS[now.weekday()]}."

    # ---------- таймеры и напоминания ----------

    def add_reminder(self, kind: str, text: str, due_at: float) -> Reminder:
        cursor = self._db.execute(
            "INSERT INTO reminders (kind, text, due_at, created_at) VALUES (?, ?, ?, ?)",
            (kind, text.strip(), due_at, self._clock()),
        )
        self._db.commit()
        self._wake.set()
        return Reminder(int(cursor.lastrowid or 0), kind, text.strip(), due_at)

    def pending(self) -> list[Reminder]:
        rows = self._db.execute(
            "SELECT id, kind, text, due_at, rings FROM reminders WHERE done = 0 ORDER BY due_at"
        ).fetchall()
        return [Reminder(*row) for row in rows]

    def today(self) -> list[Reminder]:
        """Напоминания до конца сегодняшнего дня (для утренней сводки)."""
        now = datetime.fromtimestamp(self._clock())
        midnight = now.replace(hour=23, minute=59, second=59).timestamp()
        return [r for r in self.pending() if r.kind == "reminder" and r.due_at <= midnight]

    def day_speech(self) -> str:
        """«Сегодня: позвонить маме в четырнадцать часов.» или пусто."""
        items = []
        for r in self.today()[:4]:
            when = datetime.fromtimestamp(r.due_at)
            items.append(f"{r.text} в {time_words(when.hour, when.minute)}")
        return f"Сегодня: {', '.join(items)}." if items else ""

    def cancel(self, reminder_id: int | None = None) -> int:
        if reminder_id is None:
            cursor = self._db.execute("UPDATE reminders SET done = 1 WHERE done = 0")
        else:
            cursor = self._db.execute(
                "UPDATE reminders SET done = 1 WHERE done = 0 AND id = ?", (reminder_id,)
            )
        self._db.commit()
        return cursor.rowcount

    def due(self) -> list[Reminder]:
        """Сработавшие сейчас; помечаются выполненными, будильник — перезванивает."""
        now = self._clock()
        due = [r for r in self.pending() if r.due_at <= now]
        for reminder in due:
            if reminder.kind == "alarm":
                self._db.execute(
                    "UPDATE reminders SET rings = rings + 1, due_at = ?, "
                    "done = (rings + 1 >= ?) WHERE id = ?",
                    (now + ALARM_EVERY_S, ALARM_RINGS, reminder.id),
                )
            else:
                self._db.execute("UPDATE reminders SET done = 1 WHERE id = ?", (reminder.id,))
        if due:
            self._db.commit()
        return due

    def ringing(self) -> int:
        """Будильники, которые уже звенят и ждут «стоп»."""
        row = self._db.execute(
            "SELECT count(*) FROM reminders WHERE done = 0 AND kind = 'alarm' AND rings > 0"
        ).fetchone()
        return int(row[0])

    def ringing_ids(self) -> set[int]:
        rows = self._db.execute(
            "SELECT id FROM reminders WHERE done = 0 AND kind = 'alarm' AND rings > 0"
        ).fetchall()
        return {int(row[0]) for row in rows}

    def stop_alarms(self, ringing_only: bool = False) -> int:
        """Выключает звенящие будильники или (ringing_only=False) все поставленные."""
        cursor = self._db.execute(
            "UPDATE reminders SET done = 1 WHERE done = 0 AND kind = 'alarm'"
            + (" AND rings > 0" if ringing_only else "")
        )
        self._db.commit()
        return cursor.rowcount

    async def run_reminders(
        self, announce: Callable[[Reminder], Awaitable[None]], tick_s: float = 1.0
    ) -> None:
        """Фоновая задача brain: произносит напоминания, когда приходит срок.

        Пропущенные за время простоя (brain лежал) говорятся сразу после старта.
        """
        while True:
            try:
                for reminder in self.due():
                    _LOGGER.info("Сработало напоминание %s: %s", reminder.id, reminder.text)
                    await announce(reminder)
            except Exception:
                # Одна ошибка не должна навсегда выключить напоминания.
                _LOGGER.exception("Напоминания: ошибка")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), tick_s)

    # ---------- инструменты для LLM ----------

    def run(self, name: str, args: dict[str, Any]) -> str:
        """Выполняет инструмент. Итог — короткая фраза, которую можно сразу сказать."""
        now = self._clock()
        if name == "remember_fact":
            text = str(args.get("text") or "").strip()
            if not text:
                return "Не понял, что запомнить."
            self.remember(text, str(args.get("kind") or "fact"))
            return "Запомнил."
        if name == "forget_fact":
            return "Забыл." if self.forget(int(args.get("id") or 0)) else "Такого не помню."
        if name == "set_reminder":
            kind = args.get("kind") if args.get("kind") == "alarm" else None
            return self.set_reminder(
                str(args.get("text") or ""), args.get("in_seconds"), args.get("at"), kind
            )
        if name == "list_reminders":
            pending = self.pending()
            if not pending:
                return "Ничего не поставлено."
            # Модели — с номерами для cancel_reminder, вслух — без них.
            with_id = bool(args.get("with_id"))
            text = "; ".join(r.describe(now, with_id) for r in pending) + "."
            return text[0].upper() + text[1:]
        if name == "cancel_reminder":
            raw = args.get("id")
            count = self.cancel(int(raw) if raw else None)
            if not count:
                return "Отменять нечего."
            if count == 1:
                return "Отменил."
            return f"Отменил {count} {plural(count, 'штуку', 'штуки', 'штук')}."
        return "Нет такого инструмента."

    def set_reminder(
        self, text: str, in_seconds: Any = None, at: Any = None, kind: str | None = None
    ) -> str:
        now = self._clock()
        if in_seconds:
            seconds = int(in_seconds)
            due = now + seconds
        elif at:
            at_time = _next_time(str(at), now)
            if at_time is None:
                return "Не понял, к какому времени."
            due, seconds = at_time, round(at_time - now)
        else:
            return "Не понял, когда напомнить."
        kind = kind or ("reminder" if text.strip() else "timer")
        self.add_reminder(kind, text, due)
        verb = "Разбужу" if kind == "alarm" else "Напомню"
        if at and not in_seconds:
            when = datetime.fromtimestamp(due)
            return f"{verb} в {time_words(when.hour, when.minute)}."
        span = duration_words(seconds)
        if kind == "alarm":
            return f"Разбужу через {span}."
        return f"Напомню через {span}." if kind == "reminder" else f"Таймер на {span}."


_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def _next_time(at: str, now: float) -> float | None:
    """«07:30» → ближайшие 07:30 по местному времени (сегодня или завтра)."""
    try:
        hours, minutes = (int(p) for p in at.replace(".", ":").split(":")[:2])
        base = datetime.fromtimestamp(now).replace(
            hour=hours, minute=minutes, second=0, microsecond=0
        )
    except ValueError:
        return None
    due = base.timestamp()
    return due if due > now else due + 24 * 3600
