"""Что Баюн говорит сам: подписка на SSE brain /announcements (таймеры, итоги Claude Code).

Поток читается в фоне и переподключается, если brain перезапустили. Говорит цикл,
когда свободен: между разговорами, не перебивая.
"""

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass

import httpx

_LOGGER = logging.getLogger(__name__)


@dataclass
class Announcement:
    id: str
    text: str
    expects_reply: bool = False
    conversation_id: str | None = None


class Announcer:
    def __init__(
        self, url: str, transport: httpx.BaseTransport | None = None, retry_s: float = 5.0
    ) -> None:
        self._url = url.rstrip("/") + "/announcements"
        self._transport = transport
        self._retry_s = retry_s
        self._queue: queue.Queue[Announcement] = queue.Queue()
        # Вопросы, закрытые до того, как их успели сказать.
        self._resolved: set[str] = set()
        self._said: set[str] = set()

    def start(self) -> None:
        threading.Thread(target=self._run, name="announcer", daemon=True).start()

    def next(self) -> Announcement | None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return None
            if item.id in self._resolved or item.id in self._said:
                continue
            self._said.add(item.id)
            return item

    def feed(self, line: str) -> None:
        """Одна строка SSE."""
        if not line.startswith("data:"):
            return
        try:
            event = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            return
        if event.get("type") == "resolved":
            self._resolved.add(str(event.get("id")))
        elif event.get("type") == "announcement" and event.get("text"):
            self._queue.put(
                Announcement(
                    str(event.get("id")),
                    str(event["text"]),
                    bool(event.get("expects_reply")),
                    event.get("conversation_id"),
                )
            )

    def _run(self) -> None:
        while True:
            try:
                with (
                    httpx.Client(timeout=httpx.Timeout(5, read=60), transport=self._transport) as c,
                    c.stream("GET", self._url) as response,
                ):
                    response.raise_for_status()
                    _LOGGER.info("Слушаю объявления brain")
                    for line in response.iter_lines():
                        self.feed(line)
            except Exception as error:
                _LOGGER.debug("Объявления недоступны: %s", error)
            time.sleep(self._retry_s)
