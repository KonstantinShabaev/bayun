from bayun_stt.normalize import normalize


def test_normalize_collapses_spaces_and_case() -> None:
    assert normalize("  Включи   Свет ") == "включи свет"
