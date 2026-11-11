"""Голосовой цикл на сервере: python -m bayun_voice --config config.yaml

Половинный дуплекс для первого черновика: пока Баюн говорит, микрофон не слушается.
Перебивание появится вместе с эхоподавлением PipeWire (deploy/server/desktop).
"""

import argparse
import itertools
import logging
import os
import queue
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

from bayun_stt.asr import load_engine

from .announce import Announcement, Announcer
from .audio import Ducker, Frame, Mic, Playback, Player, PlayerDucker, beep
from .brain_client import BrainClient, Turn
from .config import Config, load_config
from .listen import Endpointer, SpeechDetector, Verdict, load_vad
from .status import Status, serve
from .tts import load_tts, strip_audio_tags
from .wake import Frontend, OpenWakeWord, SttWake, name_match, normalize, strip_names

_LOGGER = logging.getLogger("bayun_voice")

NEAR_MISS = 0.15
# Микрофон отдаёт ровно ноль дольше этого — выключен кнопкой или завис.
# Живой fifine даже в тишине даёт −60 dBFS, ровного нуля не бывает: 5 с хватает.
MIC_SILENT_S = 5.0

STOP_WORDS = re.compile(r"\b(стоп|хватит|замолчи|помолчи|подожди|тихо|джарв\w*)\b")

TROUBLE_SPEECH = "Что-то сломалось у меня внутри, загляни в журнал."

# Частые короткие ответы brain: синтезируются при старте и звучат без задержки.
COMMON_REPLIES = ["Готово.", "Включаю.", "Выключаю.", "Запомнил.", "Отменил.", "Забыл."]
CACHE_MAX_CHARS = 40


