"""Walking bass and the two-feel."""
import random

from accompanist import config as c, simulate
from accompanist.walking import Walker

C7, F7, G7 = {0, 4, 7, 10}, {5, 9, 0, 3}, {7, 11, 2, 5}
SCALE = {0, 2, 4, 5, 7, 9, 10}


def walk_changes(changes, seed=0, skip=0.0):
    """changes: [(root pc, chord pcs, beats)]: the notes walked, beat by beat."""
    w = Walker(36, random.Random(seed))
    out = []
    for i, (root, pcs, beats) in enumerate(changes):
        nxt = changes[(i + 1) % len(changes)][0]
        for b in range(beats):
            notes = w.walk(b % 4, root, pcs, SCALE, nxt, beats - b, skip)
            out.append((root, pcs, beats - b, notes))
    return out


def test_every_new_chord_lands_on_its_root_from_an_approach():
    changes = [(0, C7, 4), (5, F7, 4), (0, C7, 2), (7, G7, 2)] * 6
    for seed in range(20):
        played = walk_changes(changes, seed)
        prev = None
        for root, pcs, left, notes in played:
            n = notes[0].pitch
            if prev is not None and prev[0] != root:
                assert n % 12 == root                                  # the root on arrival
                assert abs(n - prev[3][0].pitch) <= 7                  # from close by
            prev = (root, pcs, left, notes)


def test_a_walk_never_sits_on_one_note_and_stays_in_range():
    played = walk_changes([(0, C7, 8), (5, F7, 8)] * 8, seed=3)
    pitches = [notes[0].pitch for *_, notes in played]
    assert all(a != b for a, b in zip(pitches, pitches[1:]))
    assert all(28 <= p <= 55 for p in pitches)
    assert len(set(pitches)) >= 8                                      # it moves around


def test_skips_are_swung_ghosts_and_the_two_feel_plays_half_notes():
    played = walk_changes([(0, C7, 4)] * 40, seed=1, skip=1.0)
    skips = [n for *_, notes in played for n in notes[1:]]
    assert skips and all(abs(n.at - 2 / 3) < 1e-9 and n.level < 1 for n in skips)
    w = Walker(36, random.Random(0))
    beats = [w.two(b, 4, 0, C7, SCALE, 5, 4 - b) for b in range(4)]
    assert [bool(x) for x in beats] == [True, False, True, False]
    assert beats[0][0].pitch % 12 == 0 and beats[0][0].length > 1.5


def test_the_band_walks_a_chart_and_meets_its_changes():
    cfg = c.load(None, overrides={"pulse": {"line": "walk", "rhythm": 0.0}},
                 song="lady-sings-the-blues")
    res = simulate.run(cfg, onsets=[], actions=[(1.0, "song_start")], total=40)
    bass = [m.note for t, m in res.timeline if m.type == "note_on" and m.velocity
            and m.channel == cfg.pulse.channel - 1]
    assert len(bass) >= 30 and len(set(bass)) >= 8                    # a note a beat at 66
    assert all(a != b for a, b in zip(bass, bass[1:]))
