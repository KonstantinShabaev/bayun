import json
import urllib.request

from bayun_voice.status import Status, serve


def test_talk_button_and_events() -> None:
    status = Status()
    server = serve(status, "127.0.0.1", 0)
    port = server.server_address[1]
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
        assert "Говорить" in page

        request = urllib.request.Request(f"http://127.0.0.1:{port}/talk", method="POST")
        urllib.request.urlopen(request)
        assert status.talk.is_set()

        status.log("user", "привет")
        _, data = status.wait(-1, timeout=1)
        assert json.loads(data)["log"][0]["text"] == "привет"
    finally:
        server.shutdown()


def test_level_is_throttled_and_bounded() -> None:
    import numpy as np
    from bayun_voice.status import Status

    status = Status()
    loud = (np.ones(1280) * 30000).astype(np.int16).tobytes()
    status.level(loud)
    assert status.state["level"] == 1.0
    status.level(b"\0\0" * 1280)
    assert status.state["level"] == 1.0  # второй раз за 0,1 с не обновляется


def test_brain_proxy() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: list[tuple[str, str]] = []

    class Brain(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def _ok(self) -> None:
            seen.append((self.command, self.path))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": 1}')

        do_GET = do_DELETE = _ok

    brain = ThreadingHTTPServer(("127.0.0.1", 0), Brain)
    threading.Thread(target=brain.serve_forever, daemon=True).start()
    server = serve(Status(), "127.0.0.1", 0, f"http://127.0.0.1:{brain.server_address[1]}")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert json.loads(urllib.request.urlopen(base + "/brain/api/dashboard").read()) == {"ok": 1}
        urllib.request.urlopen(urllib.request.Request(base + "/brain/api/facts/3", method="DELETE"))
        assert seen == [("GET", "/api/dashboard"), ("DELETE", "/api/facts/3")]
    finally:
        server.shutdown()
        brain.shutdown()
