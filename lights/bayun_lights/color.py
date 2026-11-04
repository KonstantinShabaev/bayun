"""Цвет в формате ламп Tuya (DP 24 и DP 28).

Тон 0–360, насыщенность и яркость 0–1000, каждое число — 4 hex-цифры.
DP 28 («музыка», управляющие данные) меняет цвет без записи в память лампы,
поэтому годится для потока кадров.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class HSV:
    h: float  # 0–360, по кругу
    s: float  # 0–1
    v: float  # 0–1

    def clamped(self) -> "HSV":
        return HSV(self.h % 360, min(max(self.s, 0.0), 1.0), min(max(self.v, 0.0), 1.0))


BLACK = HSV(0, 0, 0)


def _hex4(x: int) -> str:
    return f"{x:04x}"


def tuya_hsv(c: HSV) -> tuple[int, int, int]:
    c = c.clamped()
    return round(c.h) % 360, round(c.s * 1000), round(c.v * 1000)


def encode_dp24(c: HSV) -> str:
    h, s, v = tuya_hsv(c)
    return _hex4(h) + _hex4(s) + _hex4(v)


def encode_dp28(c: HSV, *, smooth: bool = False) -> str:
    """Кадр для DP 28: режим (0 — сразу, 1 — плавно), HSV, яркость и температура белого."""
    return ("1" if smooth else "0") + encode_dp24(c) + "0000" + "0000"


def decode_dp24(raw: str) -> HSV:
    if len(raw) != 12:
        raise ValueError(f"Ожидалось 12 hex-символов, получено {raw!r}")
    h, s, v = (int(raw[i : i + 4], 16) for i in (0, 4, 8))
    return HSV(h, s / 1000, v / 1000)
