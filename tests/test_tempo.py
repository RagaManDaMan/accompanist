from pathlib import Path

import pytest

from accompanist.config import TempoCfg
from accompanist.recording import load_take
from accompanist.simulate import synthetic_melody
from accompanist.tempo import TempoEstimator

FIXTURES = Path(__file__).parent / "fixtures"


def track(onsets, total, cfg=None, dt=0.05):
    """Feed onsets in virtual time; return the estimator and [(t, bpm, confidence)] per update."""
    est, trace, i, now = TempoEstimator(cfg or TempoCfg()), [], 0, 0.0
    times = sorted(t for t, *_ in onsets)
    while now <= total:
        while i < len(times) and times[i] <= now:
            est.on_onset(times[i])
            i += 1
        if est.update(now):
            trace.append((now, est.bpm, est.confidence))
        now = round(now + dt, 6)
    return est, trace


def at(trace, t):
    return next(b for tt, b, _ in trace if tt >= t)


@pytest.mark.parametrize("bpm", [60, 90])
def test_steady_melody_fixture_within_4pct_by_30s(bpm):
    onsets = load_take(FIXTURES / f"melody-{bpm}bpm.jsonl")
    _, trace = track(onsets, 45)
    assert all(abs(b / bpm - 1) <= 0.04 for t, b, _ in trace if t >= 30)


def test_120_reads_as_60_with_the_default_prior_and_right_with_a_faster_one():
    """The beat octave is a prior: documented in the README."""
    onsets = load_take(FIXTURES / "melody-120bpm.jsonl")
    _, trace = track(onsets, 45)
    assert at(trace, 30) == pytest.approx(60, rel=0.04)
    _, trace = track(onsets, 45, TempoCfg(prior_bpm=110))
    assert all(abs(b / 120 - 1) <= 0.04 for t, b, _ in trace if t >= 30)


def test_finds_tempo_from_a_wrong_start():
    """The hardware failure: true 100 bpm, estimator starting at 70, stuck near 68."""
    onsets, _ = synthetic_melody(100, 30)
    _, trace = track(onsets, 30, TempoCfg(initial_bpm=70))
    assert at(trace, 10) == pytest.approx(100, rel=0.02)


def test_accelerando_fixture_lags_at_most_15s():
    onsets = load_take(FIXTURES / "accel-70to110bpm.jsonl")   # 70 -> 110 over 60 s
    true = lambda t: 70 + 40 * min(max(t, 0), 60) / 60
    _, trace = track(onsets, 70)
    for t, b, _ in trace:
        if 20 <= t <= 70:
            assert any(abs(b / true(t - lag) - 1) <= 0.04 for lag in range(16)), (t, b)


def test_drifting_25pct_melody_within_8pct_after_30s():
    onsets = load_take(FIXTURES / "drift25-85bpm.jsonl")
    _, trace = track(onsets, 50)
    assert all(abs(b / 85 - 1) <= 0.08 for t, b, _ in trace if t >= 30)


def test_loose_timing_around_a_steady_beat():
    onsets, _ = synthetic_melody(80, 45, jitter=0.15, seed=3)
    _, trace = track(onsets, 45)
    assert all(abs(b / 80 - 1) <= 0.04 for t, b, _ in trace if t >= 30)


def test_eighth_note_stream_reads_as_half_beats():
    onsets = [(i * 60 / 80 / 2,) for i in range(80)]  # eighths at 80 bpm
    est, _ = track(onsets, 30)
    assert est.bpm == pytest.approx(80, rel=0.03)


def test_simultaneous_notes_count_once():
    onsets = [(i * 60 / 70 + d,) for i in range(30) for d in (0.0, 0.02)]  # chords
    est, _ = track(onsets, 25)
    assert est.bpm == pytest.approx(70, rel=0.02)


def test_confident_on_a_steady_beat_not_on_random_notes():
    import random

    rng = random.Random(1)
    est, _ = track(synthetic_melody(90, 30)[0], 30)
    assert est.confidence > 0.8
    random_onsets = sorted((rng.uniform(0, 30),) for _ in range(60))
    est, _ = track(random_onsets, 30)
    assert est.confidence < 0.5


def test_too_few_onsets_keep_the_previous_estimate():
    est, _ = track([(i * 0.5,) for i in range(5)], 10, TempoCfg(initial_bpm=70))
    assert est.bpm == 70 and est.confidence == 0


def test_silence_holds_tempo_and_confidence_fades():
    onsets, _ = synthetic_melody(90, 20)
    est, trace = track(onsets, 60)
    assert est.bpm == pytest.approx(90, rel=0.02)
    assert est.confidence < max(c for _, _, c in trace) / 2


def test_recomputes_about_once_per_second():
    _, trace = track(synthetic_melody(90, 20)[0], 20)
    gaps = [b[0] - a[0] for a, b in zip(trace, trace[1:])]
    assert min(gaps) >= 1.0 - 1e-9


def test_bpm_is_clamped():
    onsets = [(i * 0.2,) for i in range(150)]   # 300 bpm stream
    est, _ = track(onsets, 30, TempoCfg(initial_bpm=170, max_bpm=180, prior_bpm=170))
    assert 40 <= est.bpm <= 180


def test_parameters_changed_live_apply_at_the_next_update():
    cfg = TempoCfg()
    onsets, _ = synthetic_melody(120, 60)
    est = TempoEstimator(cfg)
    for t, *_ in onsets:
        if t <= 30:
            est.on_onset(t)
    for now in range(31):
        est.update(float(now))
    assert est.bpm == pytest.approx(60, rel=0.04)
    cfg.prior_bpm = 110   # same object the estimator holds
    for t, *_ in onsets:
        if 30 < t <= 60:
            est.on_onset(t)
    for now in range(31, 61):
        est.update(float(now))
    assert est.bpm == pytest.approx(120, rel=0.04)
