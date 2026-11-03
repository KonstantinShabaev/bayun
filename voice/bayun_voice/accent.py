"""Акценты на уровне фонем: правим русские фонемы espeak перед синтезом.

`zh` — очень сильный китайский акцент: р → л, ы → и, звонкие оглушаются,
между согласными и после конечной согласной вставляется гласный, потому что
в слоге путунхуа согласные не стоят подряд и слово кончается гласным или «н».
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
    "b": "p",
    "d": "t",
    "ɡ": "k",
    "g": "k",
    "v": "f",
    "z": "s",
    "ʐ": "ʂ",
    "ʒ": "ʂ",
}
_EPENTHETIC = "ə"


def _is_consonant(p: str) -> bool:
    return p.isalpha() and p not in _VOWELS and p not in _MODIFIERS


def zh_accent(phonemes: Phonemes) -> Phonemes:
    swapped = [_ZH_SWAP.get(p, p) for p in phonemes]
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


ACCENTS: dict[str, Callable[[Phonemes], Phonemes]] = {"zh": zh_accent}
