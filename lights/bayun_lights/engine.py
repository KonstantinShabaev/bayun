"""Цикл кадров: эффект → лампы с заданной частотой."""

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator, Sequence

from bayun_lights.audio import Analyzer, AudioFeatures, rms_s16le
from bayun_lights.color import HSV, tuya_hsv
from bayun_lights.effects import Effect
from bayun_lights.transport import Sink

log = logging.getLogger(__name__)


class AudioThread:
    """Читает звук в фоне и отдаёт последние признаки; удар не теряется между кадрами."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks
        self._analyzer = Analyzer()
        self._q: queue.SimpleQueue[AudioFeatures] = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            for chunk in self._chunks:
                self._q.put(self._analyzer.feed(rms_s16le(chunk)))
        except Exception:
            log.exception("Чтение звука упало")
        else:
            log.error("Источник звука закрылся (parec завершился)")

    def latest(self) -> AudioFeatures | None:
        last, beat = None, False
        while True:
            try:
                last = self._q.get_nowait()
            except queue.Empty:
                break
            beat = beat or last.beat
        return AudioFeatures(last.level, beat) if last else None


def run(
    effect: Effect,
    sinks: Sequence[Sink],
    *,
    fps: float = 12.0,
    duration: float | None = None,
    audio: AudioThread | None = None,
    smooth: bool | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Возвращает число отправленных кадров (неизменные цвета не шлём)."""
    for s in sinks:
        s.start()
    last: list[tuple[int, int, int] | None] = [None] * len(sinks)
    sent = 0
    t0 = clock()
    period = 1 / fps
    features = None
    smooth = effect.smooth if smooth is None else smooth
    try:
        while True:
            now = clock() - t0
            if duration is not None and now >= duration:
                break
            if audio is not None:
                features = audio.latest() or features
            colors: list[HSV] = effect.frame(now, len(sinks), features)
            for i, (sink, color) in enumerate(zip(sinks, colors, strict=True)):
                key = tuya_hsv(color)
                if key != last[i]:
                    sink.send(color, smooth=smooth)
                    last[i] = key
                    sent += 1
            sleep(max(0.0, period - (clock() - t0 - now)))
    except KeyboardInterrupt:
        pass
    finally:
        for s in sinks:
            s.stop()
    return sent
