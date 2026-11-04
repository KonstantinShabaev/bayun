"""Голосовой цикл на сервере: python -m bayun_voice --config config.yaml

Половинный дуплекс для первого черновика: пока Баюн говорит, микрофон не слушается.
Перебивание появится вместе с эхоподавлением PipeWire (deploy/server/desktop).
"""

import argparse
import itertools
import logging
import queue
import subprocess
import threading
import time
from pathlib import Path

from bayun_stt.asr import load_engine

from .audio import Ducker, Mic, Playback, Player, beep
from .brain_client import BrainClient, Turn
from .config import Config, load_config
from .listen import Endpointer, SpeechDetector, Verdict, load_vad
from .status import Status, serve
from .tts import load_tts, strip_audio_tags
from .wake import OpenWakeWord

_LOGGER = logging.getLogger("bayun_voice")

NEAR_MISS = 0.15

TROUBLE_SPEECH = "Что-то сломалось у меня внутри, загляни в журнал."


class VoiceLoop:
    def __init__(self, config: Config, status: Status) -> None:
        self.config = config
        self.status = status
        status.set(phase="loading")
        self.wake = OpenWakeWord(config.wake.model)
        self.vad: SpeechDetector = load_vad()
        self.asr = load_engine(config.stt.engine)
        self.tts = load_tts(config.tts)
        self.brain = BrainClient(config.brain.url, config.brain.timeout_s)
        self.mic = Mic(config.audio.input_device)
        self.ducker = Ducker(config.audio.duck_volume)
        # «Угу» синтезируем заранее, чтобы оно звучало мгновенно.
        self.fillers = itertools.cycle(
            [self.tts.synthesize(f) for f in config.reply.fillers] or [b""]
        )
        _LOGGER.info(
            "Готово: имя %s, VAD %s, STT %s, голос %s",
            self.wake.name,
            self.vad.name,
            self.asr.name,
            config.tts.engine if config.tts.engine != "piper" else config.tts.voice.stem,
        )

    def run(self) -> None:
        self.mic.start()
        self.status.set(phase="idle", wake_word=self.wake.name.replace("_", " "))
        while True:
            if self.wait_for_wake():
                self.wake_screen()
                self.ducker.duck()
                try:
                    self.conversation()
                finally:
                    self.ducker.restore()

    def wait_for_wake(self) -> bool:
        while True:
            if self.status.talk.is_set():
                self.status.talk.clear()
                _LOGGER.info("Кнопка «Говорить»")
                return True
            try:
                frame = self.mic.read(timeout=0.5)
            except queue.Empty:
                continue
            score = self.wake(frame)
            if score >= self.config.wake.threshold:
                _LOGGER.info("Имя услышано (%.2f)", score)
                return True
            if score >= NEAR_MISS:
                # Похоже на имя, но не дотянуло: по журналу видно, не занижен ли порог.
                _LOGGER.info("Почти имя (%.2f)", score)

    def wake_screen(self) -> None:
        command = self.config.audio.wake_screen_command
        if command:
            try:
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as error:
                _LOGGER.warning("Не смог разбудить экран: %s", error)

    def conversation(self) -> None:
        turn = Turn()
        timeout = self.config.listen.start_timeout_s
        while True:
            if self.config.listen.beep and timeout == self.config.listen.start_timeout_s:
                self.say_raw(beep(self.tts.rate, volume=0.1))
            pcm = self.record(timeout)
            if pcm is None:
                break
            text = self.transcribe(pcm)
            if not text:
                break
            if self.reply(text, turn):
                # Перебили по имени: сразу слушаем новую фразу.
                timeout = self.config.listen.start_timeout_s
                continue
            if not (turn.continue_conversation and self.config.listen.follow_up_s > 0):
                break
            timeout = self.config.listen.follow_up_s
        self.wake.reset()
        self.mic.drain()
        self.status.set(phase="idle")

    def record(self, start_timeout_s: float) -> bytes | None:
        self.mic.drain()
        self.vad.reset()
        self.status.set(phase="listening")
        listen = self.config.listen
        ender = Endpointer(start_timeout_s, listen.silence_s, listen.max_s)
        frames: list[bytes] = []
        for frame in self.mic.stream():
            frames.append(frame.tobytes())
            verdict = ender.push(self.vad(frame))
            if verdict is Verdict.DONE:
                return b"".join(frames)
            if verdict is Verdict.NOTHING:
                _LOGGER.info("Речи так и не услышал")
                return None
        return None

    def transcribe(self, pcm: bytes) -> str:
        self.status.set(phase="thinking")
        started = time.monotonic()
        text = self.asr.transcribe(pcm, 16000).strip()
        stt_s = time.monotonic() - started
        _LOGGER.info("STT %.2f с: %r", stt_s, text)
        self.status.set(timings={"stt_s": stt_s})
        if text:
            self.status.log("user", text)
        return text

    def reply(self, text: str, turn: Turn) -> bool:
        """Говорит ответ по мере готовности. True — перебили по имени."""
        started = time.monotonic()
        sentences: queue.Queue[str | Exception | None] = queue.Queue()

        def produce() -> None:
            try:
                for sentence in self.brain.converse(text, turn, self.tts.audio_tags):
                    sentences.put(sentence)
            except Exception as error:
                sentences.put(error)
            finally:
                sentences.put(None)

        threading.Thread(target=produce, daemon=True).start()
        player = Player(self.tts.rate, self.config.audio.output_device)
        self.mic.drain()
        self.wake.reset()
        first = True
        filler_played = False
        producing = True
        while not player.finished.is_set():
            if self.config.reply.barge_in and self.heard_name():
                player.abort()
                _LOGGER.info("Перебили")
                self.status.log("system", "перебили")
                return True
            if not producing:
                player.finished.wait(0.05)
                continue
            try:
                item = sentences.get(timeout=0.05)
            except queue.Empty:
                waited = time.monotonic() - started
                if first and not filler_played and waited >= self.config.reply.filler_after_s:
                    player.add(next(self.fillers))
                    filler_played = True
                continue
            if item is None:
                producing = False
                player.finish()
            elif isinstance(item, Exception):
                _LOGGER.error("brain не ответил: %s", item)
                turn.continue_conversation = False
                self.status.set(phase="error")
                self.status.log("system", "brain не ответил")
                player.add(self.tts.synthesize(TROUBLE_SPEECH))
            else:
                if first:
                    first = False
                    first_s = time.monotonic() - started
                    _LOGGER.info("Первая фраза от brain через %.2f с", first_s)
                    self.status.set(
                        phase="speaking",
                        timings={**self.status.state["timings"], "first_s": first_s},
                    )
                self.status.log("bayun", strip_audio_tags(item))
                player.add(self.tts.synthesize(item))
        return False

    def heard_name(self) -> bool:
        """Прогнать накопленные кадры микрофона через детектор имени."""
        while True:
            try:
                frame = self.mic.frames.get_nowait()
            except queue.Empty:
                return False
            if self.wake(frame) >= self.config.wake.threshold:
                self.wake.reset()
                return True

    def say(self, text: str) -> None:
        self.say_raw(self.tts.synthesize(text))

    def say_raw(self, pcm: bytes) -> None:
        playback = Playback(self.tts.rate, self.config.audio.output_device)
        playback.write(pcm)
        playback.close()


def run() -> None:
    parser = argparse.ArgumentParser(prog="bayun-voice")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    status = Status()
    serve(status, config.status.host, config.status.port)
    _LOGGER.info("Страница состояния: http://%s:%d", config.status.host, config.status.port)
    VoiceLoop(config, status).run()


if __name__ == "__main__":
    run()
