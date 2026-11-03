"""Конфигурация голосового цикла: config.yaml с валидацией."""

from pathlib import Path

import yaml
from pydantic import BaseModel


class AudioConfig(BaseModel):
    input_device: str | None = None
    output_device: str | None = None


class WakeConfig(BaseModel):
    model: str = "hey_jarvis"
    threshold: float = 0.5


class ListenConfig(BaseModel):
    start_timeout_s: float = 4.0
    silence_s: float = 0.8
    max_s: float = 12.0
    follow_up_s: float = 5.0


class SttConfig(BaseModel):
    engine: str = "whisper-small"


class ElevenLabsConfig(BaseModel):
    # Ключ — в переменной ELEVENLABS_API_KEY, не в конфиге.
    voice_id: str = ""
    model: str = "eleven_v3"
    url: str = "https://api.elevenlabs.io"


class TtsConfig(BaseModel):
    # piper — локально; elevenlabs — облако с эмоциями и смехом, наружу уходит только текст ответа.
    engine: str = "piper"
    elevenlabs: ElevenLabsConfig = ElevenLabsConfig()
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
    stt: SttConfig = SttConfig()
    tts: TtsConfig = TtsConfig()
    brain: BrainConfig = BrainConfig()
    status: StatusConfig = StatusConfig()


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)
