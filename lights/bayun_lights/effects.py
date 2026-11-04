"""Эффекты: функция времени (и звука) → цвет каждой лампы.

Эффект не знает про сеть и лампы, поэтому его легко проверить тестом
и показать в --dry-run.
"""

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field

from bayun_lights.audio import AudioFeatures
from bayun_lights.color import HSV


class Effect:
    name = ""
    needs_audio = False

    def frame(self, t: float, n: int, audio: AudioFeatures | None) -> list[HSV]:
        raise NotImplementedError


@dataclass
class Rainbow(Effect):
    """Радуга бежит по лампам, period — секунд на полный круг."""

    name = "rainbow"
    period: float = 12.0
    brightness: float = 1.0

    def frame(self, t: float, n: int, audio: AudioFeatures | None) -> list[HSV]:
        base = 360 * t / self.period
        return [HSV(base + 360 * i / max(n, 1), 1.0, self.brightness) for i in range(n)]


@dataclass
class Breathe(Effect):
    """Дыхание одним цветом, лампы чуть вразнобой."""

    name = "breathe"
    hue: float = 30.0
    period: float = 5.0

    def frame(self, t: float, n: int, audio: AudioFeatures | None) -> list[HSV]:
        out = []
        for i in range(n):
            phase = 2 * math.pi * (t / self.period + i * 0.15)
            out.append(HSV(self.hue, 0.9, 0.08 + 0.92 * (0.5 - 0.5 * math.cos(phase))))
        return out


@dataclass
class Candle(Effect):
    """Свеча: тёплый цвет, случайное мерцание у каждой лампы своё."""

    name = "candle"
    seed: int = 0
    _rng: random.Random = field(init=False, repr=False)
    _v: list[float] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def frame(self, t: float, n: int, audio: AudioFeatures | None) -> list[HSV]:
        if len(self._v) != n:
            self._v = [0.7] * n
        out = []
        for i in range(n):
            target = self._rng.uniform(0.45, 1.0)
            self._v[i] += (target - self._v[i]) * 0.35
            out.append(HSV(28 + self._rng.uniform(-4, 4), 0.95, self._v[i]))
        return out


@dataclass
class Music(Effect):
    """Светомузыка: громкость — яркость, на удар — новый цвет, лампы по очереди."""

    name = "music"
    needs_audio = True
    hue_step: float = 47.0
    floor: float = 0.05
    _hue: float = field(default=0.0, init=False, repr=False)
    _lead: int = field(default=0, init=False, repr=False)

    def frame(self, t: float, n: int, audio: AudioFeatures | None) -> list[HSV]:
        if audio is None:
            # звука ещё нет — слабое свечение, чтобы лампа не казалась сломанной
            return [HSV(self._hue + 25 * i, 1.0, self.floor) for i in range(n)]
        if audio.beat:
            self._hue = (self._hue + self.hue_step) % 360
            self._lead = (self._lead + 1) % max(n, 1)
        v = self.floor + (1 - self.floor) * audio.level
        out = []
        for i in range(n):
            # лампа «на ударе» ярче и с основным цветом, остальные — соседние оттенки
            lead = i == self._lead
            hue = self._hue + (0 if lead else 25 * ((i - self._lead) % n))
            out.append(HSV(hue, 1.0, v if lead else v * 0.6))
        return out


EFFECTS: dict[str, Callable[[], Effect]] = {
    "rainbow": Rainbow,
    "breathe": Breathe,
    "candle": Candle,
    "music": Music,
}
