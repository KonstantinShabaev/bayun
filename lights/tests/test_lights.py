import json
import math
import struct
from pathlib import Path

import pytest
from bayun_lights import devices
from bayun_lights.audio import Analyzer, AudioFeatures, rms_s16le
from bayun_lights.color import HSV, decode_dp24, encode_dp24, encode_dp28
from bayun_lights.effects import Candle, Music, Rainbow
from bayun_lights.engine import run


def test_dp24_matches_lamp_dump() -> None:
    # W5K стояла на синем 1%: "00ef03e8000a" = тон 239, насыщенность 1000, яркость 10
    assert decode_dp24("00ef03e8000a") == HSV(239, 1.0, 0.01)
    assert encode_dp24(HSV(239, 1.0, 0.01)) == "00ef03e8000a"


def test_dp28_frame_layout() -> None:
    frame = encode_dp28(HSV(360 + 120, 2.0, -1))
    assert frame == "0" + "0078" + "03e8" + "0000" + "0000" + "0000"
    assert encode_dp28(HSV(0, 0, 1), smooth=True)[0] == "1"


def test_rainbow_spreads_lamps_around_circle() -> None:
    hues = [c.h % 360 for c in Rainbow(period=10).frame(0, 3, None)]
    assert hues == pytest.approx([0, 120, 240])


def test_candle_is_deterministic_and_warm() -> None:
    a = [Candle(seed=1).frame(0, 2, None) for _ in range(3)]
    b = [Candle(seed=1).frame(0, 2, None) for _ in range(3)]
    assert a == b
    assert all(20 <= c.h <= 36 and 0.4 <= c.v <= 1 for c in a[0])


def test_music_changes_hue_on_beat_and_follows_level() -> None:
    m = Music()
    quiet = m.frame(0, 2, AudioFeatures(level=0.0, beat=False))
    loud = m.frame(0, 2, AudioFeatures(level=1.0, beat=True))
    assert loud[1].v > quiet[0].v
    assert loud[1].h != quiet[0].h


def _tone(amp: float, n: int = 512) -> bytes:
    return b"".join(struct.pack("<h", int(amp * 32767 * math.sin(i / 5))) for i in range(n))


def test_analyzer_detects_beat_after_silence() -> None:
    an = Analyzer()
    for _ in range(20):
        assert not an.feed(rms_s16le(_tone(0.01))).beat
    hit = an.feed(rms_s16le(_tone(0.8)))
    assert hit.beat
    assert hit.level > 0.5
    assert not an.feed(rms_s16le(_tone(0.8))).beat  # пауза после удара


def test_devices_parse_wizard_list_and_dict(tmp_path: Path) -> None:
    wizard = [
        {"name": "LED BULB W5K", "id": "a1", "key": "k1", "ip": "192.168.0.55", "version": "3.5"},
        {"name": "без ip", "id": "a2", "key": "k2", "ip": ""},
    ]
    f = tmp_path / "d.json"
    f.write_text(json.dumps(wizard))
    lamps = devices.load(f)
    assert [x.name for x in lamps] == ["LED BULB W5K"]
    assert lamps[0].version == 3.5
    by_name = devices.parse({"кухня": {"device_id": "b", "local_key": "k", "host": "10.0.0.2"}})
    assert by_name[0].name == "кухня"
    assert devices.select(lamps, ["192.168.0.55"]) == lamps
    with pytest.raises(ValueError):
        devices.select(lamps, ["спальня"])


class FakeSink:
    def __init__(self) -> None:
        self.sent: list[HSV] = []
        self.started = self.stopped = False

    def start(self) -> None:
        self.started = True

    def send(self, color: HSV, *, smooth: bool = False) -> None:
        self.sent.append(color)
        self.smooth = smooth

    def stop(self) -> None:
        self.stopped = True


def test_engine_skips_unchanged_frames_and_stops() -> None:
    t = [0.0]

    def clock() -> float:
        return t[0]

    def sleep(dt: float) -> None:
        t[0] += 0.1

    sinks = [FakeSink(), FakeSink()]
    sent = run(Rainbow(period=1e9), sinks, fps=10, duration=1.0, clock=clock, sleep=sleep)
    # радуга почти стоит на месте: каждой лампе ушёл один кадр
    assert sent == 2
    assert all(s.started and s.stopped and len(s.sent) == 1 for s in sinks)


def test_breathe_eases_low_end() -> None:
    from bayun_lights.effects import Breathe

    b = Breathe(period=8)
    lows = [b.frame(t, 1, None)[0].v for t in (0.0, 0.4, 0.8)]
    # у дна яркость растёт медленно, без скачка
    assert lows[0] == pytest.approx(0.05)
    assert lows[1] - lows[0] < 0.01
    assert lows[2] - lows[1] < 0.05


def test_engine_uses_effect_smooth_mode() -> None:
    t = [0.0]
    sink = FakeSink()
    run(
        Rainbow(),
        [sink],
        fps=10,
        duration=0.3,
        clock=lambda: t[0],
        sleep=lambda dt: t.__setitem__(0, t[0] + 0.1),
    )
    assert sink.smooth is True


def test_restore_colour_skips_white_dps() -> None:
    from bayun_lights.transport import restore_steps

    saved = {"20": True, "21": "colour", "22": 10, "23": 1000, "24": "000003e803e8"}
    steps = restore_steps(saved)
    assert all("22" not in s and "23" not in s for s in steps)
    assert steps[-1] == {"21": "colour", "24": "000003e803e8"}
    white = restore_steps({"20": True, "21": "white", "22": 1000, "23": 500, "24": "x"})
    assert "24" not in white[-1] and white[-1]["22"] == 1000
    off = restore_steps({"20": False, "21": "colour", "24": "x"})
    assert off[-1] == {"20": False}
