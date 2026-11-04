"""tv-agent: one long-lived process that drives the living-room TV.

Holds a CDP session with the kiosk Chromium and a connection to the Android TV,
follows the voice loop state for the overlay, and serves a small HTTP+JSON API
on 127.0.0.1:8790 so every command is a single local round trip.

    python -m bayun_tv.agent [--port 8790] [--cdp-port 9222] [--no-tv] [--no-status]
"""

import argparse
import http.client
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from bayun_tv.cdp import Connection, list_pages

HERE = os.path.dirname(os.path.abspath(__file__))
HOME_URL = "http://127.0.0.1:8770/"
TV_CONF = os.path.expanduser("~/.config/bayun/androidtv/tv.conf")
TV_HOST = "fe80::4044:c4ff:fef6:3ea0%wlp0s20f3"
# HDMI 2 of the Changhong TV (MediaTek TIS hardware id HW3)
LAPTOP_INPUT_URI = "content://android.media.tv/passthrough/com.mediatek.tis%2F.HdmiInputService%2FHW3"
KIOSK_ARGS = [
    "chromium", "--kiosk", f"--app={HOME_URL}",
    f"--user-data-dir={os.path.expanduser('~/.config/bayun/tv-chromium')}",
    "--remote-debugging-port=9222", "--remote-debugging-address=127.0.0.1",
    "--enable-features=VaapiVideoDecodeLinuxGL", "--ozone-platform=wayland",
    "--no-first-run", "--noerrdialogs", "--disable-session-crashed-bubble",
    "--autoplay-policy=no-user-gesture-required",
]

PAGE_SCRIPTS = "\n".join(open(os.path.join(HERE, name), encoding="utf-8").read()
                         for name in ("overlay.js", "screen.js"))

VK_VIDEO = re.compile(r"https?://(?:www\.)?(?:vkvideo\.ru|vk\.com|vk\.ru)/video-?\d+_\d+")
YOUTUBE_ID = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)|youtu\.be/)([\w-]{11})")

# Runs inside a YouTube page for its whole life.
YOUTUBE_PAGE_JS = """(() => {
  if (window.__bayunYT) return;
  window.__bayunYT = true;
  try {
    localStorage.setItem('yt-player-sticky-caption',
      JSON.stringify({data: 'false', expiration: Date.now() + 3e10, creation: Date.now()}));
  } catch (e) {}
  setInterval(() => {
    const p = document.getElementById('movie_player');
    if (!p) return;
    const v = p.querySelector('video');
    if (p.classList.contains('ad-showing')) {
      const skip = document.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern');
      if (skip) skip.click();
      else if (v && isFinite(v.duration) && v.duration > 0) { v.muted = true; v.currentTime = v.duration; }
    } else if (v && v.muted && v.dataset.bayunMuted !== '1') {
      v.muted = false;  // never leave a video muted after an ad
      if (p.isMuted && p.isMuted()) p.unMute();
    }
  }, 300);
})()"""

# Runs inside a VK Video page: skips ads (the player lives in a shadow root) and never leaves it muted.
VK_PAGE_JS = """(() => {
  if (window.__bayunVK) return;
  window.__bayunVK = true;
  setInterval(() => {
    const s = window.__bayunScreen;
    if (!s) return;
    s.clickDeep(/^(пропустить|skip)/i);
    const v = s.video();
    if (!v) return;
    // an ad is a second, shorter <video> that plays while the main one waits
    for (const ad of s.videos()) {
      if (ad !== v && !ad.paused && isFinite(ad.duration) && ad.duration > 0 && ad.duration < 180) {
        ad.muted = true;
        ad.currentTime = ad.duration;
      }
    }
    if (v.muted && v.dataset.bayunMuted !== '1') v.muted = false;
  }, 500);
})()"""

REJECT_CONSENT_JS = """(() => {
  const btn = [...document.querySelectorAll('button, [role=button], input[type=submit]')]
    .find(b => b.offsetHeight > 0 && /reject all|отклонить все/i.test(b.innerText || b.getAttribute('aria-label') || ''));
  if (!btn) return false;
  btn.click();
  return true;
})()"""

