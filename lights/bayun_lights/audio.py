"""Признаки звука для светомузыки без numpy: громкость и удары (onset).

Звук берём из монитора PipeWire/PulseAudio, то есть то, что ноутбук сам играет:
    parec --device=@DEFAULT_MONITOR@ --format=s16le --rate=16000 --channels=1
"""

import math
import subprocess
import sys
from array import array
from collections.abc import Iterator
from dataclasses import dataclass

RATE = 16000
CHUNK = 512  # 32 мс


@dataclass(frozen=True)
class AudioFeatures:
    level: float  # 0–1, сглаженная громкость относительно недавнего максимума
    beat: bool  # резкий рост энергии


def rms_s16le(chunk: bytes) -> float:
    samples = array("h")
    samples.frombytes(chunk[: len(chunk) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return 0.0
    return math.sqrt(sum(x * x for x in samples) / len(samples)) / 32768


class Analyzer:
    """Громкость с автоусилением и детектор ударов по скачку энергии."""

    def __init__(self, *, threshold: float = 1.6, refractory: int = 6) -> None:
        self.threshold = threshold
        self.refractory = refractory  # минимум чанков между ударами (~200 мс)
        self._avg = 0.0
        self._peak = 1e-4
        self._level = 0.0
        self._since_beat = refractory

    def feed(self, rms: float) -> AudioFeatures:
        energy = rms * rms
        if self._avg == 0.0:
            self._avg = energy  # первый чанк — точка отсчёта, а не удар
        self._peak = max(self._peak * 0.995, rms, 1e-4)
        target = rms / self._peak
        # быстрая атака, медленный спад — свет не дрожит
        k = 0.6 if target > self._level else 0.15
        self._level += (target - self._level) * k
        self._since_beat += 1
        beat = (
            self._since_beat >= self.refractory
            and energy > self._avg * self.threshold
            and rms > self._peak * 0.2
        )
        if beat:
            self._since_beat = 0
        self._avg = self._avg * 0.9 + energy * 0.1
        return AudioFeatures(level=min(self._level, 1.0), beat=beat)


def monitor_chunks(device: str = "@DEFAULT_MONITOR@") -> Iterator[bytes]:
    cmd = ["parec", f"--device={device}", "--format=s16le", f"--rate={RATE}", "--channels=1"]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        assert proc.stdout is not None
        while chunk := proc.stdout.read(CHUNK * 2):
            yield chunk
