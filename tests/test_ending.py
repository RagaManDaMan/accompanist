"""Finishing a song: f (or a pedal) ends it on the next 1 with one last chord."""
import pytest

from accompanist import config as c, simulate
from accompanist.patterns import GM_DRUMS

PERIOD = 0.6


def finished_run(finish_at=8.0, extra=None):
    notes = [(0.2 + i * 0.6, (62, 65, 69)[i % 3], 90) for i in range(12)]   # D minor-ish
    taps = [(1.0 + i * PERIOD, "tap_tempo") for i in range(4)]
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                       "harmony": {"model": "modal", "root": "D", "mode": "minor"},
                       **(extra or {})})
    return cfg, simulate.run(cfg, onsets=notes, actions=taps + [(finish_at, "finish")], total=20.0)


def hits(res, channel):
    return [(t, m.note) for t, m in res.timeline if m.type == "note_on" and m.channel == channel]


def test_finish_ends_on_the_next_one_with_the_tonic_then_stops():
    cfg, res = finished_run()
    downbeat = 1.0 + 4 * PERIOD
    crashes = [t for t, n in hits(res, 9) if n == GM_DRUMS["crash"]]
    end = crashes[-1]
    bars = (end - downbeat) / (4 * PERIOD)
    assert bars == pytest.approx(round(bars), abs=0.05) and end > 8.0      # on a 1, after f
    assert end - 8.0 <= 4 * PERIOD + 0.05                                  # the next one
    assert not [t for t, _ in hits(res, 9) if t > end + 0.01]              # then the drums stop
    bass = [(t, n) for t, n in hits(res, 1) if t >= end - 0.01]
    assert len(bass) == 1 and bass[0][1] % 12 == 2                         # the tonic, D
    pad = [n % 12 for t, n in hits(res, 0) if abs(t - end) < 0.05]
    assert pad and set(pad) <= {2, 5, 9, 4}                                # Dm(add9)
    assert res.engine.finished and res.engine.muted


def test_the_drums_fill_into_the_end_and_the_pad_fades():
    cfg, res = finished_run(finish_at=7.5)                 # before beat 4: room for a fill
    crash = [t for t, n in hits(res, 9) if n == GM_DRUMS["crash"]][-1]
    before = [n for t, n in hits(res, 9) if crash - PERIOD - 0.01 <= t < crash]
    assert GM_DRUMS["snare"] in before and len(before) >= 4                # a fill
    cc = [m.value for t, m in res.timeline if m.type == "control_change" and m.control == 11
          and t > crash]
    assert cc and cc[0] > cc[-1] and cc[-1] < 20                           # fading out


def test_after_an_ending_a_count_off_starts_again():
    cfg, res = finished_run(extra={})
    taps = [(1.0 + i * PERIOD, "tap_tempo") for i in range(4)]
    again = [(16.0 + i * PERIOD, "tap_tempo") for i in range(3)]
    notes = [(0.2 + i * 0.6, 62, 90) for i in range(12)]
    res = simulate.run(cfg, onsets=notes, actions=taps + [(8.0, "finish")] + again, total=22.0)
    assert not res.engine.muted and res.engine.groove.label().startswith("3/4")
    assert [t for t, _ in hits(res, 9) if t > 16.0 + 3 * PERIOD]           # playing again


def test_finish_with_nothing_playing_says_so():
    from accompanist.controller import Controller
    from accompanist.output import RecordingPort, SafeOutput

    ctl = Controller(c.from_dict({}), SafeOutput(RecordingPort()))
    assert ctl.do("finish", 0.0) == "nothing playing to finish"
