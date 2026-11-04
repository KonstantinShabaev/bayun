"""bayun-lights: свои эффекты для ламп Tuya напрямую по LAN.

    bayun-lights list
    bayun-lights rainbow --fps 12
    bayun-lights music --lamp "LED BULB W5K" --lamp 192.168.0.58
    bayun-lights candle --dry-run --duration 2

Ctrl+C — остановить, лампы вернутся в прежнее состояние.
"""

import argparse
import logging
from pathlib import Path

from bayun_lights import devices
from bayun_lights.audio import monitor_chunks
from bayun_lights.effects import EFFECTS
from bayun_lights.engine import AudioThread, run
from bayun_lights.transport import PrintSink, Sink, TuyaSink


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="bayun-lights", description=__doc__.split("\n")[0])
    p.add_argument("effect", choices=["list", *EFFECTS])
    p.add_argument("--devices", type=Path, default=devices.DEFAULT_PATH)
    p.add_argument("--lamp", action="append", default=[], help="имя, IP или id; можно несколько")
    p.add_argument("--fps", type=float, default=12.0)
    p.add_argument("--duration", type=float, help="секунд; по умолчанию до Ctrl+C")
    p.add_argument("--audio-device", default="@DEFAULT_MONITOR@")
    p.add_argument(
        "--smooth",
        action=argparse.BooleanOptionalAction,
        help="лампа плавно перетекает между кадрами (по умолчанию — как задумано эффектом)",
    )
    p.add_argument("--dry-run", action="store_true", help="печатать кадры, лампы не трогать")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    lamps = devices.select(devices.load(a.devices), a.lamp)
    if a.effect == "list":
        for lamp in lamps:
            print(f"{lamp.name}\t{lamp.ip}\tv{lamp.version}")
        return

    effect = EFFECTS[a.effect]()
    sinks: list[Sink] = [PrintSink(x) if a.dry_run else TuyaSink(x) for x in lamps]
    audio = None
    if effect.needs_audio:
        audio = AudioThread(monitor_chunks(a.audio_device))
        audio.start()
    sent = run(effect, sinks, fps=a.fps, duration=a.duration, audio=audio, smooth=a.smooth)
    print(f"Отправлено кадров: {sent}")


if __name__ == "__main__":
    main()
