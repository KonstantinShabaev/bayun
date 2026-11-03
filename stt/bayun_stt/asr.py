"""Движки распознавания.

`dummy` — заглушка, чтобы сервер запускался без моделей.
`whisper-<размер>` — faster-whisper на CPU (extra `whisper`), точка отсчёта до GigaAM.
GigaAM подключается в этапе M1.
"""

from typing import Any, Protocol

import numpy as np


class AsrEngine(Protocol):
    """Распознаёт целую фразу: PCM 16 бит, моно, `rate` Гц."""

    name: str

    def transcribe(self, pcm: bytes, rate: int) -> str: ...


class DummyAsr:
    """Возвращает пустой текст. Нужна, чтобы сервер запускался без моделей."""

    name = "dummy"

    def transcribe(self, pcm: bytes, rate: int) -> str:
        return ""


class FasterWhisperAsr:
    """faster-whisper, int8 на CPU. Модель скачивается при первом запуске."""

    def __init__(self, size: str = "small", threads: int = 4) -> None:
        from faster_whisper import WhisperModel

        self.name = f"whisper-{size}"
        self._model: Any = WhisperModel(
            size, device="cpu", compute_type="int8", cpu_threads=threads
        )

    def transcribe(self, pcm: bytes, rate: int) -> str:
        if rate != 16000:
            raise ValueError("faster-whisper ждёт 16 кГц")
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(
            audio,
            language="ru",
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()


def load_engine(name: str) -> AsrEngine:
    if name == "dummy":
        return DummyAsr()
    if name.startswith("whisper-"):
        return FasterWhisperAsr(name.removeprefix("whisper-"))
    raise ValueError(f"Неизвестный движок ASR: {name!r}. Доступно: dummy, whisper-<размер>")
