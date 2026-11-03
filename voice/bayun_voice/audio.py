"""Звук через PipeWire: запись `parec`, воспроизведение `pacat`.

Утилиты из pulseaudio-utils уже есть в любом рабочем столе с PipeWire, а сессия
пользователя сама выбирает устройства и сможет подставить источник с эхоподавлением.
"""

import math
import queue
import subprocess
import threading
from collections.abc import Iterator

import numpy as np

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
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        threading.Thread(target=self._read, daemon=True).start()

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


def beep(rate: int, freq: float = 880.0, duration_s: float = 0.12, volume: float = 0.3) -> bytes:
    """Короткий сигнал «слушаю» с мягкими краями, чтобы не щёлкал."""
    n = int(rate * duration_s)
    t = np.arange(n) / rate
    fade = np.minimum(1.0, np.minimum(t, duration_s - t) / 0.01)
    wave = np.sin(2 * math.pi * freq * t) * fade * volume
    return (wave * 32767).astype(np.int16).tobytes()
