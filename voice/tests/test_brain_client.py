import json

import httpx
from bayun_voice.brain_client import BrainClient, Turn


def test_converse_streams_sentences_and_keeps_conversation() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        lines = [
            {"speech": "Первая фраза."},
            {"speech": "Вторая."},
            {"done": True, "conversation_id": "c1", "continue_conversation": True},
        ]
        body = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
        return httpx.Response(200, text=body)

    client = BrainClient("http://brain", 5, transport=httpx.MockTransport(handler))
    turn = Turn()
    assert list(client.converse("привет", turn)) == ["Первая фраза.", "Вторая."]
    assert turn.conversation_id == "c1"
    assert turn.continue_conversation

    list(client.converse("ещё", turn))
    assert seen[1]["conversation_id"] == "c1"