class VoiceLoop:
    def __init__(self, config: Config, status: Status) -> None:
        self.config = config
        self.status = status
        status.set(phase="loading")
        self.vad: SpeechDetector = load_vad()
        self.asr = load_engine(config.stt.engine)
        wake = config.wake
        self.wake: OpenWakeWord | SttWake = (
            SttWake(self.asr, load_vad(), wake.pattern, wake.vad_threshold, wake.preroll_frames)
            if wake.engine == "stt"
            else OpenWakeWord(wake.model)
        )
        self.tts = load_tts(config.tts)
        self.brain = BrainClient(config.brain.url, config.brain.timeout_s)
        self.announcer = Announcer(config.brain.url)
        self._mic_zero_since: float | None = None
        self.mic = Mic(config.audio.input_device)
        audio = config.audio
        self.ducker: Ducker | PlayerDucker = (
            PlayerDucker(audio.duck_command, audio.duck_volume)
            if audio.duck_command
            else Ducker(audio.duck_volume)
        )
        # «Угу» синтезируем заранее, чтобы оно звучало мгновенно.
        self.fillers = itertools.cycle(
            [self.tts.synthesize(f) for f in config.reply.fillers] or [b""]
        )
        self._speech_cache: dict[str, bytes] = {}
        for text in COMMON_REPLIES:
            self.synthesize(text)
        _LOGGER.info(
            "Готово: имя %s, VAD %s, STT %s, голос %s",
            self.wake.name,
            self.vad.name,
            self.asr.name,
            config.tts.engine if config.tts.engine != "piper" else config.tts.voice.stem,
        )

    def run(self) -> None:
        self.ducker.recover()
        # systemd и kill шлют SIGTERM: сначала вернуть громкость видео.
        signal.signal(signal.SIGTERM, self._terminate)
        self.mic.start()
        self.announcer.start()
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
            if (announcement := self.announcer.next()) is not None:
                self.announce(announcement)
                continue
            if self.status.talk.is_set():
                self.status.talk.clear()
                _LOGGER.info("Кнопка «Говорить»")
                return True
            try:
                frame = self.mic.read(timeout=0.5)
            except queue.Empty:
                continue
            self.watch_mic(frame)
            score = self.wake(frame)
            if score >= self.config.wake.threshold:
                _LOGGER.info("Имя услышано (%.2f)", score)
                return True
            if isinstance(self.wake, SttWake) and self.wake.near_miss:
                # Только само слово, не вся фраза: видно, как GigaAM слышит имя.
                _LOGGER.info("Почти имя: %r", self.wake.near_miss)
                self.wake.near_miss = ""
            elif score >= NEAR_MISS:
                # Похоже на имя, но не дотянуло: по журналу видно, не занижен ли порог.
                _LOGGER.info("Почти имя (%.2f)", score)

    def watch_mic(self, frame: Frame) -> None:
        """Живой микрофон всегда немного шумит; сплошные нули подряд — его выключили."""
        now = time.monotonic()
        silent = self.status.state.get("mic") == "silent"
        if frame.any():
            self._mic_zero_since = None
            if silent:
                _LOGGER.info("Микрофон снова слышит")
                self.status.set(mic="ok")
            return
        if self._mic_zero_since is None:
            self._mic_zero_since = now
        elif not silent and now - self._mic_zero_since > MIC_SILENT_S:
            _LOGGER.warning("Микрофон присылает тишину: выключен кнопкой или отвалился")
            self.status.set(mic="silent")

    def _terminate(self, *_: object) -> None:
        self.ducker.restore()
        self.mic.stop()
        # sys.exit из обработчика сигнала внутри queue.get роняет процесс с
        # «release unlocked lock» и кодом 1: systemd пишет FAILURE. Выходим сразу.
        os._exit(0)

    def wake_screen(self) -> None:
        command = self.config.audio.wake_screen_command
        if command:
            try:
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as error:
                _LOGGER.warning("Не смог разбудить экран: %s", error)

    def announce(self, announcement: Announcement) -> None:
        """Сработал таймер или Claude Code спрашивает: говорим сами, без имени."""
        _LOGGER.info("Объявление: %s", announcement.text)
        self.wake_screen()
        self.ducker.duck()
        try:
            self.status.set(phase="speaking")
            self.status.log("bayun", strip_audio_tags(announcement.text))
            self.say(announcement.text)
            if announcement.expects_reply:
                self.conversation(Turn(announcement.conversation_id), listen_first=True)
            elif announcement.listen:
                self.conversation(listen_first=True)
        finally:
            self.ducker.restore()
            self.wake.reset()
            self.mic.drain()
            self.status.set(phase="idle", level=0.0)

    def conversation(self, turn: Turn | None = None, listen_first: bool = False) -> None:
        turn = turn or Turn()
        timeout = self.config.listen.start_timeout_s
        # Сразу после имени в очереди уже начало команды: его не выбрасываем.
        # После своей речи (объявление, ответ) — выбрасываем эхо.
        keep_queue = not listen_first
        while True:
            # «Волтэр, включи свет» одной фразой: команда уже распознана вместе с именем.
            text = "" if listen_first else self._said_with_name()
            listen_first = False
            if text:
                _LOGGER.info("Вместе с именем: %r", text)
                self.status.log("user", text)
            else:
                if self.config.listen.beep and timeout == self.config.listen.start_timeout_s:
                    self.say_raw(beep(self.tts.rate, volume=0.1))
                pcm = self.record(timeout, drain=not keep_queue)
                keep_queue = False
                if pcm is None:
                    break
                heard = self.transcribe(pcm)
                text = strip_names(heard) if isinstance(self.wake, SttWake) else heard
                if heard and not text:
                    # Позвали ещё раз («Волтэр?»), не дождавшись: это не команда.
                    _LOGGER.info("Только имя, слушаю дальше")
                    timeout = self.config.listen.start_timeout_s
                    continue
                if not text:
                    break
            barged = self.reply(text, turn)
            if barged == "name":
                # Перебили по имени: сразу слушаем новую фразу (команда могла прийти с ним).
                timeout = self.config.listen.start_timeout_s
                continue
            if barged == "stop":
                break
            if not (turn.continue_conversation and self.config.listen.follow_up_s > 0):
                break
            timeout = self.config.listen.follow_up_s
        self.wake.reset()
        self.mic.drain()
        self.status.set(phase="idle", level=0.0)

    def record(self, start_timeout_s: float, drain: bool = True) -> bytes | None:
        if drain:
            self.mic.drain()
        self.vad.reset()
        self.status.set(phase="listening")
        listen = self.config.listen
        ender = Endpointer(start_timeout_s, listen.silence_s, listen.max_s, listen.vad_threshold)
        frontend = Frontend()
        frames: list[bytes] = []
        for frame in self.mic.stream():
            frame = frontend(frame)
            frames.append(frame.tobytes())
            self.status.level(frames[-1])
            verdict = ender.push(self.vad(frame))
            if verdict is Verdict.DONE:
                return b"".join(frames)
            if verdict is Verdict.NOTHING:
                _LOGGER.info("Речи так и не услышал")
                return None
        return None

    def transcribe(self, pcm: bytes) -> str:
        self.status.set(phase="thinking", level=0.0)
        started = time.monotonic()
        import numpy as np

        # Издалека команда тихая, как и имя: поднимаем по громкости речи.
        audio = normalize(np.frombuffer(pcm, dtype=np.int16))
        text = self.asr.transcribe(audio.tobytes(), 16000).strip()
        stt_s = time.monotonic() - started
        _LOGGER.info("STT %.2f с: %r", stt_s, text)
        self.status.set(timings={"stt_s": stt_s})
        if text:
            self.status.log("user", text)
        return text

    def reply(self, text: str, turn: Turn) -> str | None:
        """Говорит ответ по мере готовности. "name" — перебили по имени, "stop" — стоп-словом."""
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
        player = Player(self.tts.rate, self.config.audio.output_device, self.status.level)
        self.mic.drain()
        self.wake.reset()
        self.vad.reset()
        self._barge: list[bytes] = []
        self._barge_silence = 0
        # Что сам говорит сейчас: эхо из ТВ со словом «подожди» или своим именем
        # не должно обрывать ответ (эхоподавления нет).
        self._spoken = ""
        first = True
        filler_played = False
        producing = True
        while not player.finished.is_set():
            if self.config.reply.barge_in and (barged := self.heard_name()):
                player.abort()
                _LOGGER.info("Перебили (%s)", barged)
                self.status.log("system", "перебили")
                return barged
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
                self._spoken += " " + TROUBLE_SPEECH.lower()
                player.add(self.synthesize(TROUBLE_SPEECH))
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
                self._spoken += " " + item.lower()
                player.add(self.synthesize(item))
        return None

    def _said_with_name(self) -> str:
        if not isinstance(self.wake, SttWake):
            return ""
        text, self.wake.remainder = self.wake.remainder, ""
        return text

    def _echo(self, text: str) -> bool:
        """Кусок — это, похоже, эхо собственного ответа."""
        spoken = getattr(self, "_spoken", "")
        if not spoken:
            return False
        if (m := STOP_WORDS.search(text.lower())) and m.group() in spoken:
            return True
        return name_match(text) is not None and any(
            name_match(w) for w in re.findall(r"\w+[,.!?]?", spoken)
        )

    def heard_name(self) -> str | None:
        """Перебили ли: имя по детектору или «стоп/хватит» по короткому STT.

        Эхоподавления нет, микрофон слышит и сам ответ из ТВ, поэтому любой речи
        мало: распознаём кусок и ищем стоп-слово, которого в ответах не бывает.
        """
        while True:
            try:
                frame = self.mic.frames.get_nowait()
            except queue.Empty:
                return None
            if isinstance(self.wake, SttWake):
                # Детектор и так распознаёт каждый кусок: стоп-слово ищем в нём же.
                self.wake.last_text = ""
                named = self.wake(frame) >= 1.0
                heard = self.wake.last_text
                if heard and self._echo(heard):
                    _LOGGER.info("Своё эхо, не перебиваю: %r", heard)
                    self.wake.remainder = ""
                    continue
                if named:
                    return "stop" if STOP_WORDS.search(self.wake.remainder.lower()) else "name"
                if STOP_WORDS.search(heard.lower()):
                    _LOGGER.info("Стоп-слово: %r", heard)
                    return "stop"
                continue
            if self.wake(frame) >= self.config.wake.threshold:
                self.wake.reset()
                return "name"
            if self._barge_frame(frame):
                return "stop"

    def _barge_frame(self, frame: Frame) -> bool:
        speech = self.vad(frame) >= 0.5
        if speech:
            self._barge.append(frame.tobytes())
            self._barge_silence = 0
        elif self._barge:
            self._barge_silence += 1
        # Кусок закончен: пауза 0,3 с или уже 2 с речи.
        if not self._barge or (self._barge_silence < 4 and len(self._barge) < 25):
            return False
        pcm, self._barge = b"".join(self._barge), []
        if len(pcm) < 2 * 16000 * 0.25:
            return False
        text = self.asr.transcribe(pcm, 16000).lower()
        if STOP_WORDS.search(text):
            _LOGGER.info("Стоп-слово: %r", text)
            return True
        return False

    def say(self, text: str) -> None:
        self.say_raw(self.synthesize(text))

    def synthesize(self, text: str) -> bytes:
        """Короткие фразы повторяются («Готово.»): синтез один раз."""
        if len(text) > CACHE_MAX_CHARS:
            return self.tts.synthesize(text)
        if text not in self._speech_cache:
            if len(self._speech_cache) > 200:
                self._speech_cache.clear()
            self._speech_cache[text] = self.tts.synthesize(text)
        return self._speech_cache[text]

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
    serve(status, config.status.host, config.status.port, config.brain.url)
    _LOGGER.info("Страница состояния: http://%s:%d", config.status.host, config.status.port)
    VoiceLoop(config, status).run()


if __name__ == "__main__":
    run()
