"""Страница состояния на экране сервера (потом на ТВ): что слышит, что отвечает.

Стандартная библиотека, без фреймворка: GET / — страница, GET /events — SSE,
GET /api/state — текущее состояние одним JSON,
POST /talk — кнопка «Говорить» вместо имени,
GET и DELETE /brain/api/… и GET /brain/health — к brain (таймеры, память, погода, связь
с облаком): brain слушает только 127.0.0.1, а экран открывают и с телефона.
GET /remote — пульт для телефона (PWA: /manifest.webmanifest, /icon.svg),
POST /say {"text"} — команда текстом с пульта, как будто её сказали,
POST /utter — голос с микрофона телефона (16 кГц s16le), только если страница открыта по https,
GET /tv/status и POST /tv/… — к tv-agent (видео, громкость ТВ), он тоже слушает только 127.0.0.1.
"""

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any

import numpy as np


class Status:
    def __init__(self) -> None:
        self._lock = threading.Condition()
        self._version = 0
        # boot: экран перезагружает себя, когда голосовой цикл перезапустили (новая версия).
        self.state: dict[str, Any] = {
            "phase": "idle",
            "log": [],
            "timings": {},
            "level": 0.0,
            "boot": time.time(),
        }
        self.talk = threading.Event()
        # С пульта: str — текст команды, bytes — запись с микрофона телефона.
        self.inbox: queue.Queue[str | bytes] = queue.Queue(maxsize=4)
        self._level_at = 0.0

    def set(self, **fields: Any) -> None:
        with self._lock:
            self.state.update(fields)
            self._version += 1
            self._lock.notify_all()

    def level(self, pcm: bytes) -> None:
        """Громкость того, что слышно или говорится сейчас, 0..1, для анимации.
        Не чаще 10 раз в секунду, чтобы не заваливать SSE."""
        now = time.monotonic()
        if now - self._level_at < 0.1:
            return
        self._level_at = now
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0
        # Речь у микрофона ~1000–5000 rms: корень сглаживает тихое и громкое.
        self.set(level=round(min(1.0, (rms / 6000) ** 0.5), 2))

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


# Что пульт может попросить у tv-agent: только видео, экран и ТВ, ничего про CDP напрямую.
TV_POST = {
    "/player",
    "/fullscreen",
    "/home",
    "/input",
    "/screen/key",
    "/screen/back",
    "/screen/scroll",
    "/youtube/search",
    "/youtube/play",
    "/vk/search",
    "/vk/play",
    "/tv/volume",
    "/tv/power",
    "/tv/key",
}
MAX_UTTER = 16000 * 2 * 30  # 30 с речи

ICON = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
    b'<rect width="512" height="512" rx="112" fill="#0b0d12"/>'
    b'<circle cx="256" cy="256" r="150" fill="#3fdc9a"/>'
    b'<circle cx="210" cy="205" r="60" fill="#fff" opacity=".35"/></svg>'
)


def manifest() -> bytes:
    return json.dumps(
        {
            "name": "Баюн",
            "short_name": "Баюн",
            "start_url": "remote",
            "scope": "./",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#0b0d12",
            "theme_color": "#0b0d12",
            "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        },
        ensure_ascii=False,
    ).encode()


def serve(
    status: Status,
    host: str,
    port: int,
    brain_url: str = "http://127.0.0.1:8765",
    tv_url: str = "http://127.0.0.1:8790",
) -> ThreadingHTTPServer:
    files = resources.files(__package__)
    page = files.joinpath("status.html").read_bytes()
    remote = files.joinpath("remote.html").read_bytes()
    static = {
        "/": ("text/html; charset=utf-8", page),
        "/remote": ("text/html; charset=utf-8", remote),
        "/manifest.webmanifest": ("application/manifest+json", manifest()),
        "/icon.svg": ("image/svg+xml", ICON),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _send(self, code: int, kind: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _body(self, limit: int = 4096) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("too large")
            return self.rfile.read(n) if n else b""

        def _brain(self, method: str) -> None:
            self._proxy(brain_url, self.path[len("/brain") :], method)

        def _proxy(self, base: str, path: str, method: str, data: bytes | None = None) -> None:
            request = urllib.request.Request(
                base.rstrip("/") + path,
                data=data,
                method=method,
                headers={"Content-Type": "application/json"} if data is not None else {},
            )
            try:
                # Поиск видео в tv-agent ждёт загрузки страницы до ~8 с.
                with urllib.request.urlopen(request, timeout=20) as response:
                    body, code = response.read(), response.status
            except urllib.error.HTTPError as error:
                body, code = error.read(), error.code
            except OSError:
                body, code = b'{"error": "unavailable"}', 502
            self._send(code, "application/json; charset=utf-8", body)

        def do_DELETE(self) -> None:
            if self.path.startswith("/brain/api/"):
                self._brain("DELETE")
            else:
                self.send_error(404)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if self.path.startswith("/brain/api/") or path == "/brain/health":
                self._brain("GET")
            elif path == "/tv/status":
                self._proxy(tv_url, "/status", "GET")
            elif path in static:
                self._send(200, *static[path])
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
            elif self.path == "/say":
                try:
                    text = str(json.loads(self._body() or b"{}").get("text", "")).strip()[:300]
                except ValueError:
                    text = ""
                if not text:
                    self.send_error(400)
                    return
                self._inbox(text)
            elif self.path == "/utter":
                try:
                    pcm = self._body(MAX_UTTER)
                except ValueError:
                    self.send_error(413)
                    return
                if len(pcm) < 16000 * 2 * 0.3 or len(pcm) % 2:
                    self.send_error(400)
                    return
                self._inbox(pcm)
            elif self.path.startswith("/tv/") and self.path[len("/tv") :] in TV_POST:
                try:
                    data = self._body()
                except ValueError:
                    self.send_error(413)
                    return
                self._proxy(tv_url, self.path[len("/tv") :], "POST", data or b"{}")
            else:
                self.send_error(404)

        def _inbox(self, item: str | bytes) -> None:
            try:
                status.inbox.put_nowait(item)
            except queue.Full:
                self._send(429, "application/json", b'{"error": "busy"}')
                return
            self.send_response(202)
            self.end_headers()

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
