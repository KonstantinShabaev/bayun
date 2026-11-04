"""HTTP-сервис эффектов: HA (или Баюн) выбирает эффект, сервис крутит его на лампах.

    bayun-lights serve --host 192.168.122.1 --port 8775

    GET  /state            → {"effect": "rainbow" | null, "lamps": [...]}
    POST /effect/<имя>     → запустить эффект (прошлый останавливается)
    POST /stop             → остановить, лампы вернутся в прежнее состояние

Необязательные параметры запроса: lamp (можно несколько), fps.
Если задан BAYUN_LIGHTS_TOKEN, нужен заголовок Authorization: Bearer <токен>.
"""

import json
import logging
import os
import threading
from collections.abc import Callable, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from bayun_lights import devices
from bayun_lights.audio import monitor_chunks
from bayun_lights.effects import EFFECTS
from bayun_lights.engine import AudioThread, run
from bayun_lights.transport import Sink

log = logging.getLogger(__name__)

SinkFactory = Callable[[devices.Lamp], Sink]


class Controller:
    """Один эффект за раз: новый запуск останавливает прошлый и ждёт возврата ламп."""

    def __init__(
        self,
        lamps: Sequence[devices.Lamp],
        make_sink: SinkFactory,
        *,
        fps: float = 12.0,
        audio_device: str = "@DEFAULT_MONITOR@",
    ) -> None:
        self.lamps = list(lamps)
        self.make_sink = make_sink
        self.fps = fps
        self.audio_device = audio_device
        self.effect: str | None = None
        self.error: str | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def state(self) -> dict[str, Any]:
        return {
            "effect": self.effect,
            "error": self.error,
            "effects": sorted(EFFECTS),
            "lamps": [{"name": x.name, "ip": x.ip} for x in self.lamps],
        }

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=10)
            self._thread = None
        self.effect = None

    def start(self, name: str, wanted: list[str], fps: float | None = None) -> None:
        if name not in EFFECTS:
            raise KeyError(name)
        lamps = devices.select(self.lamps, wanted)
        with self._lock:
            self._stop_locked()
            self._stop = threading.Event()
            self.effect, self.error = name, None
            self._thread = threading.Thread(
                target=self._run, args=(name, lamps, fps or self.fps, self._stop), daemon=True
            )
            self._thread.start()

    def _run(self, name: str, lamps: list[devices.Lamp], fps: float, stop: threading.Event) -> None:
        effect = EFFECTS[name]()
        try:
            sinks = [self.make_sink(x) for x in lamps]
            audio = None
            if effect.needs_audio:
                audio = AudioThread(monitor_chunks(self.audio_device, stop))
                audio.start()
            run(effect, sinks, fps=fps, audio=audio, stop=stop)
        except Exception as e:
            log.exception("Эффект %s упал", name)
            self.error = f"{type(e).__name__}: {e}"
        finally:
            if not stop.is_set():
                self.effect = None


def make_handler(ctl: Controller, token: str | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if token and self.headers.get("Authorization") != f"Bearer {token}":
                self._reply(401, {"error": "нужен токен"})
                return False
            return True

        def do_GET(self) -> None:
            if not self._authorized():
                return
            if urlparse(self.path).path == "/state":
                self._reply(200, ctl.state())
            else:
                self._reply(404, {"error": "нет такого адреса"})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            url = urlparse(self.path)
            q = parse_qs(url.query)
            parts = [p for p in url.path.split("/") if p]
            try:
                if parts == ["stop"] or parts == ["effect", "off"]:
                    ctl.stop()
                elif len(parts) == 2 and parts[0] == "effect":
                    fps = float(q["fps"][0]) if "fps" in q else None
                    ctl.start(parts[1], q.get("lamp", []), fps)
                else:
                    self._reply(404, {"error": "нет такого адреса"})
                    return
            except KeyError:
                self._reply(404, {"error": f"нет эффекта {parts[-1]!r}"})
                return
            except ValueError as e:
                self._reply(400, {"error": str(e)})
                return
            self._reply(200, ctl.state())

        def log_message(self, format: str, *args: Any) -> None:
            log.info("%s %s", self.address_string(), format % args)

    return Handler


def serve(ctl: Controller, host: str, port: int) -> None:
    token = os.environ.get("BAYUN_LIGHTS_TOKEN") or None
    httpd = ThreadingHTTPServer((host, port), make_handler(ctl, token))
    log.info("bayun-lights слушает http://%s:%d", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ctl.stop()
        httpd.server_close()
