"""Interludes: you rest, and the piano comps and the guitar plays your phrases, taking turns."""
from accompanist import config as c, simulate

PERIOD = 0.5          # 120 bpm


def run(rest_until=30.0, extra=None):
    phrases = []
    for k in range(4):                                     # four phrases, then a long rest
        start = 3.0 + k * 2.0
        phrases += [(start + i * 0.25, (62, 65, 69, 72, 69)[i], 90) for i in range(5)]
    back = [(rest_until + i * 0.25, 62 + i % 3, 90) for i in range(8)]
    taps = [(0.5 + i * PERIOD, "tap_tempo") for i in range(4)]
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                       "harmony": {"model": "modal", "root": "D", "mode": "minor"},
                       "piano": {"enabled": True, "share": 0.0, "chance": 0.0},
                       "response": {"enabled": True, "chance": 0.0},
                       **(extra or {})})
    res = simulate.run(cfg, onsets=phrases + back, actions=taps, total=rest_until + 4.0)
    return res


def notes(res, ch, lo, hi):
    return [(t, m.note) for t, m in res.timeline
            if m.type == "note_on" and m.channel == ch and lo <= t < hi]


def test_resting_the_piano_comps_then_the_guitar_takes_a_turn():
    res = run()
    last = 3.0 + 3 * 2.0 + 1.0                             # your last note
    start = last + 6 * PERIOD                              # interlude.after_beats later
    piano = notes(res, 4, start, start + 8.0)              # piano's 4 bars of 4/4
    chords = {}
    for t, n in piano:
        chords.setdefault(round(t, 2), []).append(n)
    assert len(chords) >= 4 and max(len(v) for v in chords.values()) >= 3   # chords, often
    guitar = notes(res, 3, start, start + 20.0)            # then the guitar's turn...
    assert guitar and guitar[0][0] > piano[0][0] + 6.0
    g0 = guitar[0][0]                                       # ...without the piano
    assert not notes(res, 4, g0 - 0.2, g0 + 6.0)


def test_coming_back_in_ends_the_interlude():
    res = run(rest_until=24.0)
    assert not notes(res, 4, 24.3, 28.0) and not notes(res, 3, 24.6, 28.0)
    assert not res.engine.in_interlude


def test_interludes_can_be_turned_off():
    res = run(extra={"interlude": {"enabled": False}})
    assert not notes(res, 4, 14.0, 30.0)
