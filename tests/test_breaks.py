"""A break: on the next 1 the band hits, then stops for breaks.bars bars; the pad stays, low;
the band comes back in on the 1 with a crash."""
import pytest

from accompanist import config as c, simulate
from accompanist.patterns import GM_DRUMS

PERIOD = 0.5                  # 120 bpm, 4/4: bars of 2 s from the downbeat at 3.0


def run(at=8.3, extra=None, more=()):
    notes = [(0.2 + i * 0.25, (62, 65, 69)[i % 3], 90) for i in range(80)]   # 20 s of playing
    taps = [(1.0 + i * PERIOD, "tap_tempo") for i in range(4)]
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                       "response": {"enabled": True}, "piano": {"enabled": True},
                       "harmony": {"model": "modal", "root": "D", "mode": "minor"},
                       **(extra or {})})
    res = simulate.run(cfg, onsets=notes, actions=taps + [(at, "break"), *more], total=20.0)
    return res


def on(res, ch, lo, hi):
    return [(t, m.note) for t, m in res.timeline
            if m.type == "note_on" and m.channel == ch and lo <= t < hi]


def test_the_band_hits_the_one_then_rests_for_two_bars_then_returns():
    res = run()
    one = 9.0                                                       # the next 1 after 8.3
    hit = on(res, 9, one - 0.06, one + 0.06)
    assert {GM_DRUMS["kick"], GM_DRUMS["crash"]} <= {n for _, n in hit}
    assert on(res, 1, one - 0.06, one + 0.06)                        # the bass's hit
    assert not on(res, 9, one + 0.1, one + 4.0 - 0.1)               # then silence...
    assert not on(res, 1, one + 0.1, one + 4.0 - 0.1)
    assert not on(res, 3, one + 0.1, one + 4.0) and not on(res, 4, one + 0.1, one + 4.0)
    back = on(res, 9, one + 4.0 - 0.06, one + 4.1)                   # ...back on the 1
    assert GM_DRUMS["crash"] in {n for _, n in back}
    assert on(res, 1, one + 4.0 - 0.06, one + 6.0)


def test_the_pad_stays_low_through_a_break():
    res = run()
    cc = [(t, m.value) for t, m in res.timeline if m.type == "control_change" and m.control == 11]
    level_at_start = [v for t, v in cc if t <= 9.1][-1]
    during = [v for t, v in cc if 9.1 < t < 12.95]
    assert level_at_start <= round(127 * 0.15) + 2 and all(v <= 21 for v in during)


def test_break_again_during_a_break_ends_it_at_the_next_one():
    res = run(more=[(9.6, "break")])
    assert on(res, 9, 11.0 - 0.06, 11.1)                             # back after one bar


def test_break_with_nothing_playing_says_so():
    from accompanist.controller import Controller
    from accompanist.output import RecordingPort, SafeOutput

    ctl = Controller(c.from_dict({}), SafeOutput(RecordingPort()))
    assert ctl.do("break", 0.0) == "nothing playing to break"
