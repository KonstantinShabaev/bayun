import numpy as np
from bayun_voice.audio import FRAME_S, FRAME_SAMPLES, beep
from bayun_voice.listen import Endpointer, EnergyVad, Verdict


def run(ender: Endpointer, probs: list[float]) -> Verdict:
    verdict = Verdict.LISTENING
    for p in probs:
        verdict = ender.push(p)
        if verdict is not Verdict.LISTENING:
            break
    return verdict


def frames(seconds: float) -> int:
    return round(seconds / FRAME_S)


def test_silence_only_gives_nothing_after_timeout() -> None:
    ender = Endpointer(start_timeout_s=2.0, silence_s=0.8, max_s=10)
    assert run(ender, [0.0] * frames(2.0)) is Verdict.NOTHING


def test_phrase_ends_after_pause() -> None:
    ender = Endpointer(start_timeout_s=2.0, silence_s=0.8, max_s=10)
    probs = [0.0] * 5 + [0.9] * frames(1.5) + [0.0] * frames(0.8)
    assert run(ender, probs) is Verdict.DONE
    assert ender.elapsed_s < 3.0


def test_single_click_is_not_speech() -> None:
    ender = Endpointer(start_timeout_s=1.0, silence_s=0.5, max_s=10)
    assert run(ender, [0.9] + [0.0] * frames(1.0)) is Verdict.NOTHING


def test_long_speech_is_cut_at_max() -> None:
    ender = Endpointer(start_timeout_s=2.0, silence_s=0.8, max_s=3.0)
    assert run(ender, [0.9] * frames(5)) is Verdict.DONE
    assert ender.elapsed_s <= 3.0 + FRAME_S


def test_energy_vad_tells_voice_from_hum() -> None:
    vad = EnergyVad()
    rng = np.random.default_rng(0)
    hum = (rng.normal(0, 150, FRAME_SAMPLES)).astype(np.int16)
    for _ in range(20):
        assert vad(hum) == 0.0
    voice = np.frombuffer(beep(16000, duration_s=FRAME_S, volume=0.5), dtype=np.int16)
    assert vad(voice[:FRAME_SAMPLES]) == 1.0


def test_strip_audio_tags() -> None:
    from bayun_voice.tts import strip_audio_tags

    assert strip_audio_tags("[laughs] Ну ты даёшь. [sighs]") == "Ну ты даёшь."
