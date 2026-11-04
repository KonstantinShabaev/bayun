"""Persistent Android TV Remote connection (androidtvremote2) on its own asyncio thread.

The TV is paired once (~/.config/bayun/androidtv/cert.pem, key.pem). Key codes
for HDMI inputs are ignored by this Changhong TV; opening the TV input URI works.
"""

import asyncio
import os
import threading
from concurrent.futures import Future
from typing import Any

PAIR_DIR = os.path.expanduser("~/.config/bayun/androidtv")
INPUT_PLAYER = "com.google.android.tv.inputplayer"


class AndroidTV:
    def __init__(self, host: str, input_uri: str) -> None:
        self.host = host
        self.input_uri = input_uri
        self._loop = asyncio.new_event_loop()
        self._remote: Any = None
        threading.Thread(target=self._loop.run_forever, daemon=True).start()

    def _run(self, coro, timeout: float = 15) -> Any:
        fut: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout)

    async def _connected(self):
        if self._remote is not None:
            return self._remote
        from androidtvremote2 import AndroidTVRemote

        remote = AndroidTVRemote("Bayun", os.path.join(PAIR_DIR, "cert.pem"),
                                 os.path.join(PAIR_DIR, "key.pem"), self.host)
        await remote.async_connect()
        remote.keep_reconnecting()
        for _ in range(20):  # current_app arrives right after connect
            if remote.current_app:
                break
            await asyncio.sleep(0.05)
        self._remote = remote
        return remote

    async def _status(self) -> dict:
        r = await self._connected()
        return {"on": r.is_on, "app": r.current_app, "volume": r.volume_info}

    async def _keys(self, *keys: str) -> None:
        r = await self._connected()
        for k in keys:
            r.send_key_command(k)
            await asyncio.sleep(0.05)

    async def _to_laptop(self) -> str:
        r = await self._connected()
        if not r.is_on:
            r.send_key_command("KEYCODE_POWER")
            for _ in range(40):
                if r.is_on:
                    break
                await asyncio.sleep(0.1)
        if r.current_app == INPUT_PLAYER:
            return "already"  # the input player is up; it shows our HDMI
        r.send_launch_app_command(self.input_uri)
        return "switched"

    async def _power(self, on: bool) -> None:
        r = await self._connected()
        if bool(r.is_on) != on:
            r.send_key_command("KEYCODE_POWER")

    # -- public, thread-safe ---------------------------------------------

    def status(self) -> dict:
        return self._run(self._status())

    def keys(self, *keys: str) -> None:
        self._run(self._keys(*keys))

    def to_laptop(self) -> str:
        return self._run(self._to_laptop())

    def power(self, on: bool) -> None:
        self._run(self._power(on))
