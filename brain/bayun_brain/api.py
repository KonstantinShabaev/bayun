"""HTTP API: /converse, /speaker-hint, /health (см. docs/PROJECT_SPEC.md, §4.2)."""

import time
from dataclasses import dataclass

from fastapi import FastAPI
from pydantic import BaseModel

from . import __version__
from .config import Config


class ConverseRequest(BaseModel):
    text: str
    conversation_id: str | None = None
    device_id: str | None = None
    language: str = "ru"


class ConverseResponse(BaseModel):
    speech: str
    continue_conversation: bool = False


class SpeakerHint(BaseModel):
    speaker: str
    confidence: float
    ts: float | None = None


@dataclass
class HintStore:
    """Последняя подсказка о говорящем от STT, сопоставляется по времени."""

    window_s: float
    last: SpeakerHint | None = None

    def put(self, hint: SpeakerHint) -> None:
        if hint.ts is None:
            hint = hint.model_copy(update={"ts": time.time()})
        self.last = hint

    def speaker_for(self, now: float) -> str:
        hint = self.last
        if hint is None or hint.ts is None or now - hint.ts > self.window_s:
            return "guest"
        return hint.speaker


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config()
    app = FastAPI(title="bayun-brain", version=__version__)
    hints = HintStore(window_s=config.speaker_hint_window_s)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/speaker-hint")
    async def speaker_hint(hint: SpeakerHint) -> dict[str, str]:
        hints.put(hint)
        return {"status": "ok"}

    @app.post("/converse")
    async def converse(request: ConverseRequest) -> ConverseResponse:
        # Агент появится в этапе M2. Пока честно говорим, что ничего не умеем.
        speaker = hints.speaker_for(time.time())
        name = "" if speaker == "guest" else f"{speaker}, "
        return ConverseResponse(speech=f"{name}я пока учусь и ещё ничего не умею.")

    return app
