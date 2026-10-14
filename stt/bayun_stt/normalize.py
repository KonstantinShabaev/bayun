"""Нормализация текста после ASR: «двадцать два градуса» → «22 градуса».

Реализация в этапе M1 (см. docs/ROADMAP.md). Пока — только чистка пробелов и регистра.
"""


def normalize(text: str) -> str:
    return " ".join(text.lower().split())
