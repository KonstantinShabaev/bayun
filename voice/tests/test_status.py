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