# Before typing: keep the focused field, otherwise focus the first visible search box or text field.
FOCUS_TEXT_FIELD_JS = """(() => {
  const a = document.activeElement;
  if (a && (a.isContentEditable || /^(input|textarea)$/i.test(a.tagName))) return true;
  const f = [...document.querySelectorAll('input[type=search], input[name*="search" i], input[name="q"], '
    + 'input:not([type]), input[type=text], textarea, [contenteditable=true]')].find(e => e.getClientRects().length);
  if (!f) return false;
  f.focus();
  return true;
})()"""

PLAYER_JS = {
    "play": "v.play().then(() => true)",
    "pause": "(v.pause(), true)",
    "toggle": "(v.paused ? v.play() : v.pause(), true)",
    "seek": "(v.currentTime = Math.max(0, v.currentTime + ARG), true)",
    "volume": "(v.muted = false, v.volume = Math.min(1, Math.max(0, ARG / 100)), true)",
    "mute": "(v.muted = true, v.dataset.bayunMuted = '1', true)",
    "unmute": "(v.muted = false, v.dataset.bayunMuted = '', true)",
    "next": "(() => { const b = document.querySelector('.ytp-next-button');"
            " if (!b || b.offsetParent === null) return false; b.click(); return true; })()",
    "status": "({playing: !v.paused && !v.ended, currentTime: v.currentTime, duration: v.duration,"
              " volume: Math.round(v.volume * 100), muted: v.muted, title: document.title, url: location.href})",
}

# name -> (key, code, windowsVirtualKeyCode)
KEYS = {
    "enter": ("Enter", "Enter", 13), "escape": ("Escape", "Escape", 27), "esc": ("Escape", "Escape", 27),
    "space": (" ", "Space", 32), "tab": ("Tab", "Tab", 9), "backspace": ("Backspace", "Backspace", 8),
    "up": ("ArrowUp", "ArrowUp", 38), "down": ("ArrowDown", "ArrowDown", 40),
    "left": ("ArrowLeft", "ArrowLeft", 37), "right": ("ArrowRight", "ArrowRight", 39),
    "page_up": ("PageUp", "PageUp", 33), "page_down": ("PageDown", "PageDown", 34),
    "home": ("Home", "Home", 36), "end": ("End", "End", 35),
}
for _ch in "abcdefghijklmnopqrstuvwxyz":
    KEYS[_ch] = (_ch, f"Key{_ch.upper()}", ord(_ch.upper()))
for _d in "0123456789":
    KEYS[_d] = (_d, f"Digit{_d}", ord(_d))

