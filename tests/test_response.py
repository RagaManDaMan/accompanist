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
    assert all(60 <= n <= 72 for _, n in ans)             # in your register: your own notes


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
    _, ctl = controller(min_notes=3)
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


def test_one_note_at_a_time_and_loud_enough():
    _, ctl = controller()
    sent = run(ctl, PHRASE, 6.0)
    sounding, most = set(), 0
    for _, m in sent:
        if m.channel == CH and m.type == "note_on":
            sounding.add(m.note)
            most = max(most, len(sounding))
        elif m.channel == CH and m.type == "note_off":
            sounding.discard(m.note)
    assert most == 1
    vels = [m.velocity for _, m in sent if m.channel == CH and m.type == "note_on"]
    assert min(vels) >= 60                              # ~ response.velocity, shaped by accents


def test_not_yielding_lets_the_answer_finish_over_you():
    _, ctl = controller(yield_to_you=False)
    first = answer_notes(run(ctl, PHRASE, 6.0))
    _, ctl = controller(yield_to_you=False)
    back_in = first[0][0] + 0.05
    sent = run(ctl, PHRASE + [(back_in, 62, 90)], first[-1][0] + 0.1)
    assert answer_notes(sent) == first                  # the whole answer, unchanged


def test_with_the_beat_running_answers_land_on_the_eighth_note_grid():
    """Loose (audio-like) timing in; the answer comes in on a beat, every note on an 8th."""
    from accompanist import simulate

    period = 60 / 100
    loose = []
    t = 1.0
    for bar in range(8):                                   # 3-note phrases, then a pause
        for k, n in enumerate((60, 64, 67)):
            loose.append((t + k * period / 2 + (0.03 if k == 1 else -0.02), n, 90))
        t += 4 * period
    config = c.from_dict({"response": {"enabled": True, "chance": 1.0},
                          "harmony": {"root": "C"}, "lock": {"auto": False},
                          "tempo": {"prior_bpm": 100}})
    res = simulate.run(config, onsets=loose, total=t)
    beats = [t for t, m in res.timeline if m.type == "note_on" and m.channel == 1]
    settled = beats[0] + 4 * period                        # answers made once the beat was known
    answers = [t for t, m in res.timeline if m.type == "note_on" and m.channel == CH and t > settled]
    assert answers
    for a in answers:
        prev = max(b for b in beats if b <= a + 0.01)
        nxt = min(b for b in beats if b > prev)
        x = (a - prev) / ((nxt - prev) / 2)
        assert abs(x - round(x)) * (nxt - prev) / 2 < 0.02            # on an 8th (5 ms ticks)


def test_variety_zero_keeps_the_motif_shape():
    r = c.from_dict({"response": {"variety": 0.0}}).response
    phrase = [(0.0, 60, 90), (0.3, 62, 90), (0.6, 64, 90), (0.9, 67, 90)]
    for seed in range(20):
        ans = make_answer(phrase, {0, 4, 7}, {0, 2, 4, 5, 7, 9, 11}, random.Random(seed), r, 0.6)
        steps = [b[1] - a[1] for a, b in zip(ans, ans[1:])]
        assert all(s >= 0 for s in steps[:-1])                      # still rising, like yours


# ---- curating your phrases -------------------------------------------------------------------
from accompanist.response import choose_phrase


def test_the_answer_is_one_of_your_earlier_phrases_that_fits():
    r = cfg().response
    earlier = [[(0, 67, 90), (0.3, 71, 90), (0.6, 74, 90)],        # G B D: fits G7
               [(0, 61, 90), (0.3, 63, 90), (0.6, 66, 90)]]        # C# D# F#: does not
    current = [(0, 60, 90), (0.3, 64, 90)]
    for seed in range(10):
        phrase, i = choose_phrase(current, earlier, {7, 11, 2, 5}, {0, 2, 4, 5, 7, 9, 11},
                                  random.Random(seed), r)
        assert i == 0 and [n for _, n, _ in phrase] == [67, 71, 74]


def test_with_nothing_that_fits_it_echoes_your_last_phrase():
    r = cfg().response
    phrase, i = choose_phrase([(0, 60, 90), (0.3, 64, 90)], [[(0, 61, 90), (0.3, 66, 90)]],
                              {0, 4, 7}, {0, 2, 4, 5, 7, 9, 11}, random.Random(0), r)
    assert i is None and [n for _, n, _ in phrase] == [60, 64]


def test_live_answers_replay_your_own_phrases_in_your_register():
    _, ctl = controller()
    first = [(0.0, 62, 90), (0.4, 65, 90), (0.8, 69, 90)]          # D F A
    second = [(4.0, 60, 90), (4.4, 64, 90), (4.8, 67, 90)]         # C E G
    sent = run(ctl, first + second, 8.0)
    after_second = [n for t, n in answer_notes(sent) if t > 4.8]
    assert after_second and set(after_second) <= {62, 65, 69, 60, 64, 67}   # only your notes


def test_yield_is_a_sliding_scale_and_old_true_false_still_works():
    assert cfg(yield_to_you=False).response.yield_to_you == 0.0
    assert cfg(yield_to_you=True).response.yield_to_you == 1.0

    def answered_after_back_in(y):
        _, ctl = controller(yield_to_you=y, variety=0.0)
        full = answer_notes(run(ctl, PHRASE, 8.0))
        _, ctl = controller(yield_to_you=y, variety=0.0)
        back_in = full[0][0] + 0.01
        sent = run(ctl, PHRASE + [(back_in, 62, 90)], full[-1][0] + 0.1)
        return len(full), len([t for t, _ in answer_notes(sent) if t > back_in])
    total, none = answered_after_back_in(1.0)
    _, half = answered_after_back_in(0.5)
    _, allrest = answered_after_back_in(0.0)
    assert none == 0 and allrest == total - 1 and 0 < half < allrest


def test_a_fixed_octave_moves_the_phrase_but_keeps_its_shape():
    _, ctl = controller(octave=6)
    ans = [n for _, n in answer_notes(run(ctl, PHRASE, 6.0))]
    assert ans and all(n >= 72 for n in ans)
    assert [b - a for a, b in zip(ans, ans[1:])] == [b[1] - a[1] for a, b in zip(PHRASE, PHRASE[1:])][:len(ans) - 1]
