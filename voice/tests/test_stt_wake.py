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


@pytest.mark.parametrize(
    "heard",
    ["Молты, включи свет.", "Волт, на паузу.", "олты, включи свет.", "Волдер. Какая погода?",
     "Полтер, какая погода?", "Колтер, включи фильм."],
)  # fmt: skip
def test_far_field_fragments_wake(heard: str) -> None:
    from bayun_voice.wake import name_match

    assert name_match(heard) is not None


@pytest.mark.parametrize(
    "text",
    ["Волны, шум.", "Молоты, включи", "На Волге, говорят", "Волге, привет", "Мол, не знаю.",
     "Поттер, часть два", "Полтора часа.", "Ольга, привет", "Вольты, амперы", "Молт пиво"],
)  # fmt: skip
def test_fragments_need_vocative_first_word(text: str) -> None:
    from bayun_voice.wake import name_match

    assert name_match(text) is None


def test_repeated_name_is_not_a_command() -> None:
    from bayun_voice.wake import strip_names

    assert strip_names("Волтер.") == ""
    assert strip_names("Волтер, Волтер, включи свет") == "включи свет"
    assert strip_names("включи свет") == "включи свет"


def test_preroll_keeps_speech_start() -> None:
    vad, asr = FakeVad(), FakeAsr("Волтер")
    wake = SttWake(asr, vad, preroll_frames=3)
    vad.speech = False
    for _ in range(5):
        wake(np.zeros(1280, dtype=np.int16))
    captured: list[int] = []
    asr.transcribe = lambda pcm, rate: captured.append(len(pcm) // 2) or "Волтер"  # type: ignore[method-assign]
    assert say(wake, vad) == 1.0
    assert captured[0] == (3 + 10 + 5) * 1280


def test_click_does_not_block_gain() -> None:
    from bayun_voice.wake import normalize

    audio = (np.sin(np.arange(16000) / 5) * 200).astype(np.int16)
    audio[100] = 30000  # стук по столу
    assert np.abs(normalize(audio)).mean() > 1500
