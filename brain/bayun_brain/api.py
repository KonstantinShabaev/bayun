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

from . import __version__, intents
from .config import Config
from .ha import TOOL, Device, HomeAssistant, devices_prompt, parse_args
from .llm import (
    AUDIO_TAGS_PROMPT,
    NO_HOME_PROMPT,
    SYSTEM_PROMPT,
    WEB_SEARCH_TOOL,
    ChatModel,
    Conversations,
    Final,
    ToolCall,
    agent_sentences,
    reply_sentences,
)
from .video import PROMPT as VIDEO_PROMPT
from .video import TOOLS as VIDEO_TOOLS
from .video import Video

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


def create_app(
    config: Config | None = None,
    chat: ChatModel | None = None,
    home: HomeAssistant | None = None,
    video: Video | None = None,
) -> FastAPI:
    """`chat=None` — заглушка без LLM (тесты, стенд без ключа). `home=None` — без дома,
    `video=None` — без телевизора."""
    config = config or Config()
    app = FastAPI(title="bayun-brain", version=__version__)
    hints = HintStore(window_s=config.speaker_hint_window_s)
    conversations = Conversations(config.assistant.history_turns)

    async def sentences(request: ConverseRequest, conversation_id: str) -> AsyncIterator[str]:
        speaker = hints.speaker_for(time.time())
        if video is not None and (intent := intents.match(request.text)):
            started = time.monotonic()
            outcome = await video.run(intent.tool, intent.args)
            _LOGGER.info(
                "Быстрая команда %s %s за %.2f с: %s",
                intent.tool, intent.args, time.monotonic() - started, outcome,
            )  # fmt: skip
            conversations.remember(conversation_id, request.text, outcome)
            yield outcome
            return
        if chat is None:
            name = "" if speaker == "guest" else f"{speaker}, "
            yield f"{name}я пока учусь и ещё ничего не умею."
            return

        system = SYSTEM_PROMPT.format(name=config.assistant.name)
        devices: list[Device] = []
        if home is not None:
            try:
                devices = await home.devices()
                system += devices_prompt(devices)
            except Exception:
                _LOGGER.exception("Home Assistant не ответил")
                system += "\nHome Assistant сейчас недоступен, устройствами управлять нельзя."
        else:
            system += NO_HOME_PROMPT
        tools = [TOOL] if devices else []
        search = getattr(chat, "search", None)
        if search is not None:
            tools.append(WEB_SEARCH_TOOL)
        if video is not None:
            system += VIDEO_PROMPT
            tools += VIDEO_TOOLS
        if request.audio_tags:
            system += AUDIO_TAGS_PROMPT
        if speaker != "guest":
            system += f"\nСейчас говорит: {speaker}."
        messages = conversations.messages(conversation_id, system, request.text)
        said: list[str] = []
        started = time.monotonic()
        try:

            async def run_tool(call: ToolCall) -> str:
                args = parse_args(call.arguments)
                if call.name == "control_device" and home is not None:
                    outcome = await home.control(devices, args)
                elif (
                    call.name in ("play_video", "stop_video", "control_video", "set_volume")
                    and video is not None
                ):
                    outcome = Final(await video.run(call.name, args))
                elif call.name == "web_search" and search is not None:
                    question = str(args.get("question") or request.text)
                    outcome = Final(await search(question))
                else:
                    outcome = "Нет такого инструмента."
                _LOGGER.info("Инструмент %s %s: %s", call.name, args, outcome)
                return outcome

            source = (
                agent_sentences(chat, messages, tools, run_tool)
                if tools
                else reply_sentences(chat, messages)
            )
            async for sentence in source:
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
