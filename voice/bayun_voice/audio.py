"""Звук через PipeWire: запись `parec`, воспроизведение `pacat`.

Утилиты из pulseaudio-utils уже есть в любом рабочем столе с PipeWire, а сессия
пользователя сама выбирает устройства и сможет подставить источник с эхоподавлением.
"""

import logging
import math
import queue
import subprocess
import threading
import time
from collections.abc import Iterator
from typing import Any

import numpy as np

_LOGGER = logging.getLogger(__name__)

RATE = 16000
# 80 мс: такими кусками openWakeWord считает лучше всего.
FRAME_SAMPLES = 1280
FRAME_S = FRAME_SAMPLES / RATE

Frame = np.ndarray


def _device_args(device: str | None) -> list[str]:
    return [f"--device={device}"] if device else []


class Mic:
    """Постоянная запись в очередь кадров. Ничего не пишется на диск."""

    def __init__(self, device: str | None = None) -> None:
        self.device = device
        self.frames: queue.Queue[Frame] = queue.Queue(maxsize=200)
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        """parec падает при перезапуске PipeWire: поднимаем его заново."""
        cmd = [
            "parec",
            "--raw",
            "--format=s16le",
            f"--rate={RATE}",
            "--channels=1",
            "--latency-msec=40",
            "--client-name=bayun-voice",
            *_device_args(self.device),
        ]
        while True:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            self._read()
            self._proc.wait()
            _LOGGER.warning("Запись с микрофона прервалась, перезапускаю")
            time.sleep(1)

    def _read(self) -> None:
        assert self._proc and self._proc.stdout
        size = FRAME_SAMPLES * 2
        while chunk := self._proc.stdout.read(size):
            if len(chunk) < size:
                break
            frame = np.frombuffer(chunk, dtype=np.int16)
            try:
                self.frames.put_nowait(frame)
            except queue.Full:
                # Основной цикл занят (говорит или думает): старое выбрасываем.
                self.frames.get_nowait()
                self.frames.put_nowait(frame)

    def read(self, timeout: float = 1.0) -> Frame:
        return self.frames.get(timeout=timeout)

    def stream(self) -> Iterator[Frame]:
        while True:
            yield self.read(timeout=5.0)

    def drain(self) -> None:
        """Выбросить накопленное, например собственный голос после ответа."""
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                return

    def stop(self) -> None:
        if self._proc:
            self._proc.terminate()


class Playback:
    """Один поток воспроизведения на ответ: предложения пишутся по мере синтеза."""

    def __init__(self, rate: int, device: str | None = None) -> None:
        cmd = [
            "pacat",
            "--raw",
            "--format=s16le",
            f"--rate={rate}",
            "--channels=1",
            "--client-name=bayun-voice",
            "--stream-name=Баюн",
            *_device_args(device),
        ]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def write(self, pcm: bytes) -> None:
        assert self._proc.stdin
        self._proc.stdin.write(pcm)
        self._proc.stdin.flush()

    def close(self) -> None:
        """Дождаться, пока всё доиграет."""
        assert self._proc.stdin
        self._proc.stdin.close()
        self._proc.wait()


class Player:
    """Воспроизведение в своём потоке, чтобы можно было перебить на полуслове.

    `add` ставит кусок в очередь, `finish` — больше кусков не будет,
    `finished` взводится, когда всё доиграло или воспроизведение прервали.
    """

    def __init__(self, rate: int, device: str | None = None) -> None:
        self.rate = rate
        self.device = device
        self.finished = threading.Event()
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._aborted = False
        self._proc: subprocess.Popen[bytes] | None = None
        threading.Thread(target=self._run, daemon=True).start()

    def add(self, pcm: bytes) -> None:
        if pcm:
            self._queue.put(pcm)

    def finish(self) -> None:
        self._queue.put(None)

    def abort(self) -> None:
        self._aborted = True
        if self._proc:
            self._proc.kill()
        self._queue.put(None)

    def _run(self) -> None:
        cmd = [
            "pacat",
            "--raw",
            "--format=s16le",
            f"--rate={self.rate}",
            "--channels=1",
            "--latency-msec=100",
            "--client-name=bayun-voice",
            "--stream-name=Баюн",
            *_device_args(self.device),
        ]
        try:
            while (pcm := self._queue.get()) is not None and not self._aborted:
                if self._proc is None:
                    self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                assert self._proc.stdin
                # Пишем кусками по 0,1 с, чтобы прерывание срабатывало быстро.
                step = self.rate // 10 * 2
                for i in range(0, len(pcm), step):
                    if self._aborted:
                        break
                    self._proc.stdin.write(pcm[i : i + step])
            if self._proc and not self._aborted:
                assert self._proc.stdin
                self._proc.stdin.close()
                self._proc.wait()
        except (BrokenPipeError, ValueError):
            pass
        finally:
            self.finished.set()


