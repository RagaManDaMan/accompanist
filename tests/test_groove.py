"""Hearing meter, downbeat and feel: on the pure Groove, and end to end through the engine."""
import pytest

from accompanist import config as c, simulate
from accompanist.groove import Groove
from groove_synth import notes


def groove(**g):
    return Groove(c.from_dict({"groove": g}).groove)


@pytest.mark.parametrize("meter", [3, 4])
@pytest.mark.parametrize("swing", [0.0, 0.33])
def test_meter_downbeat_and_feel(meter, swing):
    for down in range(meter):
        for seed in range(3):
            g = groove()
            ns = notes(meter, bars=10, swing=swing, downbeat=down, seed=seed, jitter=0.06)
            for t, b, p, v in ns:
                g.observe(t, b, p, v)
            g.update(ns[-1][0] + 0.1, 0.6)
            assert (g.meter, g.downbeat, g.feel) == (meter, down % meter,
                                                     "swing" if swing else "straight")


def test_it_commits_and_only_switches_after_holding():
    g = groove(hold_s=4.0)
    for t, b, p, v in notes(4, bars=10, seed=1):
        g.observe(t, b, p, v)
    g.update(24.0, 0.6)
    assert g.meter == 4
    # Now a waltz starts: the old 4/4 notes age out of the window and the 3 wins, but only
    # after it has kept winning for hold_s.
    for t, b, p, v in notes(3, bars=20, seed=1, downbeat=40):
        g.observe(t, b, p, v)
    last = max(t for t, *_ in notes(3, bars=20, seed=1, downbeat=40))
    g.update(last, 0.6)
    assert g.meter == 4                                   # a challenger, not yet the meter
    g.update(last + 4.5, 0.6)
    assert g.meter == 3


def as_onsets(meter, swing=0.0, bars=24, seed=0):
    """The synthetic line as timed notes (MIDI pitch from a simple melody), from t = 1 s."""
    ns = notes(meter, bars=bars, swing=swing, downbeat=0, seed=seed)
    melody = [62, 66, 69, 67, 64, 66, 62]
    return [(1.0 + t, melody[i % len(melody)], v) for i, (t, b, p, v) in enumerate(ns)], 0.6


def test_a_waltz_gets_a_waltz():
    onsets, period = as_onsets(3)
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                       "harmony": {"root": "D"}, "tempo": {"prior_bpm": 100}})
    res = simulate.run(cfg, onsets=onsets, total=onsets[-1][0])
    eng = res.engine
    assert eng.groove.meter == 3 and eng.drums.pattern_name == "waltz"
    # The loudest bass notes (the accented 1s) fall on the waltz's real downbeats.
    late = [(t, m.velocity) for t, m in res.timeline
            if m.type == "note_on" and m.channel == 1 and t > onsets[-1][0] - 8]
    top = max(v for _, v in late)
    ones = [t for t, v in late if v == top]
    for t in ones:
        bars = (t - 1.0) / (3 * period)
        assert abs(bars - round(bars)) * 3 * period < 0.08


def test_swing_is_heard_and_the_drums_swing():
    onsets, _ = as_onsets(4, swing=0.33)
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                       "harmony": {"root": "D"}, "tempo": {"prior_bpm": 100}})
    res = simulate.run(cfg, onsets=onsets, total=onsets[-1][0])
    g = res.engine.groove
    assert g.meter == 4 and g.feel == "swing" and g.swing == pytest.approx(0.33, abs=0.08)
    assert "4/4 swing" in res.controller.get_state(res.end_time)["groove"]


def test_the_groove_can_be_turned_off():
    onsets, _ = as_onsets(3)
    cfg = c.from_dict({"groove": {"auto": False}, "drums": {"enabled": True},
                       "lock": {"auto": False}, "harmony": {"root": "D"}})
    res = simulate.run(cfg, onsets=onsets, total=onsets[-1][0])
    assert res.engine.drums.pattern_name is None


def test_feel_does_not_flicker_at_the_threshold():
    g = groove(swing_threshold=0.12)
    g.swing = 0.13
    g._update_feel([])                    # no new evidence: judge the current amount
    assert g.feel == "swing"
    g.swing = 0.10                        # just under the threshold: still swinging
    g._update_feel([])
    assert g.feel == "swing"
    g.swing = 0.05                        # clearly straight
    g._update_feel([])
    assert g.feel == "straight"
