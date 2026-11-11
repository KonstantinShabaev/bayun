"""Телевизор: видео с YouTube и VK Видео, плеер, громкость и экран через tv-agent.

tv-agent (dev/tv, серверная часть) держит киоск Chromium и пульт ТВ и отвечает по
HTTP за десятки миллисекунд. brain только переводит намерение в его вызовы.
"""

import logging
import time
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)

SOURCES = {"youtube": "youtube", "vk": "vk"}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> Any:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_SOURCE = {
    "type": "string",
    "enum": ["youtube", "vk"],
    "description": "vk — если просят VK Видео или ВКонтакте, иначе youtube",
}

TOOLS: list[dict[str, Any]] = [
    _tool(
        "play_video",
        "Находит видео и сразу включает первое найденное на телевизоре.",
        {"query": {"type": "string", "description": "что искать"}, "source": _SOURCE},
        ["query"],
    ),
    _tool(
        "search_video",
        "Показывает на экране пронумерованные результаты поиска видео, когда просят "
        "найти или показать варианты, а не включить сразу.",
        {"query": {"type": "string"}, "source": _SOURCE},
        ["query"],
    ),
    _tool(
        "play_result",
        "Включает результат поиска по его номеру на экране.",
        {"n": {"type": "integer", "minimum": 1}},
        ["n"],
    ),
    _tool("stop_video", "Выключает видео и возвращает на телевизор экран Баюна.", {}, []),
    _tool(
        "control_video",
        "Плеер на телевизоре: pause, play, seek (seconds: +вперёд, -назад), next — "
        "следующее видео, volume — громкость ролика level_pct 0–100, unmute — вернуть звук ролика.",
        {
            "action": {
                "type": "string",
                "enum": ["pause", "play", "seek", "next", "volume", "unmute"],
            },
            "seconds": {"type": "number"},
            "level_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        },
        ["action"],
    ),
    _tool(
        "set_volume",
        "Громкость самого телевизора (видео и голос Баюна): up, down, mute. "
        "steps — на сколько делений, обычно 3.",
        {
            "direction": {"type": "string", "enum": ["up", "down", "mute"]},
            "steps": {"type": "integer", "minimum": 1, "maximum": 15},
        },
        ["direction"],
    ),
    _tool(
        "screen",
        "Экран телевизора как у человека с пультом: list — показать номера на "
        "кнопках и ссылках, click — нажать номер n или текст text, scroll — "
        "листать (direction up/down), back — назад, type — ввести text, key — "
        "клавиша text (enter, escape, space, up, down, left, right).",
        {
            "action": {
                "type": "string",
                "enum": ["list", "click", "scroll", "back", "type", "key"],
            },
            "n": {"type": "integer", "minimum": 1},
            "text": {"type": "string"},
            "direction": {"type": "string", "enum": ["up", "down"]},
        },
        ["action"],
    ),
]

NAMES = frozenset(t["function"]["name"] for t in TOOLS)

PROMPT = (
    "\nЕсть телевизор с YouTube и VK Видео. Фильм, сериал, мультфильм или «найди» — "
    "search_video, чтобы человек выбрал по номеру (потом play_result). Музыка, клип, "
    "короткое видео — play_video сразу. Плеер — control_video, "
    "громкость ТВ — set_volume, всё остальное на экране — screen. Отвечай коротко."
)

PLAYER_DONE = {
    "pause": "Пауза.",
    "play": "Продолжаю.",
    "seek": "Перемотал.",
    "next": "Следующее.",
    "volume": "Громкость ролика изменена.",
    "unmute": "Звук в ролике включён.",
}


class AgentError(Exception):
    pass


class Video:
    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(base_url=url, timeout=30, transport=transport)
        # Откуда были последние результаты, чтобы «включи второй» знал, где искать.
        self._last_source = "youtube"
        # Что и когда включили последним: повторное «включи первый» (часто это речь
        # из самого фильма, услышанная в продолжении разговора) не перезапускает фильм.
        self._last_play: tuple[str, int, float] | None = None
        self.replay_guard_s = 120.0

    async def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=body or {})
        except httpx.HTTPError as error:
            raise AgentError("Телевизор не отозвался.") from error
        data: dict[str, Any] = response.json() if response.content else {}
        if response.is_error:
            _LOGGER.warning("tv-agent %s %s: %s", path, body, data.get("error"))
            raise AgentError(str(data.get("error") or response.status_code))
        return data

    async def run(self, name: str, args: dict[str, Any]) -> str:
        try:
            return await self._run(name, args)
        except AgentError as error:
            if name in ("control_video",):
                return "Видео сейчас не играет."
            return "Телевизор не отозвался." if "отозвался" in str(error) else f"Не вышло: {error}."

    async def _run(self, name: str, args: dict[str, Any]) -> str:
        if name in ("play_video", "search_video"):
            query = str(args.get("query") or "").strip()
            if not query:
                return "Не сказали, что искать."
            source = SOURCES.get(str(args.get("source")), "youtube")
            self._last_source = source
            self._last_play = None
            results = (await self._post(f"/{source}/search", {"q": query, "n": 8}))["results"]
            if not results:
                return f"По запросу «{query}» ничего не нашлось."
            if name == "play_video":
                await self._post(f"/{source}/play", {"n": results[0]["n"]})
                return f"Включаю: {results[0]['title']}."
            # Номера и так нарисованы на экране: список вслух только мешает.
            return f"Нашёл {len(results)}, номера на экране. Какой включить?"
        if name == "play_result":
            n = int(args.get("n") or 1)
            now = time.monotonic()
            last = self._last_play
            if last and last[:2] == (self._last_source, n) and now - last[2] < self.replay_guard_s:
                return "Уже идёт."
            await self._post(f"/{self._last_source}/play", {"n": n})
            self._last_play = (self._last_source, n, now)
            return "Включаю."
        if name == "stop_video":
            self._last_play = None
            await self._post("/home")
            return "Видео выключено."
        if name == "control_video":
            return await self._player(args)
        if name == "set_volume":
            direction = str(args.get("direction"))
            if direction not in ("up", "down", "mute"):
                return "Не понял, громче или тише."
            steps = max(1, min(15, int(args.get("steps") or 3)))
            await self._post("/tv/volume", {"dir": direction, "steps": steps})
            return {"up": "Громче.", "down": "Тише.", "mute": "Звук ТВ переключён."}[direction]
        if name == "screen":
            return await self._screen(args)
        return "Нет такого инструмента."

    async def _player(self, args: dict[str, Any]) -> str:
        action = str(args.get("action"))
        if action not in PLAYER_DONE:
            return "Так плеером управлять не умею."
        body: dict[str, Any] = {"action": action}
        if action == "seek":
            seconds = float(args.get("seconds") or 0)
            if not seconds:
                return "Не сказали, на сколько перемотать."
            body["value"] = seconds
        if action == "volume":
            body["value"] = max(0, min(100, int(args.get("level_pct") or 100)))
        await self._post("/player", body)
        return PLAYER_DONE[action]

    async def _screen(self, args: dict[str, Any]) -> str:
        action = str(args.get("action"))
        text = str(args.get("text") or "")
        if action == "list":
            items = (await self._post("/screen/list", {"hints": True}))["items"]
            return f"Номера на экране, их {len(items)}. Скажи, что нажать."
        if action == "click":
            if args.get("n"):
                await self._post("/screen/click", {"n": int(args["n"])})
            elif text:
                await self._post("/screen/click_text", {"text": text})
            else:
                return "Не сказали, что нажать."
            return "Нажал."
        if action == "scroll":
            direction = "up" if args.get("direction") == "up" else "down"
            await self._post("/screen/scroll", {"dir": direction})
            return "Листаю."
        if action == "back":
            await self._post("/screen/back")
            return "Назад."
        if action == "type" and text:
            await self._post("/screen/type", {"text": text})
            return "Ввёл."
        if action == "key" and text:
            await self._post("/screen/key", {"key": text})
            return "Нажал."
        return "Так экраном управлять не умею."
