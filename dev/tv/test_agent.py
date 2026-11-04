"""End-to-end checks of tv-agent against the headless dev browser.

    python test_agent.py            (agent on :8791 with --cdp-port 9333 --no-tv --emulate-tv)
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bayun_tv.cdp import Connection, list_pages  # noqa: E402

AGENT = "http://127.0.0.1:8791"
SHOTS = os.path.expanduser("~/bayun/dev/shots")
results: list[tuple[str, bool, str]] = []


def req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(AGENT + path, data, {"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, ok, info))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{info}]" if info else ""), flush=True)


def shot(name: str) -> None:
    c = Connection(list_pages(9333)[0]["webSocketDebuggerUrl"])
    img = c.call("Page.captureScreenshot", format="jpeg", quality=80,
                 clip={"x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 0.5})["data"]
    with open(f"{SHOTS}/{name}.jpg", "wb") as f:
        f.write(base64.b64decode(img))
    c.close()


def player() -> dict:
    return req("GET", "/status")[1].get("player") or {}


def main() -> None:
    os.makedirs(SHOTS, exist_ok=True)

    # --- YouTube search and play ------------------------------------------------
    st, r = req("POST", "/youtube/search", {"q": "nature documentary 4k", "n": 6})
    check("youtube/search returns results", st == 200 and len(r.get("results", [])) >= 3,
          f"{len(r.get('results', []))} results, {r.get('ms')} ms")
    shot("api-youtube-search-hints")
    st, r = req("POST", "/youtube/play", {"n": 2})
    check("youtube/play n=2", st == 200, f"{r.get('title', r)} {r.get('ms')} ms")
    for _ in range(40):
        time.sleep(0.5)
        if player().get("playing"):
            break
    check("youtube video plays", bool(player().get("playing")))

    # --- player controls, timed ------------------------------------------------
    for action, value, verify in [
        ("pause", None, lambda p: p.get("playing") is False),
        ("play", None, lambda p: p.get("playing") is True),
        ("volume", 40, lambda p: p.get("volume") == 40),
        ("volume", 100, lambda p: p.get("volume") == 100),
        ("seek", 30, lambda p: True),
    ]:
        before = player().get("currentTime", 0)
        body = {"action": action} | ({"value": value} if value is not None else {})
        st, r = req("POST", "/player", body)
        time.sleep(0.4)
        p = player()
        ok = st == 200 and verify(p)
        if action == "seek":
            ok = ok and p.get("currentTime", 0) - before > 20
        check(f"player {action} {value or ''}".strip(), ok, f"{r.get('ms')} ms")
    st, r = req("POST", "/player", {"action": "seek"})
    check("player seek without value -> 400", st == 400, r.get("error", ""))
    st, r = req("POST", "/player", {"action": "dance"})
    check("unknown player action -> 400", st == 400, r.get("error", ""))

    # --- screen control ----------------------------------------------------------
    st, r = req("POST", "/screen/list", {"hints": True})
    items = r.get("items", [])
    check("screen/list with hints", st == 200 and len(items) > 5, f"{len(items)} items, {r.get('ms')} ms")
    shot("api-screen-hints")
    st, r = req("POST", "/screen/hints", {"on": False})
    check("screen/hints off", st == 200, f"{r.get('ms')} ms")
    st, r = req("POST", "/screen/scroll", {"dir": "down", "amount": 1})
    check("screen/scroll down", st == 200, f"{r.get('ms')} ms")
    st, r = req("POST", "/screen/scroll", {"dir": "up", "amount": 1})
    check("screen/scroll up", st == 200, f"{r.get('ms')} ms")
    st, r = req("POST", "/screen/key", {"key": "k"})  # YouTube: toggle play
    time.sleep(0.5)
    check("screen/key k toggles playback", st == 200 and player().get("playing") is False, f"{r.get('ms')} ms")
    req("POST", "/screen/key", {"key": "k"})
    st, r = req("POST", "/screen/key", {"key": "nosuchkey"})
    check("unknown key -> 400", st == 400)
    st, r = req("POST", "/screen/click_text", {"text": "zzz-no-such-element-zzz"})
    check("click_text without match -> 404", st == 404)

    # --- typing into YouTube search box via screen API ----------------------------
    req("POST", "/open", {"url": "https://www.youtube.com/"})
    time.sleep(4)
    st, r = req("POST", "/screen/click_text", {"text": "Search"})
    if st != 200:
        st, r = req("POST", "/screen/click_text", {"text": "Поиск"})
    check("click_text Search box", st == 200, str(r.get("text", r)))
    st, r = req("POST", "/screen/type", {"text": "nasa live"})
    check("screen/type", st == 200, f"{r.get('ms')} ms")
    st, r = req("POST", "/screen/key", {"key": "enter"})
    time.sleep(4)
    url = req("GET", "/status")[1].get("page", {}).get("url", "")
    check("typed search submitted", "search_query=nasa" in url, url)
    st, r = req("POST", "/screen/back", {})
    time.sleep(2)
    check("screen/back", st == 200)

    # --- VK Video --------------------------------------------------------------
    st, r = req("POST", "/vk/search", {"q": "космос 4k", "n": 6})
    check("vk/search returns results", st == 200 and len(r.get("results", [])) >= 3,
          f"{len(r.get('results', []))} results, {r.get('ms')} ms")
    shot("api-vk-search-hints")
    st, r = req("POST", "/vk/play", {"n": 1})
    check("vk/play n=1", st == 200, f"{r.get('title', r)} {r.get('ms')} ms")
    for _ in range(40):
        time.sleep(0.5)
        if player().get("playing"):
            break
    p = player()
    check("vk video plays (shadow DOM player)", bool(p.get("playing")), f"t={p.get('currentTime')}")
    st, r = req("POST", "/player", {"action": "pause"})
    time.sleep(0.4)
    check("vk pause", st == 200 and player().get("playing") is False, f"{r.get('ms')} ms")
    st, r = req("POST", "/player", {"action": "play"})
    time.sleep(0.4)
    check("vk play", st == 200 and player().get("playing") is True, f"{r.get('ms')} ms")
    st, r = req("POST", "/player", {"action": "seek", "value": 60})
    check("vk seek +60", st == 200, f"{r.get('ms')} ms")
    shot("api-vk-playing")

    # --- overlay with level ------------------------------------------------------
    st, r = req("POST", "/overlay", {"phase": "listening", "text": "Джарвис, поставь на паузу", "level": 0.8})
    check("overlay listening with level", st == 200, f"{r.get('ms')} ms")
    time.sleep(1)
    shot("api-overlay-over-vk")
    req("POST", "/overlay", {"phase": "idle", "text": ""})

    # --- timings -------------------------------------------------------------------
    times = []
    for _ in range(10):
        t = time.perf_counter()
        req("POST", "/player", {"action": "status"})
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    check("round trip /player status < 150 ms", times[5] < 150, f"median {times[5]:.1f} ms, max {times[-1]:.1f} ms")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; failed: {failed}" if failed else ""))


if __name__ == "__main__":
    main()
