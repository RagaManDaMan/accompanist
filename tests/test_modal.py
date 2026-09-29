import pytest

from accompanist import config as c
from accompanist.controller import Controller, format_status
from accompanist.harmony import Onset, make_model
from accompanist.modal import CHORDS, voice
from accompanist.output import RecordingPort, SafeOutput

C, Cs, D, Eb, E, F, Fs, G, Gs, A, Bb, B = range(60, 72)


def chord(notes, **harmony):
    """The best-fitting chord (wander off: these tests are about the fit itself)."""
    m = make_model(c.from_dict({"harmony": {"model": "modal", "wander": 0.0, **harmony}}))
    t = 0.0
    for n in notes:
        m.observe(Onset(t, n, 90))
        t += 0.4
    return m.propose(t).label(), m.key_label


@pytest.mark.parametrize("notes,expected", [
    ([C, E, G, E, C, G], "C"),
    ([F, A, C + 12, A, F], "F"),
    ([B, D + 12, F + 12, D + 12, B], "Bdim"),
    ([G, B, D + 12, F + 12, G], "G7"),
])
def test_diatonic_chords_in_c_major(notes, expected):
    assert chord(notes, root="C", mode="major") == (expected, "C major")


def test_a_major_key_keeps_out_chords_that_are_not_in_it():
    assert chord([Eb, G, Bb, G, Eb], root="C", mode="major")[0] != "D#"
    assert chord([Eb, G, Bb, G, Eb], root="C", mode="chromatic")[0] == "D#"


def test_minor_has_the_raised_seventh():
    assert chord([E, Gs, B, E + 12, Gs], root="A", mode="minor") == ("E", "A minor")


def test_colour_chords():
    assert chord([D, G, A, D + 12, G], root="D", mode="major")[0] == "Dsus4"
    assert chord([D, Fs, A, E + 12, D + 12], root="D", mode="major", color=1.0)[0] == "Dadd9"
    assert chord([D, A, D + 12, A], root="D", mode="major")[0] == "D5"   # no third played: open


def test_auto_detects_key_and_mode():
    assert chord([C, D, E, F, G, A, B, C + 12, G, E, C])[1] == "C major"
    assert chord([A, B, C + 12, D + 12, E + 12, F + 12, Gs + 12, A + 12, E + 12, C + 12, A])[1] == "A minor"
    assert chord([D, F, A, D + 12, F, A, C + 12, D], root="D")[1] == "D minor"


def test_voicings_are_in_range_and_contain_the_chord():
    for suffix, ivs, _ in CHORDS:
        for root in range(12):
            notes = voice(root, ivs, 3)
            assert all(36 <= n <= 96 for n in notes)
            assert {n % 12 for n in notes} == {(root + i) % 12 for i in ivs}


def test_mode_changes_live_and_key_shows_in_state():
    port = RecordingPort()
    ctl = Controller(c.from_dict({"harmony": {"model": "modal", "root": "D", "mode": "major"}}),
                     SafeOutput(port))
    for i, n in enumerate([D, F, A, D + 12, F, A]):
        ctl.on_note(i * 0.4, n, 90)
    ctl.tick(2.5)
    assert ctl.get_state(2.5)["key"] == "D major"
    ctl.set_param("harmony.mode", "minor")
    ctl.tick(2.6)
    s = ctl.get_state(2.6)
    assert s["key"] == "D minor" and "key D minor" in format_status(s)


def test_pad_follows_a_progression():
    """I - vi - IV - V in C, two bars each: the pad moves through them (with its lag)."""
    port = RecordingPort()
    cfg = c.from_dict({"harmony": {"model": "modal", "root": "C", "mode": "major", "chord_memory_s": 2},
                       "pad": {"lag_beats": 1, "min_change_beats": 2}, "lock": {"auto": False}})
    ctl = Controller(cfg, SafeOutput(port))
    seen, t = [], 0.0
    for tones in ([C, E, G], [A, C + 12, E + 12], [F, A, C + 12], [G, B, D + 12]):
        for i in range(12):
            ctl.on_note(t, tones[i % 3], 90)
            for k in range(50):
                ctl.tick(t + k * 0.01)
            t += 0.5
            root = ctl.get_state(t)["root"] if ctl.get_state(t)["pad"] else None
            if root and (not seen or seen[-1] != root):
                seen.append(root)
    assert seen == ["C", "A", "F", "G"]         # chord colour (Am7, Fmaj7...) depends on color
