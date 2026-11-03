"""Разговор с LLM через LiteLLM: ответ потоком, нарезанный на предложения.

Первое предложение уходит в TTS, пока модель пишет второе (architecture §6).
Агента с инструментами здесь пока нет, только характер и рабочая память.
"""

import re
from collections.abc import AsyncIterator
from typing import Any, Protocol

from .config import Config

SYSTEM_PROMPT = """\
Ты {name}, домашний голосовой ассистент. Говоришь по-русски.
Характер: ироничный интеллектуал. На команды отвечаешь коротко и точно, \
в разговоре у тебя есть своё мнение, сухой юмор, без подобострастия.
Твой ответ сразу озвучивается синтезатором речи, поэтому:
- одна-три короткие фразы, если не просят подробнее;
- никакого markdown, списков, эмодзи и ссылок;
- числа, единицы и сокращения пиши так, как их произносят.
Управлять устройствами дома ты пока не умеешь: если просят, честно скажи, \
что этому тебя ещё учат."""

Message = dict[str, str]


class ChatModel(Protocol):
    """Источник токенов ответа. В тестах подменяется фейком."""

    def stream(self, messages: list[Message]) -> AsyncIterator[str]: ...


class LiteLlmChat:
    def __init__(self, config: Config) -> None:
        # LiteLLM импортируется несколько секунд: делаем это при старте, а не на первой фразе.
        import litellm  # noqa: F401

        self.config = config.llm

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        import litellm

        kwargs: dict[str, Any] = {
            "model": self.config.main,
            "messages": messages,
            "stream": True,
            "max_tokens": self.config.max_tokens,
            "timeout": self.config.timeout_s,
        }
        if self.config.api_base:
            kwargs["api_base"] = self.config.api_base
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        response = await litellm.acompletion(**kwargs)
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
