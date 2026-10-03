"""Rāga and tāla data: the melakarta system, sūlādi tālas, and the listed rāgas and tālas."""
import pytest

from accompanist import config as c
from accompanist import indian

MAJOR = (0, 2, 4, 5, 7, 9, 11, 12)


def test_the_72_melakartas_are_distinct_and_the_famous_ones_are_right():
    scales = {indian.melakarta(n).arohana for n in range(1, 73)}
    assert len(scales) == 72
    assert indian.melakarta(29).arohana == MAJOR                       # shankarabharanam
    assert indian.melakarta(65).arohana == (0, 2, 4, 6, 7, 9, 11, 12)  # kalyani: M2
    assert indian.melakarta(15).arohana == (0, 1, 4, 5, 7, 8, 11, 12)  # mayamalavagowla
    assert indian.melakarta(22).arohana == (0, 2, 3, 5, 7, 9, 10, 12)  # kharaharapriya
    assert indian.melakarta(8).name == "hanumatodi"
    assert indian.raga("kalyan").pitch_classes == indian.raga("kalyani").pitch_classes


def test_listed_ragas_parse_and_aliases_work():
    assert indian.raga("Bhupali") is indian.raga("mohanam")
    assert indian.raga("hamsadhwani").pitch_classes == {0, 2, 4, 7, 11}
    assert indian.raga("bhairavi").pitch_classes == {0, 2, 3, 5, 7, 8, 9, 10}  # both dhaivatams
    with pytest.raises(c.ConfigError, match="unknown rāga"):
        indian.raga("nope")


def test_talas_have_their_beats_and_groups():
    assert indian.tala("tintal").beats == 16 and indian.tala("tintal").khali == (9,)
    assert indian.tala("jhaptal").group_starts == (0, 2, 5, 7)
    assert indian.tala("adi").beats == 8
    assert indian.tala("khanda-chapu").groups == (2, 3)                 # not 3+2
    assert indian.tala("misra-chapu").groups == (3, 2, 2)
    assert indian.tala("khanda-jhampa").beats == 5 + 1 + 2
    assert indian.tala("chatusra-triputa").groups == indian.tala("adi").groups
    assert len([n for n in indian.talas() if n.endswith(tuple(indian.SULADI))]) == 35
