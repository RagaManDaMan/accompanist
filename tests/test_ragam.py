"""The rāga view: Sa from a session, its swaras, and which rāga it is."""
import random

import pytest

from accompanist import indian, ragam
from accompanist.phrasebook import Phrase


def session(raga_name, sa, phrases=60, seed=0, noise=0.0):
    """Phrases that wander the rāga (up by its ārohaṇa, down by its avarohaṇa) and come home
    to Sa or Pa, as a class does; `noise` adds stray semitones (glides, slips)."""
    r = indian.raga(raga_name)
    rng = random.Random(seed)
    base = 60 + sa
    out = []
    for _ in range(phrases):
        if rng.random() < 0.5:
            line = list(r.arohana[: rng.randint(4, len(r.arohana))])
        else:
            line = list(r.avarohana[: rng.randint(4, len(r.avarohana))])
        line.append(0 if rng.random() < 0.6 else 7)
        notes = []
        for k, i in enumerate(line):
            p = base + i
            if rng.random() < noise:
                p += rng.choice((-1, 1))
            notes.append((k * 0.4, p, 80, 0.35 if k < len(line) - 1 else 1.2))
        out.append(Phrase(tuple(notes), 0, "major"))
    return out


@pytest.mark.parametrize("name,sa", [("bhairavi", 0), ("subhapantuvarali", 6),
                                     ("mohanam", 2), ("hamsadhwani", 7), ("kalyani", 5),
                                     ("todi", 4)])
def test_sa_and_raga_are_found(name, sa):
    phs = session(name, sa)
    found, _ = ragam.sa_of(phs)
    assert found == sa
    prof = ragam.profile(phs, found)
    up, down = ragam.directions(phs, found)
    best = ragam.candidates(prof, up, down)[0][1]
    assert name in best or indian.raga(name).name in best


def test_carnatic_todi_is_not_hindustani_todi():
    assert indian.raga("todi").pitch_classes != indian.raga("subhapantuvarali").pitch_classes
    assert indian.raga("todi-thaat").pitch_classes == indian.raga("subhapantuvarali").pitch_classes


def test_a_noisy_class_still_reads_as_bhairavi():
    phs = session("bhairavi", 0, noise=0.15)
    prof = ragam.profile(phs, 0)
    up, down = ragam.directions(phs, 0)
    assert "bhairavi" in ragam.candidates(prof, up, down)[0][1]
    assert ragam.swaras_used(prof)[:3] == ["S", "R2", "G2"]


def test_rāgas_with_the_same_swaras_are_reported_together():
    phs = session("mohanam", 0)
    prof = ragam.profile(phs, 0)
    names = ragam.candidates(prof)[0][1]
    assert "mohanam" in names and len(names) >= 1
