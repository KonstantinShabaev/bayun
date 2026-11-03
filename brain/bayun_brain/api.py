"""HTTP API: /converse, /converse/stream, /speaker-hint, /health (docs/PROJECT_SPEC.md, §4.2)."""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import __version__
from .config import Config
from .llm import AUDIO_TAGS_PROMPT, SYSTEM_PROMPT, ChatModel, Conversations, reply_sentences

_LOGGER = logging.getLogger(__name__)

OFFLINE_SPEECH = "Облако сейчас недоступно, попробуй чуть позже."


class ConverseRequest(BaseModel):
    text: str
    conversation_id: str | None = None
    device_id: str | None = None
    language: str = "ru"
    # Голос понимает теги эмоций вроде [laughs].
    audio_tags: bool = False


class ConverseResponse(BaseModel):
    speech: str
    continue_conversation: bool = False
    conversation_id: str | None = None


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


def create_app(config: Config | None = None, chat: ChatModel | None = None) -> FastAPI:
    """`chat=None` — заглушка без LLM (тесты, стенд без ключа)."""
    config = config or Config()
    app = FastAPI(title="bayun-brain", version=__version__)
    hints = HintStore(window_s=config.speaker_hint_window_s)
    conversations = Conversations(config.assistant.history_turns)

    async def sentences(request: ConverseRequest, conversation_id: str) -> AsyncIterator[str]:
        speaker = hints.speaker_for(time.time())
        if chat is None:
            name = "" if speaker == "guest" else f"{speaker}, "
            yield f"{name}я пока учусь и ещё ничего не умею."
            return

        system = SYSTEM_PROMPT.format(name=config.assistant.name)
        if request.audio_tags:
            system += AUDIO_TAGS_PROMPT
        if speaker != "guest":
            system += f"\nСейчас говорит: {speaker}."
        messages = conversations.messages(conversation_id, system, request.text)
        said: list[str] = []
        started = time.monotonic()
        try:
            async for sentence in reply_sentences(chat, messages):
                if not said:
                    _LOGGER.info("Первая фраза через %.2f с", time.monotonic() - started)
                said.append(sentence)
                yield sentence
        except Exception:
            _LOGGER.exception("LLM не ответила")
            if not said:
                yield OFFLINE_SPEECH
            return
        conversations.remember(conversation_id, request.text, " ".join(said))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/speaker-hint")
    async def speaker_hint(hint: SpeakerHint) -> dict[str, str]:
        hints.put(hint)
        return {"status": "ok"}

    @app.post("/converse")
    async def converse(request: ConverseRequest) -> ConverseResponse:
        conversation_id = request.conversation_id or uuid.uuid4().hex
        speech = " ".join([s async for s in sentences(request, conversation_id)])
        return ConverseResponse(
            speech=speech,
            continue_conversation=chat is not None,
            conversation_id=conversation_id,
        )

    @app.post("/converse/stream")
    async def converse_stream(request: ConverseRequest) -> StreamingResponse:
        """NDJSON: по строке {"speech": ...} на предложение, в конце {"done": true, ...}."""
        conversation_id = request.conversation_id or uuid.uuid4().hex

        async def lines() -> AsyncIterator[str]:
            async for sentence in sentences(request, conversation_id):
                yield json.dumps({"speech": sentence}, ensure_ascii=False) + "\n"
            done = {
                "done": True,
                "conversation_id": conversation_id,
                "continue_conversation": chat is not None,
            }
            yield json.dumps(done) + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    return app
