"""Разговор с LLM через LiteLLM: ответ потоком, нарезанный на предложения.

Первое предложение уходит в TTS, пока модель пишет второе (architecture §6).
С инструментами (дом) — короткий цикл: модель → вызовы → модель, потом ответ по предложениям.
"""

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import Config

SYSTEM_PROMPT = """\
Ты {name}, домашний голосовой ассистент. Говоришь по-русски.
Характер: ироничный интеллектуал. На команды отвечаешь коротко и точно, \
в разговоре у тебя есть своё мнение, сухой юмор, без подобострастия.
Твой ответ сразу озвучивается синтезатором речи, поэтому:
- одна-три короткие фразы, если не просят подробнее;
- никакого markdown, списков, эмодзи и ссылок;
- числа, единицы и сокращения пиши так, как их произносят."""

NO_HOME_PROMPT = """
Управлять устройствами дома ты пока не умеешь: если просят, честно скажи, \
что этому тебя ещё учат."""

# Добавка к промпту, когда голос понимает теги эмоций (ElevenLabs v3).
AUDIO_TAGS_PROMPT = """
Твой голос умеет эмоции. Ставь в ответ теги на английском в квадратных скобках \
перед фразой, к которой они относятся: [laughs], [chuckles], [sighs], [whispers], \
[excited], [sarcastic], [curious], [surprised]. Смейся, когда правда смешно, \
не больше одного-двух тегов на ответ."""

Message = dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str | dict[str, Any]


@dataclass
class Completion:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class ChatModel(Protocol):
    """Источник ответа. В тестах подменяется фейком."""

    def stream(self, messages: list[Message]) -> AsyncIterator[str]: ...

    async def complete(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> Completion: ...


class LiteLlmChat:
    def __init__(self, config: Config) -> None:
        # LiteLLM импортируется несколько секунд: делаем это при старте, а не на первой фразе.
        import litellm  # noqa: F401

        self.config = config.llm

    def _kwargs(self, messages: list[Message]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.config.main,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "timeout": self.config.timeout_s,
            # Anthropic иногда отвечает overloaded: повтор, потом запасная модель.
            "num_retries": 1,
        }
        if self.config.fallbacks:
            kwargs["fallbacks"] = self.config.fallbacks
        if self.config.api_base:
            kwargs["api_base"] = self.config.api_base
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        return kwargs

    async def complete(self, messages: list[Message], tools: list[dict[str, Any]]) -> Completion:
        import litellm

        response = await litellm.acompletion(**self._kwargs(messages), tools=tools)
        message = response.choices[0].message
        calls = [
            ToolCall(c.id, c.function.name, c.function.arguments)
            for c in (message.tool_calls or [])
        ]
        return Completion(message.content or "", calls)

    async def search(self, question: str) -> str:
        """Ответ с поиском в интернете (серверный web search провайдера), уже для речи."""
        import litellm

        messages: list[Message] = [
            {"role": "system", "content": SEARCH_PROMPT},
            {"role": "user", "content": question},
        ]
        response = await litellm.acompletion(
            # Поиск и рассуждение тратят токены до ответа: даём запас, иначе текст пустой.
            **{
                **self._kwargs(messages),
                "timeout": max(self.config.timeout_s, 30),
                "max_tokens": 1500,
            },
            web_search_options={"search_context_size": "low"},
        )
        answer = str(response.choices[0].message.content or "").strip()
        return answer or "Не нашёл ответа, спроси иначе."

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        import litellm

        response = await litellm.acompletion(**self._kwargs(messages), stream=True)
        async for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


# Конец предложения: . ! ? … и пробел после них. Короткие куски склеиваем со следующими,
# чтобы TTS не читал по слову.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
MIN_SENTENCE_CHARS = 12


class SentenceSplitter:
    def __init__(self) -> None:
        self.buffer = ""

    def feed(self, text: str) -> list[str]:
        self.buffer += text
        parts = _SENTENCE_END.split(self.buffer)
        self.buffer = parts.pop()
        out: list[str] = []
        pending = ""
        for part in parts:
            pending = f"{pending} {part}".strip()
            if len(pending) >= MIN_SENTENCE_CHARS:
                out.append(pending)
                pending = ""
        if pending:
            self.buffer = f"{pending} {self.buffer}"
        return out

    def flush(self) -> list[str]:
        rest = " ".join(self.buffer.split())
        self.buffer = ""
        return [rest] if rest else []


def clean_for_speech(text: str) -> str:
    """Убирает то, что синтезатор прочитает вслух как мусор."""
    text = re.sub(r"[*_#`>]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    return " ".join(text.split())


class Conversations:
    """Рабочая память: последние реплики каждого разговора, только в ОЗУ."""

    def __init__(self, max_turns: int) -> None:
        self.max_messages = max_turns * 2
        self.history: dict[str, list[Message]] = {}

    def messages(self, conversation_id: str, system: str, text: str) -> list[Message]:
        past = self.history.get(conversation_id, [])
        return [{"role": "system", "content": system}, *past, {"role": "user", "content": text}]

    def remember(self, conversation_id: str, text: str, reply: str) -> None:
        past = self.history.setdefault(conversation_id, [])
        past += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
        del past[: -self.max_messages]


async def reply_sentences(model: ChatModel, messages: list[Message]) -> AsyncIterator[str]:
    splitter = SentenceSplitter()
    async for delta in model.stream(messages):
        for sentence in splitter.feed(delta):
            if cleaned := clean_for_speech(sentence):
                yield cleaned
    for sentence in splitter.flush():
        if cleaned := clean_for_speech(sentence):
            yield cleaned


RunTool = Callable[[ToolCall], Awaitable[str]]


class Final(str):
    """Итог инструмента, который сразу произносится: без ещё одного круга LLM."""


SEARCH_PROMPT = (
    "Найди в интернете ответ на вопрос и ответь по-русски для озвучки: одно-два "
    "коротких предложения, без ссылок, списков, скобок и названий сайтов. Числа "
    "пиши цифрами, единицы словами: «плюс два градуса»."
)

WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Поиск в интернете: погода, новости, курсы, расписания, факты, которых ты "
            "не знаешь точно или которые меняются. Ответ сразу произносится."
        ),
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "вопрос целиком"}},
            "required": ["question"],
        },
    },
}
MAX_TOOL_ROUNDS = 3


async def agent_sentences(
    model: ChatModel, messages: list[Message], tools: list[dict[str, Any]], run_tool: RunTool
) -> AsyncIterator[str]:
    """Модель с инструментами: вызывает их, пока нужно, и отдаёт итоговый текст."""
    messages = list(messages)
    text = ""
    for _ in range(MAX_TOOL_ROUNDS):
        result = await model.complete(messages, tools)
        text = result.text
        if not result.tool_calls:
            break
        messages.append(
            {
                "role": "assistant",
                "content": result.text or None,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": _as_json(c.arguments)},
                    }
                    for c in result.tool_calls
                ],
            }
        )
        finals: list[str] = []
        for call in result.tool_calls:
            output = await run_tool(call)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": str(output)})
            if isinstance(output, Final):
                finals.append(output)
        if finals:
            text = " ".join(finals)
            break
    splitter = SentenceSplitter()
    for sentence in [*splitter.feed(text), *splitter.flush()]:
        if cleaned := clean_for_speech(sentence):
            yield cleaned


def _as_json(arguments: str | dict[str, Any]) -> str:
    import json

    return arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
