import pytest
from accompanist.tempo import TempoEstimator


def feed(est, times):
    for t in times:
        est.on_onset(t)


def test_converges_on_steady_quarter_notes():
    est = TempoEstimator(initial_bpm=70)
    feed(est, [i * 60 / 80 for i in range(24)])
    assert est.bpm == pytest.approx(80, rel=0.03)
    assert est.confidence > 0.9


def test_reads_eighth_notes_as_half_beats():
    est = TempoEstimator(initial_bpm=70)
    feed(est, [i * 60 / 80 / 2 for i in range(48)])  # eighths at 80 bpm
    assert est.bpm == pytest.approx(80, rel=0.05)


def test_follows_an_accelerando():
    est, t = TempoEstimator(initial_bpm=70), 0.0
    for i in range(40):
        est.on_onset(t)
        t += 60 / (70 + i * 1.0)  # 70 -> 110 bpm
    assert est.bpm == pytest.approx(108, rel=0.06)


def test_simultaneous_notes_count_once():
    est = TempoEstimator(initial_bpm=70)
    t = 0.0
    for _ in range(20):
        est.on_onset(t)
        est.on_onset(t + 0.02)  # chord: second note within min_ioi
        t += 60 / 70
    assert est.bpm == pytest.approx(70, rel=0.02)


def test_outlier_is_ignored_not_absorbed():
    est = TempoEstimator(initial_bpm=70)
    feed(est, [i * 60 / 70 for i in range(12)])
    before = est.bpm
    est.on_onset(12 * 60 / 70 + 0.37)  # an interval that fits no beat multiple
    assert est.bpm == pytest.approx(before, rel=0.02)


def test_long_gap_is_a_phrase_break():
    est = TempoEstimator(initial_bpm=70)
    feed(est, [i * 60 / 70 for i in range(12)])
    before, conf = est.bpm, est.confidence
    est.on_onset(12 * 60 / 70 + 30.0)
    assert est.bpm == pytest.approx(before)
    assert est.confidence < conf


def test_bpm_is_clamped():
    est = TempoEstimator(initial_bpm=170, max_bpm=180)
    feed(est, [i * 0.2 for i in range(40)])
    assert est.bpm <= 180.0001
