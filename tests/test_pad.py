"""The pad voice: beat-synchronous changes and varied voicings."""
from accompanist import config as c
from accompanist.controller import clock
from accompanist.harmony import Voicing
from accompanist.output import RecordingPort, SafeOutput
from accompanist.responders import PadResponder


def pad(**cfg):
    port = RecordingPort()
    base = {"lag_beats": 0, "min_change_beats": 0, "variation": 0.0, "revoice_bars": 0}
    return port, PadResponder(c.from_dict({"pad": {**base, **cfg}}).pad, SafeOutput(port))


D = Voicing(2, 4, (50, 57, 62, 66))           # D major, pitch classes D F# A
G = Voicing(7, 4, (55, 62, 67, 71))           # G major


def test_voicing_keeps_the_root_in_the_bass_and_all_chord_tones():
    _, p = pad()
    for _ in range(2):
        p.update(0.0, D, 0.5)
    assert p.current.notes[0] == 50 and {n % 12 for n in p.current.notes} == {2, 6, 9}


def test_smooth_voice_leading_keeps_common_tones():
    _, p = pad()
    for t in (0.0, 0.1):
        p.update(t, D, 0.5)
    for t in (1.0, 1.1):
        p.update(t, G, 0.5)
    moved = sum(min(abs(n - m) for m in D.notes) for n in p.current.notes[1:])
    assert moved <= 6                              # upper voices move by small steps


def test_change_waits_for_the_beat_when_the_beat_is_known():
    _, p = pad(change_on="bar")
    for t in (0.0, 0.1):
        p.update(t, D, 0.5)                        # no beat known yet: applies at once
    assert p.current.root_pc == 2
    for t in (1.0, 1.1):
        p.update(t, G, 0.5, beat_known=True)
    assert p.current.root_pc == 2                  # ready, but waiting for the bar
    p.on_beat(1.5, 2)
    assert p.current.root_pc == 2                  # mid-bar: still waiting
    p.on_beat(2.0, 0)
    assert p.current.root_pc == 7                  # the downbeat


def test_a_static_chord_is_revoiced_every_few_bars():
    _, p = pad(revoice_bars=2)
    for t in (0.0, 0.1):
        p.update(t, D, 0.5)
    first = p.current.notes
    for bar in range(1, 3):
        p.on_beat(bar * 2.0, 0)
    assert p.current.notes != first and p.current.root_pc == 2
    assert {n % 12 for n in p.current.notes} == {2, 6, 9}


def test_variation_is_repeatable_with_the_same_seed():
    def run(seed):
        port = RecordingPort()
        p = PadResponder(c.from_dict({"pad": {"lag_beats": 0, "min_change_beats": 0,
                                               "variation": 1.0}}).pad, SafeOutput(port), seed)
        out = []
        for i, ch in enumerate([D, G, D, G, D]):
            for t in (i, i + 0.1):
                p.update(t, ch, 0.5)
            out.append(p.current.notes)
        return out
    assert run(1) == run(1)


def test_clock_rounds_cleanly():
    assert clock(59.96) == "1:00.0" and clock(125.44) == "2:05.4" and clock(None) == "-:--.-"
