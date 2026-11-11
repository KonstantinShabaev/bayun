import json
import urllib.error
import urllib.request

from bayun_voice.status import Status, serve


def test_talk_button_and_events() -> None:
    status = Status()
    server = serve(status, "127.0.0.1", 0)
    port = server.server_address[1]
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
        assert "Можно сказать" in page
        remote = urllib.request.urlopen(f"http://127.0.0.1:{port}/remote").read().decode()
        assert "Говорить" in remote
        manifest = json.load(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/manifest.webmanifest")
        )
        assert manifest["start_url"] == "remote"

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


def test_say_and_utter_go_to_inbox() -> None:
    status = Status()
    server = serve(status, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        request = urllib.request.Request(
            base + "/say", data=json.dumps({"text": " пауза "}).encode(), method="POST"
        )
        assert urllib.request.urlopen(request).status == 202
        assert status.inbox.get_nowait() == "пауза"

        pcm = b"\0\0" * 16000
        request = urllib.request.Request(base + "/utter", data=pcm, method="POST")
        assert urllib.request.urlopen(request).status == 202
        assert status.inbox.get_nowait() == pcm

        empty = urllib.request.Request(base + "/say", data=b'{"text": ""}', method="POST")
        try:
            urllib.request.urlopen(empty)
            raise AssertionError("empty text accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 400
    finally:
        server.shutdown()


def test_tv_proxy_only_allowed_paths() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: list[tuple[str, str, bytes]] = []

    class Tv(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def _ok(self) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            seen.append((self.command, self.path, self.rfile.read(n)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": 1}')

        do_GET = do_POST = _ok

    tv = ThreadingHTTPServer(("127.0.0.1", 0), Tv)
    threading.Thread(target=tv.serve_forever, daemon=True).start()
    server = serve(Status(), "127.0.0.1", 0, tv_url=f"http://127.0.0.1:{tv.server_address[1]}")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        urllib.request.urlopen(base + "/tv/status")
        body = json.dumps({"action": "pause"}).encode()
        urllib.request.urlopen(
            urllib.request.Request(base + "/tv/player", data=body, method="POST")
        )
        try:
            urllib.request.urlopen(
                urllib.request.Request(base + "/tv/screen/type", data=b"{}", method="POST")
            )
            raise AssertionError("not allowed path proxied")
        except urllib.error.HTTPError as error:
            assert error.code == 404
        assert seen == [("GET", "/status", b""), ("POST", "/player", body)]
    finally:
        server.shutdown()
        tv.shutdown()
