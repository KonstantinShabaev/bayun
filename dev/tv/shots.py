"""Screenshots of the overlay variants over a paused YouTube frame in the headless dev browser.

    python shots.py [variant ...]      (agent on :8791, headless Chromium on :9333)
"""
import base64
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bayun_tv.cdp import Connection, list_pages  # noqa: E402

OUT = os.path.expanduser("~/bayun/dev/shots")
AGENT = "http://127.0.0.1:8791"
FAKE_FULLSCREEN = """(() => {
  const s = document.createElement('style');
  s.textContent = '#movie_player{position:fixed!important;inset:0!important;width:100vw!important;' +
    'height:100vh!important;z-index:9999!important} #movie_player video{width:100vw!important;' +
    'height:100vh!important;left:0!important;top:0!important;object-fit:cover}' +
    '.ytp-chrome-bottom,.ytp-chrome-top,.ytp-gradient-bottom,.ytp-gradient-top,.ytp-pause-overlay,' +
    '.ytp-ce-element,.ytp-watermark{display:none!important}';
  document.head.appendChild(s);
})()"""
TEXT = {
    "listening": "Джарвис, включи что-нибудь про космос",
    "thinking": "Джарвис, включи что-нибудь про космос",
    "speaking": "Включаю подборку про космос в 4K. Приятного просмотра!",
    "error": "Не расслышал, повторите",
}


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(AGENT + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def main() -> None:
    c = Connection(list_pages(9333)[0]["webSocketDebuggerUrl"])
    c.call("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=2, mobile=False)
    post("/player", {"action": "seek", "value": 40})
    time.sleep(2)
    post("/player", {"action": "pause"})
    c.evaluate(FAKE_FULLSCREEN)
    time.sleep(1)
    os.makedirs(OUT, exist_ok=True)
    for variant in sys.argv[1:] or ["aurora", "orb", "edge"]:
        post("/overlay", {"variant": variant})
        post("/overlay", {"phase": "listening", "text": "…"})  # warm up the canvas and transitions
        time.sleep(1.0)
        for phase, text in TEXT.items():
            post("/overlay", {"phase": phase, "text": text})
            c.evaluate("window.__bayunOverlay.settle(1.5)")
            time.sleep(0.6)
            img = c.call("Page.captureScreenshot", format="jpeg", quality=85,
                         clip={"x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 0.5})["data"]
            path = f"{OUT}/{variant}-{phase}.jpg"
            with open(path, "wb") as f:
                f.write(base64.b64decode(img))
            print(path)
    post("/overlay", {"phase": "idle", "text": ""})


if __name__ == "__main__":
    main()
