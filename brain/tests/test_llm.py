import json
from collections.abc import AsyncIterator

from bayun_brain.api import OFFLINE_SPEECH, create_app
from bayun_brain.config import Config
from bayun_brain.llm import Message, SentenceSplitter, clean_for_speech
from fastapi.testclient import TestClient


class FakeChat:
    def __init__(self, deltas: list[str], fail: bool = False) -> None:
        self.deltas = deltas
        self.fail = fail
        self.seen: list[list[Message]] = []

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        self.seen.append(messages)
        if self.fail:
            raise ConnectionError("нет сети")
        for delta in self.deltas:
            yield delta


def test_splitter_cuts_on_sentence_end_and_glues_short_parts() -> None:
    splitter = SentenceSplitter()
    out: list[str] = []
    for delta in ["Да. ", "Резину стоит ", "менять уже сейчас! Завтра ", "минус десять."]:
        out += splitter.feed(delta)
    out += splitter.flush()
    assert out == ["Да. Резину стоит менять уже сейчас!", "Завтра минус десять."]


def test_clean_for_speech_drops_markdown() -> None:
    assert clean_for_speech("**Да**, конечно\n- пункт") == "Да, конечно пункт"


def test_stream_returns_sentences_then_done() -> None:
    chat = FakeChat(["Привет, я Волтер. ", "Чем займёмся?"])
    config = Config.model_validate({"assistant": {"name": "Волтер"}})
    client = TestClient(create_app(config, chat))
    response = client.post("/converse/stream", json={"text": "привет"})
    lines = [json.loads(line) for line in response.text.splitlines()]
    assert [line.get("speech") for line in lines[:-1]] == ["Привет, я Волтер.", "Чем займёмся?"]
    assert lines[-1]["done"] is True
    assert "Волтер" in chat.seen[0][0]["content"]


def test_history_is_kept_within_conversation() -> None:
    chat = FakeChat(["Запомнил."])
    client = TestClient(create_app(Config(), chat))
    first = client.post("/converse", json={"text": "меня зовут Костя"}).json()
    client.post(
        "/converse", json={"text": "как меня зовут?", "conversation_id": first["conversation_id"]}
    )
    roles = [m["role"] for m in chat.seen[1]]
    assert roles == ["system", "user", "assistant", "user"]


def test_llm_failure_gives_offline_phrase() -> None:
    client = TestClient(create_app(Config(), FakeChat([], fail=True)))
    assert client.post("/converse", json={"text": "привет"}).json()["speech"] == OFFLINE_SPEECH


def test_audio_tags_prompt_only_when_asked() -> None:
    chat = FakeChat(["Ха."])
    client = TestClient(create_app(Config(), chat))
    client.post("/converse", json={"text": "шутка"})
    client.post("/converse", json={"text": "шутка", "audio_tags": True})
    assert "[laughs]" not in chat.seen[0][0]["content"]
    assert "[laughs]" in chat.seen[1][0]["content"]


async def test_agent_streams_text_and_runs_tools() -> None:
    from bayun_brain.llm import Completion, Final, ToolCall, agent_sentences

    class Streamy:
        rounds = 0

        async def stream_complete(self, messages, tools):  # type: ignore[no-untyped-def]
            Streamy.rounds += 1
            if Streamy.rounds == 1:
                yield Completion("", [ToolCall("t1", "lamp", "{}")])
                return
            for part in ["Небо синее из-за ", "рассеяния света. ", "Вот и всё."]:
                yield part
            yield Completion("Небо синее из-за рассеяния света. Вот и всё.")

    async def run_tool(call: ToolCall) -> str:
        return "ok"

    said = [s async for s in agent_sentences(Streamy(), [], [], run_tool)]  # type: ignore[arg-type]
    assert said == ["Небо синее из-за рассеяния света.", "Вот и всё."]

    class Lamp(Streamy):
        async def stream_complete(self, messages, tools):  # type: ignore[no-untyped-def]
            yield Completion("", [ToolCall("a", "x", "{}"), ToolCall("b", "x", "{}")])

    async def final(call: ToolCall) -> str:
        return Final("Готово.")

    assert [s async for s in agent_sentences(Lamp(), [], [], final)] == ["Готово."]  # type: ignore[arg-type]
