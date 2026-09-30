"""Groove lock and pulse hysteresis, tested by replaying takes through the engine."""
from pathlib import Path

import pytest

from accompanist import config as c, simulate
from accompanist.controller import Controller, format_status
from accompanist.output import RecordingPort, SafeOutput
from accompanist.recording import load_take

FIXTURES = Path(__file__).parent / "fixtures"
PULSE_CH = 2
OFFSET = 1.0     # replay starts the take 1 s in, as `accompanist replay` does


def replay(name, cfg=None, actions=None, tail=30.0):
    onsets = [(t + OFFSET, n, v) for t, n, v in load_take(FIXTURES / name)]
    return simulate.run(cfg or c.from_dict({}), onsets=onsets, actions=actions,
                        total=onsets[-1][0] + tail)


def lock_time(res):
    return next(t for t, what in res.log if what == "LOCKED")


def grid_error(t, bpm):
    p = 60.0 / bpm
    x = (t - OFFSET) / p
    return abs(x - round(x)) * p


def assert_locked_groove(res, bpm, start, settle=0.0):
    """From `start` on: the pulse never stops; from `start + settle` on, every beat is within
    60 ms of the grid."""
    period = 60.0 / bpm
    beats = [t for t in res.notes_on(PULSE_CH) if t >= start]
    assert beats[-1] >= res.end_time - 1.5 * period                 # still going at the very end
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    assert max(gaps) <= 1.1 * period                                # never stopped, never skipped
    assert max(grid_error(b, bpm) for b in beats if b >= start + settle) <= 0.060


def test_steady_take_with_a_pause_locks_and_keeps_the_groove():
    # 90 bpm, ~44 s of playing, 20 s of silence, then back in on the same beat. The replay
    # runs 30 s past the last note: well past pulse.idle_stop_s and pad.idle_release_s.
    res = replay("steady-90bpm-pause20.jsonl")
    t_lock = lock_time(res)
    assert t_lock < 45.0 + OFFSET                                   # locked before the pause
    assert_locked_groove(res, 90, t_lock)
    assert res.engine.locked
    assert res.engine.pad.current is not None                       # pad held through silence
    assert all(what != "unlocked" for _, what in res.log)


def test_rubato_take_locked_by_key_stays_on_the_grid():
    # 80 bpm melody, each note up to 15% of its interval off the beat. Lock pressed at 30 s.
    press = 30.0 + OFFSET
    res = replay("rubato-80bpm.jsonl", actions=[(press, "lock")])
    assert lock_time(res) == pytest.approx(press, abs=0.01)
    # A key press is not music: the beat does not jump when you press lock...
    beats = res.notes_on(PULSE_CH)
    before = [b for b in beats if b <= press][-2:]
    after = next(b for b in beats if b > press)
    assert after - before[1] == pytest.approx(before[1] - before[0], rel=0.05)
    # ...and once it has settled on your notes, it stays on the grid.
    assert_locked_groove(res, 80, press, settle=10.0)


def test_without_a_lock_the_pulse_stops_in_silence():
    res = replay("steady-90bpm-pause20.jsonl", c.from_dict({"lock": {"auto": False}}))
    beats = res.notes_on(PULSE_CH)
    assert max(b - a for a, b in zip(beats, beats[1:])) > 5.0       # it stopped for the pause
    assert not res.engine.clock.running and res.engine.pad.current is None


# ---- lock and unlock through the Controller ------------------------------------------------
def controller(d=None):
    port = RecordingPort()
    return Controller(c.from_dict(d or {}), SafeOutput(port))


def play(ctl, onsets, until, dt=0.01, start=0.0):
    i, now = 0, start
    while now <= until:
        while i < len(onsets) and onsets[i][0] <= now:
            ctl.on_note(*onsets[i])
            i += 1
        ctl.tick(now)
        now = round(now + dt, 6)


D_PHRASE = [62, 66, 69, 62, 64, 66, 69, 74]
G_MINOR = [67, 70, 74, 67, 69, 70, 74, 79]


def phrase(notes, bpm, start, beats):
    return [(start + i * 60 / bpm, notes[i % len(notes)], 90) for i in range(beats)]


