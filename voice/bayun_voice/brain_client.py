"""Клиент bayun-brain: /converse/stream отдаёт ответ по предложениям."""

import json
from collections.abc import Iterator
from dataclasses import dataclass

import httpx


@dataclass
class Turn:
    conversation_id: str | None = None
    continue_conversation: bool = False


class BrainClient:
    def __init__(
        self, url: str, timeout_s: float, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._client = httpx.Client(base_url=url, timeout=timeout_s, transport=transport)

    def converse(self, text: str, turn: Turn) -> Iterator[str]:
        """Отдаёт предложения по мере готовности и обновляет `turn` в конце."""
        body = {"text": text, "conversation_id": turn.conversation_id, "device_id": "server"}
        with self._client.stream("POST", "/converse/stream", json=body) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if data.get("done"):
                    turn.conversation_id = data.get("conversation_id")
                    turn.continue_conversation = bool(data.get("continue_conversation"))
                elif speech := data.get("speech"):
                    yield str(speech)
