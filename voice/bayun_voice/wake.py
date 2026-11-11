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
# Издалека GigaAM теряет первый звук и хвост «эр» в отзвуке: «Молты, включи свет»,
# «Волт, поставь на паузу», «Волдер.». Такие обрывки — настоящие слова рядом
# («Волге», «молот»), поэтому только первым словом куска, целиком и с запятой или
# точкой сразу после: так зовут по имени, а не рассказывают.
NAME_FRAGMENTS = {"волт", "волты", "молт", "молты", "олты", "волдер", "волгер"}
# «?олтер» с любым первым звуком: «Полтер», «Колтер», «Болтер» — таких слов в языке нет.
_LOST_FIRST = "олтер"


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


def _is_fragment(word: str) -> bool:
    w = _norm(word)
    return w in NAME_FRAGMENTS or (len(w) in (5, 6) and _distance(w, _LOST_FIRST) <= 1)


def name_match(text: str, pattern: re.Pattern[str] | None = None) -> re.Match[str] | None:
    """Имя среди первых трёх слов: зовут в начале фразы, а не посреди рассказа."""
    if pattern is not None:
        return pattern.search(text)
    words = list(re.finditer(r"\w+", text))[:3]
    for m in words:
        if _is_name(m.group()):
            return m
    if words:
        first = words[0]
        vocative = text[first.end() : first.end() + 1] in {",", ".", "!", "?"}
        if vocative and _is_fragment(first.group()):
            return first
    return None


def strip_names(text: str) -> str:
    """«Волтер. Волтер, включи свет» → «включи свет»: повтор имени — не команда."""
    while (found := name_match(text)) is not None and not text[: found.start()].strip(" ,.!?—-"):
        text = text[found.end() :].lstrip(" ,.!?—-").strip()
    return text


def near_name(text: str) -> str | None:
    """Первое слово, похожее на имя, но не узнанное: для журнала, без остального текста."""
    for m in list(re.finditer(r"\w+", text))[:3]:
        w = _norm(m.group())
        if (
            len(w) >= 4
            and w[0] in "вулмп"
            and min(_distance(w[:6], s[:6]) for s in NAME_SPELLINGS) <= 3
        ):
            return m.group()
    return None


class Frontend:
    """Подготовка звука перед VAD и STT: срез гула ниже 100 Гц.

    Половина энергии шума у fifine — ниже 100 Гц (гул, вибрации стола); речи там нет,
    а нормализация по пику и Silero VAD на нём ошибаются.
    """

    def __init__(self, cutoff_hz: float = 100.0, rate: int = 16000) -> None:
        import numpy as np

        # Биквад Баттерворта 2-го порядка, без scipy.
        w0 = 2 * np.pi * cutoff_hz / rate
        alpha = np.sin(w0) / np.sqrt(2)
        c = np.cos(w0)
        a0 = 1 + alpha
        self.b = np.array([(1 + c) / 2, -(1 + c), (1 + c) / 2]) / a0
        self.a = np.array([1.0, -2 * c / a0, (1 - alpha) / a0])
        self.z = np.zeros(2)
        try:
            from scipy.signal import lfilter  # есть в venv вместе с openwakeword

            self._lfilter: Any = lfilter
        except ImportError:
            self._lfilter = None

    def __call__(self, frame: Frame) -> Frame:
        import numpy as np

        x = frame.astype(np.float64)
        if self._lfilter is not None:
            y, self.z = self._lfilter(self.b, self.a, x, zi=self.z)
        else:
            y = np.empty_like(x)
            b0, b1, b2 = self.b
            _, a1, a2 = self.a
            z1, z2 = self.z
            for i, xi in enumerate(x):
                yi = b0 * xi + z1
                z1 = b1 * xi - a1 * yi + z2
                z2 = b2 * xi - a2 * yi
                y[i] = yi
            self.z = np.array([z1, z2])
        return np.clip(y, -32768, 32767).astype(np.int16)


def normalize(audio: "Any", target_rms: float = 3000.0, max_gain: float = 30.0) -> "Any":
    """Поднять речь до нормальной громкости по RMS громких кадров, а не по пику:
    один щелчок или стук больше не мешает поднять тихий голос."""
    import numpy as np

    x = audio.astype(np.float32)
    frames = x[: len(x) // 320 * 320].reshape(-1, 320)
    if not len(frames):
        return x.astype(np.int16)
    rms = np.sqrt((frames**2).mean(axis=1))
    loud = float(np.percentile(rms, 80)) or 1.0
    peak = float(np.percentile(np.abs(x), 99.9)) or 1.0
    gain = min(max_gain, target_rms / loud, 0.9 * 32767 / peak)
    return np.clip(x * max(gain, 1.0), -32768, 32767).astype(np.int16)


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
        preroll_frames: int = 4,
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
        # VAD узнаёт речь с опозданием на кадр-два: без этих 0,3 с до начала
        # GigaAM слышит «олты» вместо «Волтэр» (половина промахов с 2–4 м).
        self.preroll_frames = preroll_frames
        self._preroll: list[Frame] = []
        self.frontend = Frontend()
        self.near_miss = ""

    def __call__(self, frame: Frame) -> float:
        import numpy as np

        frame = self.frontend(frame)
        speech = self.vad(frame) >= self.vad_threshold
        if speech and not self._frames:
            self._frames = list(self._preroll)
        if not self._frames:
            keep = self.preroll_frames
            self._preroll = [*self._preroll, frame][-keep:] if keep else []
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
        audio = np.concatenate(self._frames)
        speech_frames = self._speech
        self._frames, self._silence, self._speech = [], 0, 0
        self._preroll = []
        # Меньше 0,3 с речи — щелчок или кашель, имя так коротко не сказать.
        if speech_frames < 4:
            return 0.0
        # Издалека речь тихая: поднимаем по громкости речи, не больше ×30.
        text = self.asr.transcribe(normalize(audio).tobytes(), 16000).strip()
        self.last_text = text
        found = name_match(text, self.pattern)
        if not found:
            self.near_miss = near_name(text) or ""
            return 0.0
        self.remainder = strip_names(text[found.end() :].lstrip(" ,.!?—-").strip())
        return 1.0

    def reset(self) -> None:
        self._frames, self._silence, self._speech = [], 0, 0
        self._preroll = []
        self.near_miss = ""
        self.vad.reset()
