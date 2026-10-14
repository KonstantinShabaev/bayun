from typing import Any

from bayun_stt.handler import SttEventHandler
from wyoming.asr import Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Describe, Info


class FakeAsr:
    name = "fake"

    def __init__(self) -> None:
        self.received = b""

    def transcribe(self, pcm: bytes, rate: int) -> str:
        self.received = pcm
        return "включи свет"


class RecordingHandler(SttEventHandler):
    def __init__(self, engine: Any) -> None:
        # Без настоящего сокета: перехватываем write_event.
        self.engine = engine
        from wyoming.audio import AudioChunkConverter

        self.converter = AudioChunkConverter(rate=16000, width=2, channels=1)
        self.audio = bytearray()
        self.sent: list[Event] = []

    async def write_event(self, event: Event) -> None:
        self.sent.append(event)


async def test_describe_returns_russian_asr() -> None:
    handler = RecordingHandler(FakeAsr())
    await handler.handle_event(Describe().event())
    info = Info.from_event(handler.sent[0])
    assert info.asr[0].models[0].languages == ["ru"]


async def test_audio_roundtrip_returns_transcript() -> None:
    asr = FakeAsr()
    handler = RecordingHandler(asr)
    pcm = b"\x00\x01" * 160
    await handler.handle_event(AudioStart(rate=16000, width=2, channels=1).event())
    await handler.handle_event(AudioChunk(rate=16000, width=2, channels=1, audio=pcm).event())
    keep_going = await handler.handle_event(AudioStop().event())

    assert keep_going is False
    assert asr.received == pcm
    assert Transcript.from_event(handler.sent[-1]).text == "включи свет"
