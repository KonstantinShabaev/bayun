"""Chrome DevTools Protocol over a persistent websocket, standard library only.

One reader thread per connection dispatches replies to waiting callers and
events to listeners, so calls are a single round trip (a few ms).
"""

import base64
import json
import os
import re
import socket
import struct
import threading
import urllib.request
from collections.abc import Callable
from typing import Any


class CDPError(RuntimeError):
    pass


class Connection:
    def __init__(self, ws_url: str, timeout: float = 10) -> None:
        m = re.match(r"ws://([^/:]+):(\d+)(/.*)", ws_url)
        if not m:
            raise ValueError(ws_url)
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((host, port), timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("websocket handshake failed")
            buf += chunk
        self._buf = buf.split(b"\r\n\r\n", 1)[1]
        self.sock.settimeout(None)
        self._send_lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, list] = {}  # id -> [Event, reply]
        self._listeners: list[Callable[[str, dict], None]] = []
        self.closed = threading.Event()
        threading.Thread(target=self._reader, daemon=True).start()

    # -- framing --------------------------------------------------------

    def _need(self, n: int) -> None:
        while len(self._buf) < n:
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise ConnectionError("websocket closed")
            self._buf += chunk

    def _recv(self) -> dict | None:
        msg = b""
        while True:
            self._need(2)
            b1, n, off = self._buf[0], self._buf[1] & 127, 2
            if n == 126:
                self._need(4)
                n, off = struct.unpack(">H", self._buf[2:4])[0], 4
            elif n == 127:
                self._need(10)
                n, off = struct.unpack(">Q", self._buf[2:10])[0], 10
            self._need(off + n)
            payload, self._buf = self._buf[off:off + n], self._buf[off + n:]
            opcode = b1 & 0x0F
            if opcode == 8:
                raise ConnectionError("websocket closed by peer")
            if opcode in (9, 10):  # ping/pong: Chrome does not send them, ignore
                continue
            msg += payload
            if b1 & 0x80:
                return json.loads(msg)

    def _send(self, obj: dict) -> None:
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            head = bytes([0x81, 0x80 | n])
        elif n < 65536:
            head = bytes([0x81, 0xFE]) + struct.pack(">H", n)
        else:
            head = bytes([0x81, 0xFF]) + struct.pack(">Q", n)
        masked = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        with self._send_lock:
            self.sock.sendall(head + mask + masked)

    def _reader(self) -> None:
        try:
            while True:
                msg = self._recv()
                if msg is None:
                    continue
                if "id" in msg:
                    slot = self._pending.pop(msg["id"], None)
                    if slot:
                        slot[1] = msg
                        slot[0].set()
                elif "method" in msg:
                    for fn in list(self._listeners):
                        try:
                            fn(msg["method"], msg.get("params", {}))
                        except Exception:
                            pass
        except (OSError, ValueError, ConnectionError):
            pass
        finally:
            self.closed.set()
            for slot in self._pending.values():
                slot[0].set()

    # -- API ------------------------------------------------------------

    def call(self, method: str, timeout: float = 10, **params: Any) -> dict:
        if self.closed.is_set():
            raise ConnectionError("CDP connection is closed")
        with self._send_lock:
            self._next_id += 1
            msg_id = self._next_id
        slot = [threading.Event(), None]
        self._pending[msg_id] = slot
        self._send({"id": msg_id, "method": method, "params": params})
        if not slot[0].wait(timeout):
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"{method}: no reply in {timeout} s")
        reply = slot[1]
        if reply is None:
            raise ConnectionError("CDP connection is closed")
        if "error" in reply:
            raise CDPError(f"{method}: {reply['error'].get('message')}")
        return reply.get("result", {})

    def evaluate(self, expression: str, timeout: float = 10, await_promise: bool = False) -> Any:
        r = self.call("Runtime.evaluate", timeout=timeout, expression=expression,
                      returnByValue=True, awaitPromise=await_promise)
        if "exceptionDetails" in r:
            ex = r["exceptionDetails"]
            raise CDPError(ex.get("exception", {}).get("description") or ex.get("text", "JS error"))
        return r.get("result", {}).get("value")

    def on_event(self, fn: Callable[[str, dict], None]) -> None:
        self._listeners.append(fn)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def list_pages(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as r:
        return [t for t in json.load(r) if t.get("type") == "page"]
