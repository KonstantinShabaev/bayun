import pytest
from bayun_bench.run_wer import score


def test_perfect_match_ignores_case_and_yo() -> None:
    result = score({"1": "Включи свет на кухне"}, {"1": "включи свет на кухне"})
    assert result.wer == 0.0
    assert score({"1": "ещё"}, {"1": "еще"}).cer == 0.0


def test_one_wrong_word_out_of_four() -> None:
    result = score({"1": "включи свет на кухне"}, {"1": "включи свет на крыше"})
    assert result.wer == pytest.approx(0.25)


def test_no_common_ids_is_an_error() -> None:
    with pytest.raises(ValueError):
        score({"1": "а"}, {"2": "а"})