def test_lock_needs_something_heard():
    ctl = controller()
    assert ctl.lock(0.0) is False and not ctl.get_state(0.0)["locked"]


def test_tempo_lock_does_not_freeze_the_chords():
    cfg = {"pad": {"lag_beats": 1, "min_change_beats": 1}, "lock": {"auto": False}}
    ctl = controller(cfg)
    play(ctl, phrase(D_PHRASE, 90, 0.0, 30), 20.0)
    assert ctl.lock(20.0)
    play(ctl, phrase(G_MINOR, 90, 20.0, 60), 60.0, start=20.01)
    s = ctl.get_state(60.0)
    assert s["locked"] and s["pad"] in ("Gm", "G5")                 # chords followed you


def test_chord_hold_freezes_the_chord_until_released():
    cfg = {"pad": {"lag_beats": 1, "min_change_beats": 1}, "lock": {"auto": False}}
    ctl = controller(cfg)
    play(ctl, phrase(D_PHRASE, 90, 0.0, 30), 20.0)
    assert ctl.do("chord_toggle", 20.0).startswith("chord HELD")
    play(ctl, phrase(G_MINOR, 90, 20.0, 60), 60.0, start=20.01)
    s = ctl.get_state(60.0)
    assert s["chord_held"] and not s["locked"] and s["pad"] in ("D", "D5")
    assert "CHORD HELD" in format_status(s) and "LOCKED" not in format_status(s)
    play(ctl, [], 90.0, start=60.01)                                # silence: still held
    assert ctl.get_state(90.0)["pad"] in ("D", "D5")
    ctl.do("chord_toggle", 90.0)
    play(ctl, phrase(G_MINOR, 90, 90.0, 30), 110.0, start=90.01)
    assert ctl.get_state(110.0)["pad"] in ("Gm", "G5")              # follows again


def test_panic_releases_a_held_chord():
    ctl = controller({"lock": {"auto": False}})
    play(ctl, phrase(D_PHRASE, 90, 0.0, 20), 10.0)
    assert ctl.hold_chord()
    ctl.panic()
    assert not ctl.get_state(10.0)["chord_held"]


def test_silence_never_ends_a_lock_only_unlock_or_panic_do():
    ctl = controller({"lock": {"auto": False}})
    play(ctl, phrase(D_PHRASE, 90, 0.0, 40), 30.0)
    assert ctl.lock(30.0) and ctl.engine.clock.running
    play(ctl, [], 200.0, start=30.01)                               # nearly three minutes of silence
    s = ctl.get_state(200.0)
    assert s["locked"] and s["pulse"] and s["pad"] is not None
    assert "LOCKED" in format_status(s)
    ctl.unlock()
    play(ctl, [], 201.0, start=200.01)
    assert not ctl.engine.clock.running and ctl.get_state(201.0)["pad"] is None

    play(ctl, phrase(D_PHRASE, 90, 202.0, 40), 230.0, start=201.01)
    assert ctl.lock(230.0)
    ctl.panic()
    assert not ctl.get_state(230.0)["locked"] and ctl.engine.out.sounding == set()


def test_auto_lock_needs_sustained_confidence_and_can_be_turned_off():
    notes = phrase(D_PHRASE, 90, 0.0, 70)
    ctl = controller({"lock": {"after_s": 25}})
    play(ctl, notes, 44.0)
    assert ctl.get_state(44.0)["locked"]
    ctl = controller({"lock": {"after_s": 60}})
    play(ctl, notes, 44.0)
    s = ctl.get_state(44.0)
    assert not s["locked"] and s["lock_in_s"] > 0                   # counting down, not there yet
    ctl = controller({"lock": {"auto": False}})
    play(ctl, notes, 44.0)
    assert not ctl.get_state(44.0)["locked"] and ctl.get_state(44.0)["lock_in_s"] is None


