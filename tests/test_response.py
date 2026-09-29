import random

import pytest

from accompanist import config as c
from accompanist.controller import Controller
from accompanist.output import RecordingPort, SafeOutput
from accompanist.response import make_answer

CH = 3   # response channel 4, 0-based


def cfg(**r):
    return c.from_dict({"response": {"enabled": True, "chance": 1.0, **r},
                        "harmony": {"root": "C"}, "lock": {"auto": False}})


def controller(**r):
    port = RecordingPort()
    return port, Controller(cfg(**r), SafeOutput(port))


def run(ctl, notes, until, start=0.0, dt=0.005):
    i, now, sent = 0, start, []
    port = ctl.engine.out._port
    while now <= until:
        while i < len(notes) and notes[i][0] <= now:
            ctl.on_note(*notes[i])
            i += 1
        n = len(port.sent)
        ctl.tick(now)
        sent += [(now, m) for m in port.sent[n:]]
        now = round(now + dt, 6)
    return sent


PHRASE = [(0.0, 60, 90), (0.5, 64, 90), (1.0, 67, 90), (1.5, 72, 100)]   # C E G C, then a pause


def answer_notes(sent):
    return [(t, m.note) for t, m in sent if m.type == "note_on" and m.channel == CH]


def test_a_pause_after_a_phrase_gets_an_answer_in_the_gap():
    _, ctl = controller()
    sent = run(ctl, PHRASE, 6.0)
    ans = answer_notes(sent)
    assert 1 <= len(ans) <= 6
    assert ans[0][0] >= 1.5 + 0.4                       # only after the pause
    assert all(abs(n - 64) <= 9 for _, n in ans)          # in the response register (octave 4)


def test_the_answer_stops_the_moment_you_play_again():
    _, ctl = controller(min_notes=3)
    first = answer_notes(run(ctl, PHRASE, 6.0))[0][0]    # when the answer begins
    _, ctl = controller(min_notes=3)
    back_in = first + 0.05                                 # come back in during the answer
    sent = run(ctl, PHRASE + [(back_in, 62, 90)], back_in + 0.3)
    assert [t for t, _ in answer_notes(sent) if t > back_in] == []
    sounding = {n for ch, n in ctl.engine.out.sounding if ch == CH}
    assert sounding == set()


def test_short_phrases_and_zero_chance_get_no_answer():
    _, ctl = controller()
    assert answer_notes(run(ctl, PHRASE[:2], 5.0)) == []  # 2 notes < min_notes
    _, ctl = controller(chance=0.0)
    assert answer_notes(run(ctl, PHRASE, 5.0)) == []


def test_off_by_default():
    port = RecordingPort()
    ctl = Controller(c.from_dict({"harmony": {"root": "C"}}), SafeOutput(port))
    assert answer_notes(run(ctl, PHRASE, 5.0)) == []


def test_answers_stay_in_the_key_and_land_on_a_chord_tone():
    r = cfg().response
    scale = {0, 2, 4, 5, 7, 9, 11}
    for seed in range(30):
        ans = make_answer([(t, n, v) for t, n, v in PHRASE], {7, 11, 2}, scale,
                          random.Random(seed), r, 0.5)
        assert all(n % 12 in scale for _, n, _, _ in ans)
        assert ans[-1][1] % 12 in {7, 11, 2}              # ends on a G-chord tone
        assert [o for o, *_ in ans] == sorted(o for o, *_ in ans)


def test_answers_are_repeatable():
    def once():
        _, ctl = controller()
        return answer_notes(run(ctl, PHRASE * 1, 6.0))
    assert once() == once()
