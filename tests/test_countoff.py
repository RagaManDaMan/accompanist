"""Counting the band in: the number of taps sets the meter, the taps set the tempo."""
import pytest

from accompanist import config as c, simulate

PERIOD = 0.6          # 100 bpm: on the simulation's 5 ms grid


def count(n, start=1.0, extra=None, notes=(), total=None):
    taps = [(start + i * PERIOD, "tap_tempo") for i in range(n)]
    downbeat = start + n * PERIOD
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                       "harmony": {"root": "D"}, "pulse": {"movement": 0.0}, **(extra or {})})
    res = simulate.run(cfg, onsets=list(notes), actions=taps, total=total or downbeat + 14 * PERIOD)
    return res, downbeat


def hits(res, channel, note=None):
    return [(t, m) for t, m in res.timeline if m.type == "note_on" and m.channel == channel
            and (note is None or m.note == note)]


@pytest.mark.parametrize("n,label,pattern", [(3, "3/4", "waltz"), (4, "4/4", None),
                                             (5, "5/4", "five"), (6, "6/8", "six-eight"),
                                             (7, "7 (3+2+2)", "seven-322")])
def test_the_count_sets_the_meter_and_the_drums(n, label, pattern):
    res, downbeat = count(n)
    eng = res.engine
    assert eng.groove.label().startswith(label) and eng.locked
    assert eng.tempo.bpm == pytest.approx(100, rel=0.01)
    assert eng.drums.pattern_name == pattern                     # 4: your pattern fits
    kicks = [t for t, _ in hits(res, 9, 36)]
    assert kicks[0] == pytest.approx(downbeat, abs=PERIOD * 0.2)  # the band comes in on 1


def test_the_band_comes_in_on_one_and_stays_on_the_counted_grid():
    res, downbeat = count(3)
    drum_times = sorted({round(t, 3) for t, _ in hits(res, 9)})
    first = drum_times[0]
    assert downbeat <= first <= downbeat + PERIOD * 0.2          # just after count_wait
    period = 60 / res.engine.tempo.bpm                            # the tempo actually counted
    later = [t for t in drum_times if t > downbeat + period]
    for t in later:                                               # on the beat or half beat
        x = (t - downbeat) / (period / 2)
        assert abs(x - round(x)) * period / 2 < 0.02


def test_waltz_accents_every_third_beat_from_the_count():
    notes = [(0.2, 62, 90), (0.4, 66, 90)]                       # a little harmony first
    res, downbeat = count(3, notes=notes, total=1.0 + 3 * PERIOD + 12 * PERIOD)
    bass = [(t, m.velocity) for t, m in hits(res, 1)]
    top = max(v for _, v in bass)
    for t, v in bass:
        beat = round((t - downbeat) / PERIOD)
        assert (v == top) == (beat % 3 == 0), (beat, v)


def test_counting_again_changes_tempo_and_meter():
    taps = [(1.0 + i * PERIOD, "tap_tempo") for i in range(4)]
    later = 1.0 + 20 * PERIOD
    fast = 0.4
    taps += [(later + i * fast, "tap_tempo") for i in range(3)]
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False}, "harmony": {"root": "D"}})
    res = simulate.run(cfg, onsets=[], actions=taps, total=later + 3 * fast + 8 * fast)
    assert res.engine.groove.label().startswith("3/4")
    assert res.engine.tempo.bpm == pytest.approx(150, rel=0.02)


@pytest.mark.parametrize("n", [2, 8])
def test_other_counts_are_ignored_with_a_message(n):
    res, _ = count(n, total=1.0 + (n + 3) * PERIOD)
    assert not res.engine.locked and res.engine.groove.meter is None
    assert not hits(res, 9)


def test_a_chart_count_in_is_on_time_after_one_bar_of_taps():
    from pathlib import Path

    chart = str(Path(__file__).parent / "fixtures" / "charts" / "form-test.musicxml")
    taps = [(1.0 + i * PERIOD, "tap_tempo") for i in range(4)]
    cfg = c.from_dict({"harmony": {"model": "chart", "chart": chart}})
    res = simulate.run(cfg, onsets=[], actions=taps, total=1.0 + 6 * PERIOD)
    first = next(t for t, w in res.log if w.startswith("pad -> "))
    assert first == pytest.approx(1.0 + 4 * PERIOD, abs=0.011)   # exactly on 1, no waiting


def test_a_fast_five_count_is_taken_at_its_tempo_not_capped():
    """Regression (takes/ms-4): five taps at ~305 bpm became 5/4 at 180, the listening cap."""
    fast, start = 0.195, 12.0
    before = [(i * 0.4, 62 + i % 3, 80) for i in range(28)]       # playing at 150 first
    taps = [(start + i * fast, "tap_tempo") for i in range(5)]
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                       "harmony": {"root": "D"}})
    res = simulate.run(cfg, onsets=before, actions=taps, total=start + 5.0)
    eng = res.engine
    assert eng.groove.label().startswith("5/4") and eng.locked
    assert eng.tempo.bpm == pytest.approx(60 / fast, rel=0.02)
    kicks = sorted(t for t, _ in hits(res, 9, 36))
    assert kicks and min(b - a for a, b in zip(kicks, kicks[1:])) < 5 * fast * 1.1


def test_a_fast_uneven_count_is_still_one_count():
    """The owner's misra-chapu count-off: 7 quick taps (~210 bpm), a little uneven."""
    gaps = [0.22, 0.32, 0.33, 0.28, 0.29, 0.28]
    t, taps = 1.0, [(1.0, "tap_tempo")]
    for g in gaps:
        t += g
        taps.append((round(t / 0.005) * 0.005, "tap_tempo"))
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False}, "harmony": {"root": "D"}})
    res = simulate.run(cfg, onsets=[], actions=taps, total=t + 3)
    assert res.engine.groove.label().startswith("7")


def test_with_a_known_meter_the_taps_only_set_the_tempo():
    res, _ = count(4, extra={"song": {"count": 7}})
    assert res.engine.groove.label().startswith("7") and res.engine.locked
    assert res.engine.tempo.bpm == pytest.approx(100, rel=0.01)


def test_with_a_known_meter_a_bar_of_taps_brings_the_band_in_on_time():
    res, downbeat = count(7, extra={"song": {"count": 7}})
    kicks = [t for t, _ in hits(res, 9, 36)]
    assert kicks[0] == pytest.approx(downbeat, abs=0.01)          # on the 1, no waiting
    assert res.engine.groove.label().startswith("7")
