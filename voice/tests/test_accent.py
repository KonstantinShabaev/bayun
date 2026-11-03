from bayun_voice.accent import zh_accent


def test_zh_accent_swaps_r_devoices_and_breaks_clusters() -> None:
    # «здрасьте» в фонемах espeak
    phonemes = ["z", "d", "r", "ˈ", "ɑ", "s", "t", "ʲ", "e", "!"]
    assert "".join(zh_accent(phonemes)) == "sətəlˈɑsətʲe!"


def test_zh_accent_keeps_final_n_and_softness() -> None:
    assert "".join(zh_accent(["d", "ʲ", "e", "n", " "])) == "tʲen "
