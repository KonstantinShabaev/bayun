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
