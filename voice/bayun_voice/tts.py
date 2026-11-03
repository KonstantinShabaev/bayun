"""Озвучка Piper локально, по предложению за раз."""

from pathlib import Path
from typing import Any


class PiperTts:
    """`phonemes` подменяет язык espeak голоса: китайский голос с русскими фонемами
    читает по-русски с сильным акцентом. Набор фонем у голосов Piper общий."""

    def __init__(
        self, voice: Path, phonemes: str | None = None, length_scale: float | None = None
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
        self._syn: Any = SynthesisConfig(length_scale=length_scale)
        self.rate: int = self._voice.config.sample_rate

    def synthesize(self, text: str) -> bytes:
        chunks = self._voice.synthesize(text, syn_config=self._syn)
        return b"".join(chunk.audio_int16_bytes for chunk in chunks)
