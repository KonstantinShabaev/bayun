"""Голосовой цикл на сервере: python -m bayun_voice --config config.yaml

Половинный дуплекс для первого черновика: пока Баюн говорит, микрофон не слушается.
Перебивание появится вместе с эхоподавлением PipeWire (deploy/server/desktop).
"""

import argparse
import logging
import queue
import time
from pathlib import Path

from bayun_stt.asr import load_engine

from .audio import Mic, Playback, beep
from .brain_client import BrainClient, Turn
from .config import Config, load_config
from .listen import Endpointer, SpeechDetector, Verdict, load_vad
from .status import Status, serve
from .tts import PiperTts
from .wake import OpenWakeWord

_LOGGER = logging.getLogger("bayun_voice")

TROUBLE_SPEECH = "Что-то сломалось у меня внутри, загляни в журнал."


class VoiceLoop:
    def __init__(self, config: Config, status: Status) -> None:
        self.config = config
        self.status = status
        status.set(phase="loading")
        self.wake = OpenWakeWord(config.wake.model)
        self.vad: SpeechDetector = load_vad()
        self.asr = load_engine(config.stt.engine)
        self.tts = PiperTts(config.tts.voice)
        self.brain = BrainClient(config.brain.url, config.brain.timeout_s)
        self.mic = Mic(config.audio.input_device)
        _LOGGER.info(
            "Готово: имя %s, VAD %s, STT %s, голос %s",
            self.wake.name,
            self.vad.name,
            self.asr.name,
            config.tts.voice.stem,
        )

    def run(self) -> None:
        self.mic.start()
        self.status.set(phase="idle", wake_word=self.wake.name.replace("_", " "))
        while True:
            if self.wait_for_wake():
                self.conversation()

    def wait_for_wake(self) -> bool:
        while True:
            if self.status.talk.is_set():
                self.status.talk.clear()
                return True
            try:
                frame = self.mic.read(timeout=0.5)
            except queue.Empty:
                continue
            score = self.wake(frame)
            if score >= self.config.wake.threshold:
                _LOGGER.info("Имя услышано (%.2f)", score)
                return True

    def conversation(self) -> None:
        turn = Turn()
        timeout = self.config.listen.start_timeout_s
        while True:
            self.say_raw(beep(self.tts.rate))
            pcm = self.record(timeout)
            if pcm is None:
                break
            text = self.transcribe(pcm)
            if not text:
                break
            self.reply(text, turn)
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

    def reply(self, text: str, turn: Turn) -> None:
        started = time.monotonic()
        playback: Playback | None = None
        try:
            for sentence in self.brain.converse(text, turn):
                if playback is None:
                    first_s = time.monotonic() - started
                    _LOGGER.info("Первая фраза от brain через %.2f с", first_s)
                    self.status.set(
                        phase="speaking",
                        timings={**self.status.state["timings"], "first_s": first_s},
                    )
                    playback = Playback(self.tts.rate, self.config.audio.output_device)
                self.status.log("bayun", sentence)
                playback.write(self.tts.synthesize(sentence))
        except Exception:
            _LOGGER.exception("brain не ответил")
            turn.continue_conversation = False
            self.status.set(phase="error")
            self.status.log("system", "brain не ответил")
            self.say(TROUBLE_SPEECH)
        finally:
            if playback is not None:
                playback.close()

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
