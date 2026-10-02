"""The piano: an arpeggio of the chord, on the beat, after your phrase (and the guitar)."""
import pytest

from accompanist import config as c, simulate
from accompanist.piano import arpeggio

PERIOD = 0.5          # 120 bpm


def test_an_arpeggio_climbs_the_chord_and_lands_on_the_root():
    notes = arpeggio({2, 5, 9}, 2, 62, 6, True, 55, 84)
    assert all(n % 12 in {2, 5, 9} for n in notes)
    assert notes[:5] == sorted(notes[:5]) and notes[-1] % 12 == 2


def test_an_arpeggio_turns_back_at_the_edge_of_its_range():
    notes = arpeggio({0, 4, 7}, 0, 70, 8, True, 60, 76)
    assert max(notes) <= 76 and notes != sorted(notes)


def run(piano=None, response=None, phrase_up=True):
    pitches = [62, 65, 69, 72] if phrase_up else [72, 69, 65, 62]
    notes = [(4.0 + i * 0.25, pitches[i % 4], 90) for i in range(8)]       # one phrase, then rest
    taps = [(1.0 + i * PERIOD, "tap_tempo") for i in range(4)]
    cfg = c.from_dict({"lock": {"auto": False}, "harmony": {"root": "D"},
                       "piano": {"enabled": True, "chance": 1.0, "subdivision": "eighths",
                                 **(piano or {})},
                       "response": {"enabled": False, **(response or {})}})
    res = simulate.run(cfg, onsets=notes, actions=taps, total=14.0)
    return [(t, m.note) for t, m in res.timeline if m.type == "note_on" and m.channel == 4], res


def test_the_piano_answers_on_the_beat_in_eighths_in_the_chord():
    played, res = run()
    assert len(played) >= 4
    downbeat = 1.0 + 4 * PERIOD
    first = played[0][0]
    assert first > 5.75                                                  # after your phrase
    for t, _ in played:                                                  # on the eighth grid
        x = (t - downbeat) / (PERIOD / 2)
        assert abs(x - round(x)) * PERIOD / 2 < 0.02
    chord = {n % 12 for n in res.engine.pad.current.notes}
    assert all(n % 12 in chord for _, n in played)


def test_the_piano_follows_your_phrase_up_or_down():
    up, _ = run(phrase_up=True)
    down, _ = run(phrase_up=False)
    assert up[1][1] > up[0][1] and down[1][1] < down[0][1]


def test_the_piano_waits_for_the_guitar():
    played, res = run(response={"enabled": True, "chance": 1.0})
    guitar = [t for t, m in res.timeline if m.type == "note_on" and m.channel == 3]
    assert guitar and played and played[0][0] >= max(guitar)


def test_you_playing_stops_the_piano():
    pitches = [62, 65, 69, 72]
    notes = [(4.0 + i * 0.25, pitches[i % 4], 90) for i in range(8)] + [(6.6, 62, 90)]
    taps = [(1.0 + i * PERIOD, "tap_tempo") for i in range(4)]
    cfg = c.from_dict({"lock": {"auto": False}, "harmony": {"root": "D"},
                       "piano": {"enabled": True, "chance": 1.0, "subdivision": "eighths",
                                 "max_beats": 8}})
    res = simulate.run(cfg, onsets=notes, actions=taps, total=14.0)
    played = [t for t, m in res.timeline if m.type == "note_on" and m.channel == 4]
    assert played and not [t for t in played if 6.62 < t < 9.0]
