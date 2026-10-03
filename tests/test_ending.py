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


def test_the_ending_is_in_the_key_you_play_not_the_key_set():
    """Regression (takes/ms-6): the song set D minor, the sax played in F minor; the last
    chord was D minor."""
    f_minor = (65, 67, 68, 70, 72, 73, 75, 77)
    notes = [(0.2 + i * 0.3, f_minor[(i * 3) % 8], 80) for i in range(40)] + \
            [(12.0 + i * 0.6, (65, 68, 72)[i % 3], 90) for i in range(4)]
    taps = [(1.0 + i * PERIOD, "tap_tempo") for i in range(4)]
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                       "harmony": {"model": "modal", "root": "D", "mode": "minor"}})
    res = simulate.run(cfg, onsets=notes, actions=taps + [(14.0, "finish")], total=24.0)
    crash = [t for t, n in hits(res, 9) if n == GM_DRUMS["crash"]][-1]
    bass = [n for t, n in hits(res, 1) if t >= crash - 0.01]
    assert bass and bass[0] % 12 == 5                                     # F
    pad = {n % 12 for t, n in hits(res, 0) if abs(t - crash) < 0.05}
    assert 5 in pad and 8 in pad                                           # F minor


def test_landing_on_the_relative_minor_ends_there():
    """Regression (home take 2026-10-02): the band followed E-flat major; the player ended on
    C (the relative minor's tonic), and the ending should land with them."""
    eb_major = (63, 65, 67, 68, 70, 72, 74, 75)
    notes = [(0.2 + i * 0.3, eb_major[(i * 3) % 8], 80) for i in range(40)] + [(12.4, 60, 90)]
    taps = [(1.0 + i * PERIOD, "tap_tempo") for i in range(4)]
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                       "harmony": {"model": "modal"}})
    res = simulate.run(cfg, onsets=notes, actions=taps + [(13.0, "finish")], total=22.0)
    crash = [t for t, n in hits(res, 9) if n == GM_DRUMS["crash"]][-1]
    bass = [n for t, n in hits(res, 1) if t >= crash - 0.01]
    assert bass and bass[0] % 12 == 0                                     # C minor


def shaped(shape, finish_at=8.0, total=26.0, **extra):
    notes = [(0.2 + i * 0.3, (62, 65, 69)[i % 3], 90) for i in range(25)]
    taps = [(1.0 + i * PERIOD, "tap_tempo") for i in range(4)]
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                       "piano": {"enabled": True, "chance": 0.0, "share": 0.0},
                       "interlude": {"enabled": False},
                       "harmony": {"model": "modal", "root": "D", "mode": "minor"},
                       "ending": {"shape": shape, **extra}})
    res = simulate.run(cfg, onsets=notes, actions=taps + [(finish_at, "finish")], total=total)
    crash = [t for t, n in hits(res, 9) if n == GM_DRUMS["crash"]]
    return res, crash[-1] if crash else None


def test_a_button_ending_is_short():
    res, end = shaped("button")
    bass = [(t, m) for t, m in res.timeline if m.channel == 1 and t >= end - 0.01]
    on = [t for t, m in bass if m.type == "note_on"]
    off = [t for t, m in bass if m.type == "note_off"]
    assert on and off and off[0] - on[0] <= 0.5 * PERIOD + 0.05
    assert res.engine.finished


def test_a_tag_plays_more_bars_before_the_last_chord():
    _, chord_end = shaped("chord")
    _, tag_end = shaped("tag", tag_bars=4)
    assert tag_end == pytest.approx(chord_end + 4 * 4 * PERIOD, abs=0.1)


def test_a_ritardando_slows_into_a_held_last_chord():
    res, end = shaped("ritardando", rit_bars=2, rit_to=0.6)
    beats = sorted({round(t, 3) for t, n in hits(res, 9) if n == GM_DRUMS["kick"] and t < end})
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    assert gaps[-1] > gaps[0] * 1.3                                   # slower and slower
    _, chord_end = shaped("chord")
    assert end > chord_end + 2 * 4 * PERIOD                           # two (longer) bars later


def test_a_piano_tag_drops_the_band_for_soft_piano_then_ends():
    res, end = shaped("piano-tag")
    _, chord_end = shaped("chord")
    bar = (chord_end - 0.05, end - 0.05)
    assert not [t for t, n in hits(res, 1) if bar[0] < t < bar[1]]   # no bass in the tag bar
    piano = [m.velocity for t, m in res.timeline if m.type == "note_on" and m.channel == 4
             and bar[0] < t < bar[1]]
    assert len(piano) == 2 and max(piano) < 70                        # plink, plink...
    assert end == pytest.approx(chord_end + 4 * PERIOD, abs=0.1)      # ...PLUNK
