import numpy as np
import pytest
from bayun_voice.wake import SttWake


class FakeVad:
    def __init__(self) -> None:
        self.speech = True

    def __call__(self, frame: np.ndarray) -> float:
        return 1.0 if self.speech else 0.0

    def reset(self) -> None:
        pass


class FakeAsr:
    def __init__(self, text: str) -> None:
        self.text = text
        self.peaks: list[int] = []

    def transcribe(self, pcm: bytes, rate: int) -> str:
        self.peaks.append(int(np.abs(np.frombuffer(pcm, dtype=np.int16)).max()))
        return self.text


def say(wake: SttWake, vad: FakeVad, speech_frames: int = 10) -> float:
    quiet = (np.ones(1280) * 300).astype(np.int16)  # тихо, как издалека
    scores = []
    vad.speech = True
    scores += [wake(quiet) for _ in range(speech_frames)]
    vad.speech = False
    scores += [wake(quiet) for _ in range(6)]
    return max(scores)


@pytest.mark.parametrize(
    ("heard", "rest"),
    [
        ("Волтер, включи свет.", "включи свет."),
        ("Уолтэр", ""),
        ("вальтер пауза", "пауза"),
        ("Вольтер, какая погода", "какая погода"),
        ("Wалтер, включи свет.", "включи свет."),
        ("Эй, Лаутер.", ""),
    ],
)
def test_name_variants_and_command_after_name(heard: str, rest: str) -> None:
    vad, asr = FakeVad(), FakeAsr(heard)
    wake = SttWake(asr, vad)
    assert say(wake, vad) == 1.0
    assert wake.remainder == rest
    assert asr.peaks[0] > 5000  # тихую речь подняли перед распознаванием


def test_other_speech_does_not_wake() -> None:
    vad = FakeVad()
    wake = SttWake(FakeAsr("включи свет на кухне"), vad)
    assert say(wake, vad) == 0.0


def test_short_noise_is_not_transcribed() -> None:
    vad, asr = FakeVad(), FakeAsr("Волтер")
    wake = SttWake(asr, vad)
    assert say(wake, vad, speech_frames=1) == 0.0
    assert asr.peaks == []


@pytest.mark.parametrize(
    "text", ["Литр молока купи", "Фильтр для воды", "Вольт и ампер", "Лотерея", "Альтернатива есть"]
)
def test_similar_words_are_not_the_name(text: str) -> None:
    from bayun_voice.wake import name_match

    assert name_match(text) is None
