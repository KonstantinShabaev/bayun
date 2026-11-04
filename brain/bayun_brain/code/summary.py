"""Итог Claude Code для озвучки: одна-две фразы той же быстрой моделью."""

from ..llm import ChatModel, clean_for_speech
from .tasks import Summarize, first_sentences

PROMPT = (
    "Перескажи для озвучки отчёт программиста одной-двумя короткими фразами по-русски: "
    "что сделано, прошли ли проверки и тесты, что нужно от пользователя. "
    "Без markdown, путей к файлам, хешей и ссылок. Имена веток и команд не зачитывай."
)


def summarizer(chat: ChatModel | None) -> Summarize:
    if chat is None:
        return first_sentences

    async def summarize(text: str) -> str:
        messages = [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": text[-6000:]},
        ]
        parts = [delta async for delta in chat.stream(messages)]
        return clean_for_speech("".join(parts)) or await first_sentences(text)

    return summarize
