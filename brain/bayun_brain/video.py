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
    {
        "type": "function",
        "function": {
            "name": "control_video",
            "description": (
                "Плеер на телевизоре: pause — пауза, play — продолжить, "
                "seek — перемотать на seconds (+вперёд, -назад), next — следующее видео, "
                "volume — громкость самого ролика level_pct 0–100 (снимает mute в видео)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["pause", "play", "seek", "next", "volume"],
                    },
                    "seconds": {"type": "number"},
                    "level_pct": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": (
                "Громкость телевизора (видео и голос Баюна): up — громче, down — тише, "
                "mute — выключить или вернуть звук. steps — на сколько делений, обычно 3."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down", "mute"]},
                    "steps": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["direction"],
            },
        },
    },
]

PLAYER_DONE = {
    "pause": "Пауза.",
    "play": "Продолжаю.",
    "seek": "Перемотал.",
    "next": "Следующее.",
    "volume": "Громкость ролика изменена.",
}
VOLUME_KEYS = {"up": "KEYCODE_VOLUME_UP", "down": "KEYCODE_VOLUME_DOWN", "mute": "KEYCODE_MUTE"}

PROMPT = (
    "\nЕсть телевизор: включить на нём видео — play_video с запросом для YouTube, "
    "выключить — stop_video, пауза и перемотка — control_video, громкость — set_volume. "
    "Назови, что включил."
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
        volume_command: list[str] | None = None,
        player_command: list[str] | None = None,
    ) -> None:
        self.player_command = player_command or []
        self.open_command = open_command
        self.home_command = home_command
        self.volume_command = volume_command or []
        self.search = search
        self._background: set[asyncio.Task[str]] = set()

    async def run(self, name: str, args: dict[str, Any]) -> str:
        if name == "set_volume":
            return await self._volume(args)
        if name == "control_video":
            return await self._player(args)
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
        # Ждать, пока ролик заиграет, долго (до 15 с): открываем в фоне и сразу отвечаем.
        task = asyncio.create_task(self._exec([*self.open_command, found.url], ""))
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return f"Включаю: {found.title}."

    async def _player(self, args: dict[str, Any]) -> str:
        action = args.get("action")
        if not self.player_command or action not in PLAYER_DONE:
            return "Так плеером управлять не умею."
        flags = [f"--{action}"]
        if action == "seek":
            seconds = float(args.get("seconds") or 0)
            if not seconds:
                return "Не сказали, на сколько перемотать."
            flags.append(f"{seconds:+g}")
        if action == "volume":
            flags.append(str(max(0, min(100, int(args.get("level_pct") or 100)))))
        outcome = await self._exec([*self.player_command, *flags], PLAYER_DONE[str(action)])
        return outcome.replace("Телевизор не отозвался.", "Видео сейчас не играет.")

    async def _volume(self, args: dict[str, Any]) -> str:
        key = VOLUME_KEYS.get(str(args.get("direction")))
        if not self.volume_command or key is None:
            return "Громкость менять не умею."
        steps = 1 if key == VOLUME_KEYS["mute"] else max(1, min(15, int(args.get("steps") or 3)))
        outcome = await self._exec([*self.volume_command, *[key] * steps], "Громкость изменена.")
        return outcome.replace("Телевизор не отозвался.", "Громкость не поменялась.")

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
