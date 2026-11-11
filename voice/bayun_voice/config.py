"""Конфигурация голосового цикла: config.yaml с валидацией."""

from pathlib import Path

import yaml
from pydantic import BaseModel


class AudioConfig(BaseModel):
    input_device: str | None = None
    output_device: str | None = None
    # Пока идёт разговор, остальной звук (видео) приглушается до этой доли. 1 — не трогать.
    duck_volume: float = 0.2
    # Приглушать в самом плеере: команда понимает --status (JSON) и --volume N.
    # Пусто — приглушать потоки PipeWire.
    duck_command: list[str] = []
    # Команда, которая будит экран и ставит нужный вход ТВ, чтобы голос было слышно.
    # Запускается в фоне, когда услышано имя. Пусто — ничего.
    wake_screen_command: list[str] = []


class WakeConfig(BaseModel):
    # openwakeword — обученная модель (model); stt — имя по распознаванию речи (pattern).
    engine: str = "openwakeword"
    model: str = "hey_jarvis"
    threshold: float = 0.5
    # Для engine: stt. Пусто — «Волтэр» во всех написаниях GigaAM.
    pattern: str = ""
    # Ниже — чувствительнее к тихой речи издалека, но чаще распознаёт шум.
    vad_threshold: float = 0.3
    # Сколько кадров по 80 мс до начала речи добавлять к куску: VAD опаздывает.
    preroll_frames: int = 4


class ListenConfig(BaseModel):
    start_timeout_s: float = 4.0
    silence_s: float = 0.8
    max_s: float = 12.0
    follow_up_s: float = 5.0
    # Порог Silero VAD для записи команды. 0,5 обрезает тихие хвосты издалека.
    vad_threshold: float = 0.3
    # Тихий сигнал «слушаю» после имени. По умолчанию выключен: раздражает,
    # а что Баюн слушает, видно на странице состояния.
    beep: bool = False


class ReplyConfig(BaseModel):
    # Если brain молчит дольше этого, сразу говорим короткое «угу».
    filler_after_s: float = 0.8
    fillers: list[str] = ["Угу.", "Так.", "Секунду."]
    # Имя во время ответа обрывает его и снова слушает.
    barge_in: bool = True
    # Позвали по имени без команды — коротко откликнуться, чтобы было ясно: услышал.
    # Пусто — молча слушать.
    ack: str = "Да?"


class SttConfig(BaseModel):
    engine: str = "onnx-gigaam-v3-e2e-ctc"


class ElevenLabsConfig(BaseModel):
    # Ключ — в переменной ELEVENLABS_API_KEY, не в конфиге.
    voice_id: str = ""
    model: str = "eleven_v3"
    url: str = "https://api.elevenlabs.io"


class YandexConfig(BaseModel):
    # Ключ — в переменной YANDEX_API_KEY, не в конфиге.
    voice: str = "filipp"
    emotion: str | None = None  # good, evil, neutral — не у всех голосов
    speed: float = 1.0


class TtsConfig(BaseModel):
    # piper, silero — локально; elevenlabs — облако с эмоциями и смехом,
    # наружу уходит только текст ответа.
    engine: str = "piper"
    # Диктор Silero: aidar, eugene (мужские), baya, kseniya, xenia (женские).
    speaker: str = "eugene"
    # Как произносить слова: {"Волтер": "Уо+лтэр"}. «+» — ударение (Silero).
    pronounce: dict[str, str] = {}
    elevenlabs: ElevenLabsConfig = ElevenLabsConfig()
    yandex: YandexConfig = YandexConfig()
    voice: Path = Path("voices/ru_RU-dmitri-medium.onnx")
    # Язык фонем вместо родного языка голоса: "ru" для китайского голоса даёт акцент.
    phonemes: str | None = None
    # Темп: больше 1 — медленнее.
    length_scale: float | None = None
    # Правка фонем перед синтезом: "zh" — очень сильный китайский акцент (accent.py).
    accent: str | None = None


class BrainConfig(BaseModel):
    url: str = "http://127.0.0.1:8765"
    timeout_s: float = 30.0


class StatusConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8770


class Config(BaseModel):
    audio: AudioConfig = AudioConfig()
    wake: WakeConfig = WakeConfig()
    listen: ListenConfig = ListenConfig()
    reply: ReplyConfig = ReplyConfig()
    stt: SttConfig = SttConfig()
    tts: TtsConfig = TtsConfig()
    brain: BrainConfig = BrainConfig()
    status: StatusConfig = StatusConfig()


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)