TV_KEYS = {"up": "KEYCODE_VOLUME_UP", "down": "KEYCODE_VOLUME_DOWN", "mute": "KEYCODE_VOLUME_MUTE"}


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Kiosk:
    """The kiosk page: reconnects when the tab or the browser goes away."""

    def __init__(self, cdp_port: int, launch: bool, emulate_tv: bool = False) -> None:
        self.cdp_port = cdp_port
        self.emulate_tv = emulate_tv
        self.launch = launch
        self._conn: Connection | None = None
        self._lock = threading.Lock()
        self.overlay_state = ("idle", "")
        self.variant = "aurora"
        self.hints_on = False

    def _start_browser(self) -> None:
        env_uid = f"/run/user/{os.getuid()}"
        subprocess.run(["systemd-run", "--user", "--unit=bayun-screen", "--collect",
                        f"-EXDG_RUNTIME_DIR={env_uid}", "-EWAYLAND_DISPLAY=wayland-0", *KIOSK_ARGS],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def conn(self) -> Connection:
        with self._lock:
            if self._conn is not None and not self._conn.closed.is_set():
                return self._conn
            pages = self._pages()
            if not pages and self.launch:
                self._start_browser()
                for _ in range(60):
                    time.sleep(0.25)
                    pages = self._pages()
                    if pages:
                        break
            if not pages:
                raise ApiError(503, f"kiosk Chromium is not reachable on CDP port {self.cdp_port}")
            c = Connection(pages[0]["webSocketDebuggerUrl"])
            c.call("Page.enable")
            if self.emulate_tv:  # headless dev browser: same CSS viewport as the 4K TV at 200 %
                c.call("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=2, mobile=False)
            c.call("Page.addScriptToEvaluateOnNewDocument", source=PAGE_SCRIPTS)
            c.on_event(self._on_event)
            self._conn = c
        self._install()
        return c

    def _pages(self) -> list[dict]:
        try:
            return list_pages(self.cdp_port)
        except OSError:
            return []

    def _on_event(self, method: str, params: dict) -> None:
        if method == "Page.loadEventFired" or (
                method == "Page.frameNavigated" and not params.get("frame", {}).get("parentId")):
            threading.Thread(target=self._install, daemon=True).start()

    def _install(self) -> None:
        """(Re)apply page scripts and the current overlay state after a navigation."""
        c = self._conn
        if c is None:
            return
        phase, text = self.overlay_state
        try:
            c.evaluate(f"{PAGE_SCRIPTS};\n"
                       f"window.__bayunOverlay && (window.__bayunOverlay.variant({json.dumps(self.variant)}),"
                       f" window.__bayunOverlay.set({json.dumps(phase)}, {json.dumps(text, ensure_ascii=False)}))")
        except Exception:  # noqa: BLE001 - the page may be mid-navigation; the next event retries
            pass

    def evaluate(self, expr: str, await_promise: bool = False, timeout: float = 10) -> Any:
        return self.conn().evaluate(expr, timeout=timeout, await_promise=await_promise)

    def call(self, method: str, **params: Any) -> dict:
        return self.conn().call(method, **params)

    def overlay(self, phase: str, text: str, level: float | None = None) -> None:
        self.overlay_state = (phase, text)
        if self._conn is None or self._conn.closed.is_set():
            return  # nothing to draw on yet; applied on the next connect
        js = f"window.__bayunOverlay && window.__bayunOverlay.set({json.dumps(phase)}, " \
             f"{json.dumps(text, ensure_ascii=False)})"
        if level is not None:
            js += f"; window.__bayunOverlay && window.__bayunOverlay.level({float(level)})"
        try:
            self._conn.evaluate(js, timeout=2)
        except Exception:  # noqa: BLE001
            pass

    # -- input ------------------------------------------------------------

    def click(self, x: float, y: float, count: int = 1) -> None:
        for n in range(1, count + 1):
            for kind in ("mousePressed", "mouseReleased"):
                self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=n)

    def key(self, name: str) -> None:
        name = name.lower()
        if name not in KEYS:
            raise ApiError(400, f"unknown key {name!r}; known: {', '.join(sorted(KEYS))}")
        key, code, vk = KEYS[name]
        text = key if len(key) == 1 else ("\r" if key == "Enter" else "")
        self.call("Input.dispatchKeyEvent", type="keyDown", key=key, code=code, text=text,
                  windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)
        self.call("Input.dispatchKeyEvent", type="keyUp", key=key, code=code,
                  windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)


class Agent:
    def __init__(self, args: argparse.Namespace) -> None:
        self.kiosk = Kiosk(args.cdp_port, launch=args.cdp_port == 9222, emulate_tv=args.emulate_tv)
        self.tv = None
        if not args.no_tv:
            from bayun_tv.atv import AndroidTV
            self.tv = AndroidTV(TV_HOST, self._input_uri())
        self.kiosk.variant = args.variant
        self.results: list[dict] = []
        if not args.no_status:
            threading.Thread(target=self._follow_status, daemon=True).start()

    @staticmethod
    def _input_uri() -> str:
        try:
            for line in open(TV_CONF):
                if line.startswith("laptop_input_uri="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            pass
        return LAPTOP_INPUT_URI

    # -- voice loop state ----------------------------------------------------

    def _follow_status(self) -> None:
        while True:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", 8770, timeout=30)
                conn.request("GET", "/events")
                for raw in conn.getresponse():
                    line = raw.decode("utf-8", "replace").strip()
                    if line.startswith("data:"):
                        state = json.loads(line[5:])
                        log = state.get("log") or []
                        last = log[-1] if log and log[-1].get("who") in ("user", "bayun") else {}
                        self.kiosk.overlay(state.get("phase", "idle"), last.get("text", ""), state.get("level"))
            except (OSError, ValueError, http.client.HTTPException):
                pass
            self.kiosk.overlay("idle", "")
            time.sleep(2)

    # -- helpers -------------------------------------------------------------

    def _to_tv(self) -> str | None:
        if self.tv is None:
            return None
        try:
            return self.tv.to_laptop()
        except Exception as e:  # noqa: BLE001 - the page is open anyway; report the TV part
            return f"error: {e}"

    def _after_youtube_open(self, url: str) -> None:
        k = self.kiosk
        settled = 0
        for _ in range(40):  # cookie consent may pop up a moment after the player
            time.sleep(0.25)
            try:
                if k.evaluate(REJECT_CONSENT_JS):
                    time.sleep(1.0)
                    if "consent." in (k.evaluate("location.host") or ""):
                        k.call("Page.navigate", url=url)
                    break
                if k.evaluate("!!document.querySelector('#movie_player video')"):
                    settled += 1
                    if settled >= 8:  # 2 s with a player and no dialog
                        break
            except Exception:  # noqa: BLE001
                pass
        for _ in range(40):
            time.sleep(0.25)
            try:
                k.evaluate(YOUTUBE_PAGE_JS)
                state = k.evaluate("(() => { const v = document.querySelector('#movie_player video');"
                                   " if (!v) return 'none'; if (v.paused) v.play(); return v.paused ? 'paused' : 'playing'; })()")
                if state == "playing":
                    break
            except Exception:  # noqa: BLE001
                pass
        try:
            k.evaluate("(() => { const p = document.getElementById('movie_player');"
                       " if (p && p.unloadModule) p.unloadModule('captions'); })()")
            self.fullscreen()
        except Exception:  # noqa: BLE001
            pass

    def _after_vk_open(self) -> None:
        k = self.kiosk
        for _ in range(60):
            time.sleep(0.25)
            try:
                k.evaluate(VK_PAGE_JS)
                state = k.evaluate("(() => { const v = window.__bayunScreen && window.__bayunScreen.video();"
                                   " if (!v) return 'none'; if (v.paused) v.play(); return v.paused ? 'paused' : 'playing'; })()")
                if state == "playing":
                    break
            except Exception:  # noqa: BLE001
                pass
        try:
            self.fullscreen()
        except Exception:  # noqa: BLE001
            pass

    def fullscreen(self) -> bool:
        k = self.kiosk
        pos = k.evaluate("(() => { if (document.fullscreenElement) return 'already';"
                         " const p = document.getElementById('movie_player')"
                         " || (window.__bayunScreen && window.__bayunScreen.video()) || document.querySelector('video');"
                         " if (!p) return null; const r = p.getBoundingClientRect();"
                         " return [r.left + r.width / 2, r.top + r.height / 2]; })()")
        if pos == "already":
            return True
        if not pos:
            return False
        k.click(pos[0], pos[1], count=2)  # YouTube toggles fullscreen on a trusted double click
        time.sleep(0.4)
        return bool(k.evaluate("!!document.fullscreenElement"))

    def _point_click(self, n: int) -> dict:
        p = self.kiosk.evaluate(f"window.__bayunScreen.point({int(n)})")
        if not p:
            raise ApiError(404, f"no element number {n}; call /screen/list first")
        self.kiosk.click(p["x"], p["y"])
        self.kiosk.evaluate("window.__bayunOverlay && window.__bayunOverlay.hints(null)")
        return {"clicked": n, "text": p["text"]}

    # -- API -----------------------------------------------------------------

    def open(self, url: str) -> dict:
        m = YOUTUBE_ID.search(url)
        if m:
            url = f"https://www.youtube.com/watch?v={m.group(1)}&cc_load_policy=0"
        self.kiosk.call("Page.navigate", url=url)
        if m:
            threading.Thread(target=self._after_youtube_open, args=(url,), daemon=True).start()
        elif VK_VIDEO.match(url):
            threading.Thread(target=self._after_vk_open, daemon=True).start()
        threading.Thread(target=self._to_tv, daemon=True).start()
        return {"url": url}

    def player(self, action: str, value: Any = None) -> dict:
        if action not in PLAYER_JS:
            raise ApiError(400, f"unknown action {action!r}; known: {', '.join(PLAYER_JS)}")
        body = PLAYER_JS[action]
        if "ARG" in body:
            if value is None:
                raise ApiError(400, f"{action} needs a value")
            body = body.replace("ARG", str(float(value)))
        r = self.kiosk.evaluate(f"(async () => {{ const v = (window.__bayunScreen && window.__bayunScreen.video())"
                                f" || document.querySelector('video');"
                                f" if (!v) return null; return await {body}; }})()", await_promise=True)
        if r is None:
            raise ApiError(404, "no video on the page")
        if action == "status":
            return r
        if r is False:
            raise ApiError(409, f"{action} is not possible on this page")
        return {"done": action}

    def status(self) -> dict:
        out: dict[str, Any] = {"overlay": self.kiosk.overlay_state[0]}
        try:
            out["page"] = self.kiosk.evaluate("({url: location.href, title: document.title})", timeout=2)
            try:
                out["player"] = self.player("status")
            except ApiError:
                out["player"] = None
        except Exception as e:  # noqa: BLE001
            out["page"] = {"error": str(e)}
        if self.tv is not None:
            try:
                out["tv"] = self.tv.status()
            except Exception as e:  # noqa: BLE001
                out["tv"] = {"error": str(e)}
        return out

    def youtube_search(self, q: str, n: int = 8) -> dict:
        url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": q})
        self.kiosk.call("Page.navigate", url=url)
        threading.Thread(target=self._to_tv, daemon=True).start()
        results: list = []
        for i in range(80):
            time.sleep(0.1)
            try:
                if i % 5 == 0 and self.kiosk.evaluate(REJECT_CONSENT_JS):
                    time.sleep(1.5)
                    self.kiosk.call("Page.navigate", url=url)
                    continue
                results = self.kiosk.evaluate(
                    f"window.__bayunScreen ? window.__bayunScreen.youtubeResults({int(n)}) : []") or []
                if len(results) >= min(n, 3):
                    break
            except Exception:  # noqa: BLE001
                pass
        self.results = results
        if results:
            self.kiosk.evaluate("window.__bayunOverlay && window.__bayunOverlay.hints("
                                + json.dumps([{k: r[k] for k in ("n", "x", "y", "w", "h")} for r in results])
                                + ")")
        return {"query": q, "results": [{k: r[k] for k in ("n", "title", "channel", "duration", "url")}
                                        for r in results]}

    def vk_search(self, q: str, n: int = 8) -> dict:
        url = "https://vkvideo.ru/?" + urllib.parse.urlencode({"q": q})
        self.kiosk.call("Page.navigate", url=url)
        threading.Thread(target=self._to_tv, daemon=True).start()
        results: list = []
        for _ in range(80):
            time.sleep(0.1)
            try:
                results = self.kiosk.evaluate(
                    f"window.__bayunScreen ? window.__bayunScreen.vkResults({int(n)}) : []") or []
                if len(results) >= min(n, 3):
                    break
            except Exception:  # noqa: BLE001
                pass
        self.results = results
        if results:
            self.kiosk.evaluate("window.__bayunOverlay && window.__bayunOverlay.hints("
                                + json.dumps([{k: r[k] for k in ("n", "x", "y", "w", "h")} for r in results])
                                + ")")
        return {"query": q, "results": [{k: r[k] for k in ("n", "title", "channel", "duration", "url")}
                                        for r in results]}

    def youtube_play(self, n: int) -> dict:
        """Play result n of the last /youtube/search or /vk/search."""
        match = [r for r in self.results if r["n"] == int(n)]
        if not match:
            raise ApiError(404, f"no result number {n}; search first")
        self.kiosk.evaluate("window.__bayunOverlay && window.__bayunOverlay.hints(null)")
        return {**self.open(match[0]["url"]), "title": match[0]["title"]}

    def screen_list(self, hints: bool | None = None) -> dict:
        items = self.kiosk.evaluate("window.__bayunScreen.list(40)") or []
        show = self.kiosk.hints_on if hints is None else hints
        if show:
            self.kiosk.evaluate(f"window.__bayunOverlay && window.__bayunOverlay.hints({json.dumps(items)})")
        return {"items": [{k: i[k] for k in ("n", "text", "tag")} for i in items]}

    def route(self, method: str, path: str, body: dict) -> Any:
        t = time.perf_counter()
        k = self.kiosk
        if method == "GET" and path == "/status":
            r = self.status()
        elif method == "GET" and path == "/screen/list":
            r = self.screen_list()
        elif method != "POST":
            raise ApiError(404, f"no route {method} {path}")
        elif path == "/open":
            r = self.open(_need(body, "url"))
        elif path == "/home":
            k.call("Page.navigate", url=HOME_URL)
            threading.Thread(target=self._to_tv, daemon=True).start()
            r = {"url": HOME_URL}
        elif path == "/input":
            r = {"tv": self._to_tv()}
        elif path == "/player":
            r = self.player(_need(body, "action"), body.get("value"))
        elif path == "/fullscreen":
            r = {"fullscreen": self.fullscreen()}
        elif path == "/overlay":
            if "variant" in body:
                k.variant = body["variant"]
                k.evaluate(f"window.__bayunOverlay && window.__bayunOverlay.variant({json.dumps(k.variant)})")
            if "phase" in body:
                k.overlay(body["phase"], body.get("text", ""), body.get("level"))
            r = {"overlay": k.overlay_state[0], "variant": k.variant}
        elif path == "/screen/list":
            r = self.screen_list(body.get("hints"))
        elif path == "/screen/hints":
            k.hints_on = bool(body.get("on", True))
            r = self.screen_list(True) if k.hints_on else (
                k.evaluate("window.__bayunOverlay && window.__bayunOverlay.hints(null)") or {"hints": False})
        elif path == "/screen/click":
            r = self._point_click(int(_need(body, "n")))
        elif path == "/screen/click_text":
            n = k.evaluate(f"window.__bayunScreen.findText({json.dumps(_need(body, 'text'), ensure_ascii=False)})")
            if not n:
                raise ApiError(404, f"nothing visible matches {body['text']!r}")
            r = self._point_click(n)
        elif path == "/screen/scroll":
            sign = -1 if body.get("dir", "down") == "up" else 1
            amount = float(body.get("amount", 1))
            w, h = k.evaluate("[innerWidth, innerHeight]")
            k.call("Input.dispatchMouseEvent", type="mouseWheel", x=w / 2, y=h / 2,
                   deltaX=0, deltaY=sign * amount * h * 0.8)
            r = {"scrolled": sign * amount}
        elif path == "/screen/back":
            k.evaluate("history.back()")
            r = {"back": True}
        elif path == "/screen/type":
            k.evaluate(FOCUS_TEXT_FIELD_JS)
            k.call("Input.insertText", text=_need(body, "text"))
            r = {"typed": len(body["text"])}
        elif path == "/screen/key":
            k.key(_need(body, "key"))
            r = {"key": body["key"]}
        elif path == "/youtube/search":
            r = self.youtube_search(_need(body, "q"), int(body.get("n", 8)))
        elif path in ("/youtube/play", "/vk/play"):
            r = self.youtube_play(int(_need(body, "n")))
        elif path == "/vk/search":
            r = self.vk_search(_need(body, "q"), int(body.get("n", 8)))
        elif path == "/tv/power":
            self._need_tv().power(bool(body.get("on", True)))
            r = {"power": bool(body.get("on", True))}
        elif path == "/tv/volume":
            key = TV_KEYS.get(body.get("dir", "up"))
            if not key:
                raise ApiError(400, "dir must be up, down or mute")
            self._need_tv().keys(*[key] * max(1, int(body.get("steps", 1))))
            r = {"volume": body.get("dir", "up")}
        elif path == "/tv/key":
            self._need_tv().keys(_need(body, "key"))
            r = {"key": body["key"]}
        else:
            raise ApiError(404, f"no route {method} {path}")
        if isinstance(r, dict):
            r["ms"] = round((time.perf_counter() - t) * 1000, 1)
        return r

    def _need_tv(self):
        if self.tv is None:
            raise ApiError(503, "Android TV control is disabled (--no-tv)")
        return self.tv


def _need(body: dict, key: str) -> Any:
    if key not in body:
        raise ApiError(400, f"missing {key!r}")
    return body[key]


def make_handler(agent: Agent) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _reply(self, status: int, obj: Any) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self, method: str) -> None:
            path = urllib.parse.urlsplit(self.path).path
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                self._reply(200, agent.route(method, path, body))
            except ApiError as e:
                self._reply(e.status, {"error": str(e)})
            except (ValueError, TypeError) as e:
                self._reply(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001 - report, keep serving
                self._reply(500, {"error": f"{type(e).__name__}: {e}"})

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

    return Handler


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--cdp-port", type=int, default=9222)
    p.add_argument("--no-tv", action="store_true", help="do not talk to the Android TV")
    p.add_argument("--no-status", action="store_true", help="do not follow the voice loop state")
    p.add_argument("--variant", default="aurora", choices=["aurora", "orb", "edge"])
    p.add_argument("--emulate-tv", action="store_true", help="dev: 1920x1080 CSS viewport at 2x in headless")
    args = p.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(Agent(args)))
    server.daemon_threads = True
    print(f"tv-agent on 127.0.0.1:{args.port}, CDP {args.cdp_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
