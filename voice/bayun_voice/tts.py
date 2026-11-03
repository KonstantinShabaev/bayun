"""Озвучка Piper локально, по предложению за раз."""

from pathlib import Path
from typing import Any


class PiperTts:
    def __init__(self, voice: Path) -> None:
        from piper import PiperVoice

        if not voice.exists():
            raise FileNotFoundError(
                f"Нет голоса {voice}. Скачать: python -m piper.download_voices "
                f"--download-dir {voice.parent} {voice.stem}"
            )
        self._voice: Any = PiperVoice.load(str(voice))
        self.rate: int = self._voice.config.sample_rate

    def synthesize(self, text: str) -> bytes:
        return b"".join(chunk.audio_int16_bytes for chunk in self._voice.synthesize(text))
