"""Детектор речи и конец фразы.

`Endpointer` — чистая логика по вероятности речи в кадре, её легко тестировать.
Вероятность даёт Silero VAD из openWakeWord, без него — порог по громкости.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import numpy as np

from .audio import FRAME_S, Frame

# Сумма длительностей кадров копит ошибку округления: 10 × 0,08 < 0,8.
_EPS = 1e-6


class Verdict(Enum):
    LISTENING = "listening"
    DONE = "done"  # фраза закончилась
    NOTHING = "nothing"  # так и не заговорили


@dataclass
class Endpointer:
    start_timeout_s: float
    silence_s: float
    max_s: float
    threshold: float = 0.5
    elapsed_s: float = field(default=0.0, init=False)
    speech_s: float = field(default=0.0, init=False)
    silence_run_s: float = field(default=0.0, init=False)

    @property
    def heard_speech(self) -> bool:
        # Одиночный щелчок не считаем речью.
        return self.speech_s >= 0.2 - _EPS

    def push(self, speech_prob: float) -> Verdict:
        self.elapsed_s += FRAME_S
        if speech_prob >= self.threshold:
            self.speech_s += FRAME_S
            self.silence_run_s = 0.0
        else:
            self.silence_run_s += FRAME_S

        if not self.heard_speech:
            if self.elapsed_s >= self.start_timeout_s - _EPS:
                return Verdict.NOTHING
            return Verdict.LISTENING
        if self.silence_run_s >= self.silence_s - _EPS or self.elapsed_s >= self.max_s - _EPS:
            return Verdict.DONE
        return Verdict.LISTENING


class SpeechDetector(Protocol):
    name: str

    def __call__(self, frame: Frame) -> float: ...

    def reset(self) -> None: ...


class EnergyVad:
    """Запасной вариант: громкость относительно плавающего уровня шума."""

    name = "energy"

    def __init__(self, ratio: float = 3.0) -> None:
        self.ratio = ratio
        self.noise = 200.0

    def __call__(self, frame: Frame) -> float:
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        loud = rms > self.noise * self.ratio
        if not loud:
            self.noise = 0.95 * self.noise + 0.05 * max(rms, 50.0)
        return 1.0 if loud else 0.0

    def reset(self) -> None:
        pass


class SileroVad:
    """Silero VAD в ONNX, который openWakeWord уже скачал вместе со своими моделями."""

    name = "silero"

    def __init__(self) -> None:
        from openwakeword.vad import VAD

        self._vad: Any = VAD()

    def __call__(self, frame: Frame) -> float:
        return float(self._vad.predict(frame))

    def reset(self) -> None:
        self._vad.reset_states()


def load_vad() -> SpeechDetector:
    try:
        return SileroVad()
    except Exception:
        return EnergyVad()
