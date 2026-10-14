"""Движки распознавания.

Пока здесь только интерфейс и заглушка. GigaAM и faster-whisper подключаются в этапе M1.
"""

from typing import Protocol


class AsrEngine(Protocol):
    """Распознаёт целую фразу: PCM 16 бит, моно, `rate` Гц."""

    name: str

    def transcribe(self, pcm: bytes, rate: int) -> str: ...


class DummyAsr:
    """Возвращает пустой текст. Нужна, чтобы сервер запускался без моделей."""

    name = "dummy"

    def transcribe(self, pcm: bytes, rate: int) -> str:
        return ""


def load_engine(name: str) -> AsrEngine:
    if name == "dummy":
        return DummyAsr()
    raise ValueError(f"Неизвестный движок ASR: {name!r}. Доступно: dummy")