def test_after_a_manual_unlock_auto_lock_waits_to_be_rearmed():
    ctl = controller({"lock": {"after_s": 10}})
    notes = phrase(D_PHRASE, 90, 0.0, 120)
    play(ctl, notes, 30.0)
    assert ctl.get_state(30.0)["locked"]
    ctl.unlock()
    play(ctl, [n for n in notes if n[0] > 30.0], 70.0, start=30.01)
    assert not ctl.get_state(70.0)["locked"]                        # steady playing: stays unlocked


def test_lock_and_unlock_from_a_controller():
    ctl = controller({"controls": {"80": "lock", "81": "unlock"}, "lock": {"auto": False}})
    play(ctl, phrase(D_PHRASE, 90, 0.0, 20), 10.0)
    ctl.on_cc(10.0, 80, 127)
    assert ctl.get_state(10.0)["locked"]
    ctl.on_cc(10.0, 81, 127)
    assert not ctl.get_state(10.0)["locked"]


# ---- pulse hysteresis ------------------------------------------------------------------------
def test_pulse_starts_at_min_confidence_and_stops_only_below_stop_confidence():
    ctl = controller({"lock": {"auto": False}, "pulse": {"min_confidence": 0.5, "stop_confidence": 0.15}})
    play(ctl, phrase(D_PHRASE, 90, 0.0, 30), 15.0)
    eng = ctl.engine
    assert eng.clock.running
    eng.tempo.confidence = 0.3                                      # below start, above stop
    ctl.tick(15.001)
    assert eng.clock.running
    eng.tempo.confidence = 0.1                                      # below stop...
    ctl.tick(15.002)
    assert eng.clock.running                                        # ...a dip is not enough
    ctl.tick(15.002 + ctl.cfg.pulse.stop_after_s * 0.5)
    eng.tempo.confidence = 0.1
    assert eng.clock.running
    ctl.tick(15.002 + ctl.cfg.pulse.stop_after_s + 0.01)            # ...it must stay low
    assert not eng.clock.running
    eng.tempo.confidence = 0.3                                      # not enough to restart
    ctl.tick(15.003 + ctl.cfg.pulse.stop_after_s + 0.02)
    assert not eng.clock.running


def test_stop_confidence_above_min_confidence_is_rejected():
    with pytest.raises(c.ConfigError, match="stop_confidence"):
        c.from_dict({"pulse": {"min_confidence": 0.3, "stop_confidence": 0.4}})


def test_pulse_starts_on_the_beat_not_on_an_off_beat_note():
    # A quarter then two eighths: the last note heard before the pulse starts is often an
    # off-beat eighth. (Even eighths alone would be ambiguous: no beat is audible.)
    notes = []
    for bar in range(20):
        t = bar * 2 * 60 / 90
        notes += [(t, 62, 90), (t + 60 / 90, 64, 70), (t + 90 / 90, 66, 70)]
    ctl = controller({"lock": {"auto": False}})
    port = ctl.engine.out._port
    times, i, now = [], 0, 0.0
    while now <= 25.0:
        while i < len(notes) and notes[i][0] <= now:
            ctl.on_note(*notes[i])
            i += 1
        n = len(port.sent)
        ctl.tick(now)
        times += [now for m in port.sent[n:] if m.type == "note_on" and m.channel == PULSE_CH - 1]
        now = round(now + 0.005, 6)
    assert times and max(grid_error(t + OFFSET, 90) for t in times) <= 0.060
    assert max(b - a for a, b in zip(times, times[1:])) <= 1.1 * 60 / 90   # steady, no flipping


def test_unlocking_never_stops_the_pulse_even_with_low_confidence():
    ctl = controller({"lock": {"auto": False}})
    play(ctl, phrase(D_PHRASE, 90, 0.0, 30), 15.0)
    assert ctl.lock(15.0)
    play(ctl, phrase(D_PHRASE, 90, 15.0, 30), 30.0, start=15.01)
    ctl.engine.tempo.confidence = 0.02                              # very unsure, then unlock
    ctl.do("lock_toggle", 30.0)
    assert not ctl.engine.locked
    for k in range(1, 30):                                          # the next 3 s
        ctl.engine.tempo.confidence = 0.02
        ctl.tick(30.0 + k * 0.1)
        assert ctl.engine.clock.running
