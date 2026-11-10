"""Детектор имени. Работает постоянно, звук никуда не уходит (architecture §6)."""

import re
from pathlib import Path
from typing import Any

from .audio import Frame


class OpenWakeWord:
    def __init__(self, model: str) -> None:
        import openwakeword
        from openwakeword.model import Model

        if not Path(model).exists():
            # Встроенные модели скачиваются один раз в папку пакета.
            openwakeword.utils.download_models([model])
        self.name = Path(model).stem
        self._model: Any = Model(wakeword_models=[model], inference_framework="onnx")

    def __call__(self, frame: Frame) -> float:
        scores: dict[str, float] = self._model.predict(frame)
        return max(scores.values(), default=0.0)

    def reset(self) -> None:
        self._model.reset()


# GigaAM пишет «Волтэр» очень по-разному: «Волтер», «Уалтер», «Wалтер», «Лаутер»,
# «Woutr», а без паузы и слитно с командой: «Уалтерпауза». Сравниваем по расстоянию
# правки с известными написаниями.
_LATIN = str.maketrans("abcdefghiklmnoprstuvwxyz", "абцдефгхиклмнопрстуввкуз")
NAME_SPELLINGS = (
    "волтер", "уолтер", "валтер", "вальтер", "уалтер", "лаутер", "ваутер", "вольтер",
    "уалатер", "лоутер", "воутр",
)  # fmt: skip


def _norm(word: str) -> str:
    return word.lower().replace("ё", "е").replace("э", "е").translate(_LATIN)


def _distance(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def _is_name(word: str) -> bool:
    w = _norm(word)
    # Все написания начинаются с «в», «у» или «л»: отсекает «альтернативу» и прочее.
    if len(w) < 5 or w[0] not in "вул":
        return False
    for spelling in NAME_SPELLINGS:
        # Целое слово или его начало, если имя слилось с командой.
        for candidate in {w, w[: len(spelling)]}:
            limit = 1 if len(candidate) <= 6 else 2
            if len(candidate) >= 5 and _distance(candidate, spelling) <= limit:
                return True
    return False


def name_match(text: str, pattern: re.Pattern[str] | None = None) -> re.Match[str] | None:
    """Имя среди первых трёх слов: зовут в начале фразы, а не посреди рассказа."""
    if pattern is not None:
        return pattern.search(text)
    for m in list(re.finditer(r"\w+", text))[:3]:
        if _is_name(m.group()):
            return m
    return None


class SttWake:
    """Имя по распознаванию речи: любое слово без обучения модели и издалека.

    VAD режет речь на куски, каждый кусок усиливается до нормальной громкости и
    распознаётся (GigaAM ~0,25 с). Если в нём есть имя, срабатываем, а всё, что
    сказано после имени, отдаём как первую фразу: «Волтэр, включи свет» — одной фразой.
    """

    name = "волтэр"

    def __init__(
        self,
        asr: Any,
        vad: Any,
        pattern: str = "",
        vad_threshold: float = 0.3,
    ) -> None:
        self.asr = asr
        self.vad = vad
        self.vad_threshold = vad_threshold
        self.pattern = re.compile(pattern, re.IGNORECASE) if pattern else None
        self.last_text = ""
        # Сказанное после имени в том же куске.
        self.remainder = ""
        self._frames: list[Frame] = []
        self._silence = 0
        self._speech = 0

    def __call__(self, frame: Frame) -> float:
        import numpy as np

        speech = self.vad(frame) >= self.vad_threshold
        if speech:
            self._frames.append(frame)
            self._silence = 0
            self._speech += 1
        elif self._frames:
            self._frames.append(frame)
            self._silence += 1
        # Кусок закончен: пауза 0,4 с или 6 с без перерыва (речь из ТВ).
        if not self._frames or (self._silence < 5 and len(self._frames) < 75):
            return 0.0
        audio = np.concatenate(self._frames).astype(np.float32)
        speech_frames = self._speech
        self._frames, self._silence, self._speech = [], 0, 0
        # Меньше 0,3 с речи — щелчок или кашель, имя так коротко не сказать.
        if speech_frames < 4:
            return 0.0
        # Издалека речь тихая: поднимаем до пика ~0,7 полной шкалы, не больше ×30.
        peak = float(np.abs(audio).max()) or 1.0
        audio = np.clip(audio * min(30.0, 0.7 * 32767 / peak), -32768, 32767)
        text = self.asr.transcribe(audio.astype(np.int16).tobytes(), 16000).strip()
        self.last_text = text
        found = name_match(text, self.pattern)
        if not found:
            return 0.0
        self.remainder = text[found.end() :].lstrip(" ,.!?—-").strip()
        return 1.0

    def reset(self) -> None:
        self._frames, self._silence, self._speech = [], 0, 0
        self.vad.reset()
