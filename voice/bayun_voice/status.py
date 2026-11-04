"""Страница состояния на экране сервера (потом на ТВ): что слышит, что отвечает.

Стандартная библиотека, без фреймворка: GET / — страница, GET /events — SSE,
GET /api/state — текущее состояние одним JSON,
POST /talk — кнопка «Говорить» вместо имени.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any


class Status:
    def __init__(self) -> None:
        self._lock = threading.Condition()
        self._version = 0
        self.state: dict[str, Any] = {"phase": "idle", "log": [], "timings": {}}
        self.talk = threading.Event()

    def set(self, **fields: Any) -> None:
        with self._lock:
            self.state.update(fields)
            self._version += 1
            self._lock.notify_all()

    def log(self, who: str, text: str) -> None:
        with self._lock:
            entry = {"who": who, "text": text, "ts": time.time()}
            self.state["log"] = [*self.state["log"], entry][-12:]
            self._version += 1
            self._lock.notify_all()

    def wait(self, version: int, timeout: float) -> tuple[int, str]:
        with self._lock:
            self._lock.wait_for(lambda: self._version != version, timeout=timeout)
            return self._version, json.dumps(self.state, ensure_ascii=False)


def serve(status: Status, host: str, port: int) -> ThreadingHTTPServer:
    page = resources.files(__package__).joinpath("status.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page)
            elif self.path == "/api/state":
                _, data = status.wait(-1, timeout=0)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(data.encode())
            elif self.path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                version = -1
                try:
                    while True:
                        version, data = status.wait(version, timeout=15)
                        self.wfile.write(f"data: {data}\n\n".encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            if self.path == "/talk":
                status.talk.set()
                self.send_response(204)
                self.end_headers()
            else:
                self.send_error(404)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
