from bayun_voice.accent import zh_accent, zh_max_accent, zh_strong_accent

# «здрасьте» в фонемах espeak
HELLO = ["z", "d", "r", "ˈ", "ɑ", "s", "t", "ʲ", "e", "!"]


def test_zh_accent_keeps_russian_but_swaps_r() -> None:
    assert "".join(zh_accent(HELLO)) == "sdlˈɑstʲe!"


def test_zh_max_accent_devoices_and_breaks_clusters() -> None:
    assert "".join(zh_max_accent(HELLO)) == "sətəlˈɑsətʲe!"


def test_zh_max_accent_keeps_final_n_and_softness() -> None:
    assert "".join(zh_max_accent(["d", "ʲ", "e", "n", " "])) == "tʲen "


def test_zh_strong_accent_adds_final_vowel() -> None:
    # «спорь со» → «сполʲə со»
    phonemes = ["s", "p", "ˈ", "o", "r", "ʲ", " ", "s", "ə"]
    assert "".join(zh_strong_accent(phonemes)) == "spˈolʲə sə"


def test_text_accents_for_silero() -> None:
    from bayun_voice.accent import zh_strong_text_accent, zh_text_accent

    assert zh_text_accent("Привет, Жора!") == "Пливет, Шола!"
    assert zh_strong_text_accent("Привет, мир") == "Пливетэ, милэ"
