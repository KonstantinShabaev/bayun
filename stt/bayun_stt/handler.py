"""Обработка событий Wyoming: Describe → Info, AudioStart/Chunk/Stop → Transcript."""

import logging
from typing import Any

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import AsrModel, AsrProgram, Attribution, Describe, Info
from wyoming.server import AsyncEventHandler

from . import __version__
from .asr import AsrEngine

_LOGGER = logging.getLogger(__name__)

RATE = 16000


def build_info(engine: AsrEngine) -> Info:
    attribution = Attribution(name="Bayun", url="https://github.com/KonstantinShabaev/bayun")
    return Info(
        asr=[
            AsrProgram(
                name="bayun-stt",
                description="Русское распознавание речи для Home Assistant",
                attribution=attribution,
                installed=True,
                version=__version__,
                models=[
                    AsrModel(
                        name=engine.name,
                        description=engine.name,
                        attribution=attribution,
                        installed=True,
                        languages=["ru"],
                        version=__version__,
                    )
                ],
            )
        ]
    )


class SttEventHandler(AsyncEventHandler):
    def __init__(self, engine: AsrEngine, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.engine = engine
        self.converter = AudioChunkConverter(rate=RATE, width=2, channels=1)
        self.audio = bytearray()

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(build_info(self.engine).event())
            return True

        if Transcribe.is_type(event.type) or AudioStart.is_type(event.type):
            self.audio.clear()
            return True

        if AudioChunk.is_type(event.type):
            chunk = self.converter.convert(AudioChunk.from_event(event))
            self.audio.extend(chunk.audio)
            return True

        if AudioStop.is_type(event.type):
            text = self.engine.transcribe(bytes(self.audio), RATE)
            _LOGGER.debug("Распознано: %r", text)
            await self.write_event(Transcript(text=text).event())
            self.audio.clear()
            return False

        return True
