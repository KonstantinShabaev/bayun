"""Видео на телевизоре: поиск на YouTube и показ через внешнюю команду.

Как открыть URL на ТВ, знает сервер (киоск Chromium, вход HDMI): brain только
вызывает настроенную команду, `open_command <url>` и `home_command`.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)


@dataclass
class Found:
    url: str
    title: str


Search = Callable[[str], Awaitable[Found | None]]

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "play_video",
            "description": "Находит видео на YouTube и включает его на телевизоре.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "что искать на YouTube"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_video",
            "description": "Выключает видео и возвращает на телевизор экран Баюна.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

PROMPT = (
    "\nЕсть телевизор: включить на нём видео — play_video с запросом для YouTube, "
    "выключить — stop_video. Назови, что включил."
)


async def youtube_search(query: str) -> Found | None:
    """Первый результат поиска YouTube через yt-dlp, без ключа API."""
    import yt_dlp  # type: ignore[import-untyped]

    def run() -> dict[str, Any]:
        options = {"quiet": True, "extract_flat": True, "skip_download": True}
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(f"ytsearch1:{query}", download=False)  # type: ignore[no-any-return]

    info = await asyncio.to_thread(run)
    entries = info.get("entries") or []
    if not entries:
        return None
    first = entries[0]
    return Found(f"https://www.youtube.com/watch?v={first['id']}", first.get("title") or query)


class Video:
    def __init__(
        self,
        open_command: list[str],
        home_command: list[str],
        search: Search = youtube_search,
    ) -> None:
        self.open_command = open_command
        self.home_command = home_command
        self.search = search

    async def run(self, name: str, args: dict[str, Any]) -> str:
        if name == "stop_video":
            if not self.home_command:
                return "Не умею выключать видео."
            return await self._exec(self.home_command, "Видео выключено.")
        query = str(args.get("query") or "").strip()
        if not query:
            return "Не сказали, что включить."
        try:
            found = await self.search(query)
        except Exception:
            _LOGGER.exception("Поиск видео не удался")
            return "YouTube сейчас не отвечает."
        if found is None:
            return f"По запросу «{query}» ничего не нашлось."
        return await self._exec([*self.open_command, found.url], f"Включено: {found.title}.")

    async def _exec(self, command: list[str], done: str) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        except (OSError, TimeoutError) as error:
            _LOGGER.error("%s: %s", command[0], error)
            return "Телевизор не отозвался."
        if proc.returncode != 0:
            _LOGGER.error("%s: %s", command[0], err.decode(errors="replace").strip())
            return "Телевизор не отозвался."
        return done
