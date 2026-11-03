"""Озвучка по предложению за раз: Piper локально или ElevenLabs в облаке."""

import os
import re
from pathlib import Path
from typing import Any, Protocol

import httpx

from .accent import ACCENTS
from .config import TtsConfig

_AUDIO_TAG = re.compile(r"\[[^\]]{1,30}\]\s*")


def strip_audio_tags(text: str) -> str:
    """[laughs] и подобные теги Piper прочитал бы вслух."""
    return " ".join(_AUDIO_TAG.sub("", text).split())


class Tts(Protocol):
    rate: int
    # Понимает ли теги эмоций в тексте.
    audio_tags: bool

    def synthesize(self, text: str) -> bytes: ...


class PiperTts:
    audio_tags = False

    """`phonemes` подменяет язык espeak голоса: китайский голос с русскими фонемами
    читает по-русски с сильным акцентом. Набор фонем у голосов Piper общий."""

    def __init__(
        self,
        voice: Path,
        phonemes: str | None = None,
        length_scale: float | None = None,
        accent: str | None = None,
    ) -> None:
        from piper import PiperVoice, SynthesisConfig

        if not voice.exists():
            raise FileNotFoundError(
                f"Нет голоса {voice}. Скачать: python -m piper.download_voices "
                f"--download-dir {voice.parent} {voice.stem}"
            )
        self._voice: Any = PiperVoice.load(str(voice))
        if phonemes:
            self._voice.config.espeak_voice = phonemes
        if accent:
            shift = ACCENTS[accent]
            phonemize = self._voice.phonemize
            self._voice.phonemize = lambda text: [shift(s) for s in phonemize(text)]
        self._syn: Any = SynthesisConfig(length_scale=length_scale)
        self.rate: int = self._voice.config.sample_rate

    def synthesize(self, text: str) -> bytes:
        text = strip_audio_tags(text)
        if not text:
            return b""
        chunks = self._voice.synthesize(text, syn_config=self._syn)
        return b"".join(chunk.audio_int16_bytes for chunk in chunks)


class ElevenLabsTts:
    """Eleven v3: интонации, смех и эмоции по тегам. Отдаёт сырой PCM 22 кГц."""

    rate = 22050
    audio_tags = True

    def __init__(self, voice_id: str, model: str, url: str, api_key: str) -> None:
        if not voice_id or not api_key:
            raise ValueError("Для ElevenLabs нужны tts.elevenlabs.voice_id и ELEVENLABS_API_KEY")
        self.voice_id = voice_id
        self.model = model
        self._client = httpx.Client(base_url=url, headers={"xi-api-key": api_key}, timeout=30)

    def synthesize(self, text: str) -> bytes:
        response = self._client.post(
            f"/v1/text-to-speech/{self.voice_id}",
            params={"output_format": f"pcm_{self.rate}"},
            json={"text": text, "model_id": self.model, "language_code": "ru"},
        )
        response.raise_for_status()
        return response.content


def load_tts(config: TtsConfig) -> Tts:
    if config.engine == "elevenlabs":
        el = config.elevenlabs
        return ElevenLabsTts(
            el.voice_id, el.model, el.url, os.environ.get("ELEVENLABS_API_KEY", "")
        )
    if config.engine == "piper":
        return PiperTts(config.voice, config.phonemes, config.length_scale, config.accent)
    raise ValueError(f"Неизвестный движок TTS: {config.engine!r}. Доступно: piper, elevenlabs")
