"""Акценты на уровне фонем: правим русские фонемы espeak перед синтезом.

`zh` — сильный китайский акцент, но речь остаётся русской: р → л, ы → и,
ж/з упрощаются. `zh-strong` — плюс гласный в конце слова после согласной.
`zh-max` — ещё и оглушение звонких и гласные между согласными, как в слогах
путунхуа; это уже почти не понять.

Голос лучше русский: китайский голос с русскими фонемами звучит как китайская речь.
"""

from collections.abc import Callable

Phonemes = list[str]

_VOWELS = set("aeiouyɑɐəɛɪɔʊʌɨæɵø")
_MODIFIERS = {"ʲ", "ː"}
_STRESS = {"ˈ", "ˌ"}
_SONORANT_END = {"n", "ŋ"}

_ZH_SWAP = {
    "r": "l",
    "ɾ": "l",
    "ɨ": "i",
    "y": "i",
    "ʐ": "ʂ",
    "ʒ": "ʂ",
    "z": "s",
}
_ZH_MAX_SWAP = {
    **_ZH_SWAP,
    "b": "p",
    "d": "t",
    "ɡ": "k",
    "g": "k",
    "v": "f",
}
_EPENTHETIC = "ə"


def _is_consonant(p: str) -> bool:
    return p.isalpha() and p not in _VOWELS and p not in _MODIFIERS


def zh_accent(phonemes: Phonemes) -> Phonemes:
    return [_ZH_SWAP.get(p, p) for p in phonemes]


def _final_vowel(phonemes: Phonemes) -> Phonemes:
    """Гласный после согласной в конце слова, кроме «н»: «шапкɐ», «мнойə»."""
    out: Phonemes = []
    for i, p in enumerate(phonemes):
        out.append(p)
        nxt = phonemes[i + 1] if i + 1 < len(phonemes) else " "
        if nxt.isalpha() or nxt in _MODIFIERS:
            continue
        soft_end = p in _MODIFIERS and i > 0 and _is_consonant(phonemes[i - 1])
        if soft_end or (_is_consonant(p) and p not in _SONORANT_END):
            out.append(_EPENTHETIC)
    return out


def zh_strong_accent(phonemes: Phonemes) -> Phonemes:
    return _final_vowel(zh_accent(phonemes))


def zh_max_accent(phonemes: Phonemes) -> Phonemes:
    swapped = [_ZH_MAX_SWAP.get(p, p) for p in phonemes]
    out: Phonemes = []
    for i, p in enumerate(swapped):
        out.append(p)
        if not _is_consonant(p):
            continue
        # Смягчение и ударение перед следующей фонемой не разрывают кластер.
        j = i + 1
        while j < len(swapped) and swapped[j] in _MODIFIERS:
            out.append(swapped[j])
            j += 1
        k = j
        while k < len(swapped) and swapped[k] in _STRESS:
            k += 1
        nxt = swapped[k] if k < len(swapped) else " "
        if _is_consonant(nxt) or (not nxt.isalpha() and p not in _SONORANT_END):
            out.append(_EPENTHETIC)
        # Уже перенесённые модификаторы пропускаем.
        swapped[i + 1 : j] = [""] * (j - i - 1)
    return [p for p in out if p]


ACCENTS: dict[str, Callable[[Phonemes], Phonemes]] = {
    "zh": zh_accent,
    "zh-strong": zh_strong_accent,
    "zh-max": zh_max_accent,
}
