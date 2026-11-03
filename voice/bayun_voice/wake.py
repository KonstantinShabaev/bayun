"""Детектор имени. Работает постоянно, звук никуда не уходит (architecture §6)."""

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
