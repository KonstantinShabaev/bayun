"""Движки распознавания.

`dummy` — заглушка, чтобы сервер запускался без моделей.
`whisper-<размер>` — faster-whisper на CPU (extra `whisper`), точка отсчёта.
`onnx-<модель>` — модели onnx-asr без torch (extra `onnx`), например `onnx-gigaam-v3-e2e-ctc`:
GigaAM v3 с пунктуацией и цифрами, ~0,25 с на фразу на i7-1185G7 против ~1,5 с у Whisper small.
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


class OnnxAsr:
    """onnx-asr: GigaAM, T-one, Parakeet в ONNX. Модель скачивается с Hugging Face."""

    def __init__(self, model: str) -> None:
        import onnx_asr

        self.name = model
        self._model: Any = onnx_asr.load_model(model)

    def transcribe(self, pcm: bytes, rate: int) -> str:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return str(self._model.recognize(audio, sample_rate=rate)).strip()


def load_engine(name: str) -> AsrEngine:
    if name == "dummy":
        return DummyAsr()
    if name.startswith("whisper-"):
        return FasterWhisperAsr(name.removeprefix("whisper-"))
    if name.startswith("onnx-"):
        return OnnxAsr(name.removeprefix("onnx-"))
    raise ValueError(
        f"Неизвестный движок ASR: {name!r}. Доступно: dummy, whisper-<размер>, onnx-<модель>"
    )