class Ducker:
    """Приглушает чужие потоки PipeWire (видео в браузере) на время разговора."""

    def __init__(self, volume: float) -> None:
        self.volume = volume
        self._saved: dict[str, str] = {}

    def duck(self) -> None:
        if self.volume >= 1 or self._saved:
            return
        for index, volume in _other_sink_inputs():
            self._saved[index] = volume
            _pactl("set-sink-input-volume", index, f"{round(self.volume * 100)}%")

    def recover(self) -> None:
        """После падения посреди разговора поток так и остался бы тихим, а PipeWire
        ещё и запоминает громкость за приложением: возвращаем 100%."""
        ducked = f"{round(self.volume * 100)}%"
        for index, volume in _other_sink_inputs():
            if volume == ducked:
                _pactl("set-sink-input-volume", index, "100%")

    def restore(self) -> None:
        for index, volume in self._saved.items():
            _pactl("set-sink-input-volume", index, volume)
        self._saved.clear()


class PlayerDucker:
    """Приглушает видео в самом плеере (команда с --status и --volume N), не трогая
    PipeWire: громкость потока там запоминается за приложением и залипает."""

    def __init__(self, command: list[str], volume: float) -> None:
        self.command = command
        self.volume = volume
        self._saved: int | None = None

    def duck(self) -> None:
        if self.volume >= 1 or self._saved is not None:
            return
        import json

        try:
            state = json.loads(self._run("--status"))
        except json.JSONDecodeError:
            return  # видео нет — приглушать нечего
        if not state.get("playing"):
            return
        self._saved = int(state.get("volume", 100))
        self._run("--volume", str(round(self._saved * self.volume)))

    def restore(self) -> None:
        if self._saved is not None:
            self._run("--volume", str(self._saved))
            self._saved = None

    def recover(self) -> None:
        pass

    def _run(self, *args: str) -> str:
        try:
            return subprocess.run(
                [*self.command, *args], capture_output=True, text=True, timeout=5, check=False
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""


def _pactl(*args: str) -> str:
    try:
        return subprocess.run(
            ["pactl", *args], capture_output=True, text=True, timeout=3, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _other_sink_inputs() -> list[tuple[str, str]]:
    """(номер, громкость) потоков, кроме собственных."""
    import json

    try:
        inputs = json.loads(_pactl("--format=json", "list", "sink-inputs") or "[]")
    except json.JSONDecodeError:
        return []
    found = []
    for item in inputs:
        if item.get("properties", {}).get("application.name") == "bayun-voice":
            continue
        channels = item.get("volume", {})
        first: dict[str, Any] = next(iter(channels.values()), {}) if channels else {}
        found.append((str(item["index"]), first.get("value_percent", "100%")))
    return found


def beep(rate: int, freq: float = 880.0, duration_s: float = 0.12, volume: float = 0.3) -> bytes:
    """Короткий сигнал «слушаю» с мягкими краями, чтобы не щёлкал."""
    n = int(rate * duration_s)
    t = np.arange(n) / rate
    fade = np.minimum(1.0, np.minimum(t, duration_s - t) / 0.01)
    wave = np.sin(2 * math.pi * freq * t) * fade * volume
    return (wave * 32767).astype(np.int16).tobytes()
