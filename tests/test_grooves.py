"""The groove-library matcher, on small synthetic templates (no dataset needed)."""
import json

import numpy as np
import pytest

from accompanist import grooves

GRID = 144


def library(tmp_path):
    def template(name, meter, beats, positions):
        p = [0.0] * GRID
        for x, prob in positions:
            p[int(round(x * GRID)) % GRID] = prob
        return {"name": name, "meter": meter, "beats": beats, "p": p, "bars": 100}
    waltz = template("waltz 3/4", "3/4", [3, 6], [(0, 1.0), (1 / 3, 0.4), (2 / 3, 0.8)])
    jig = template("jig 6/8", "6/8", [2, 6], [(k / 6, v) for k, v in enumerate((1, .5, .8, 1, .5, .9))])
    reel = template("reel 4/4", "4/4", [4, 2, 8], [(k / 8, v) for k, v in
                                                     enumerate((1, .3, .8, .5, .9, .3, .8, .5))])
    f = tmp_path / "library.json"
    f.write_text(json.dumps({"grid": GRID, "templates": [waltz, jig, reel]}))
    return grooves.load(f)


def played(pattern, beats_per_bar, bars, offset):
    """[(beat, pos)] for `pattern` (positions 0-1 in a bar) repeated, 1 at beat `offset`."""
    notes = []
    for b in range(bars):
        for x in pattern:
            beat_x = offset + b * beats_per_bar + x * beats_per_bar
            beat = int(np.floor(beat_x))
            notes.append((beat, beat_x - beat))
    return notes


def test_missing_library_loads_as_none(tmp_path):
    assert grooves.load(tmp_path / "nope.json") is None


@pytest.mark.parametrize("offset", [0, 1, 2])
def test_a_waltz_and_its_one_are_recognised_in_two_bars(tmp_path, offset):
    lib = library(tmp_path)
    m = grooves.recognise(played([0, 2 / 3], 3, 6, offset), lib, last_bars=2)
    assert m.template.name == "waltz 3/4" and m.bar_beats == 3 and m.downbeat == offset % 3
    assert m.margin > 0


def test_a_jig_at_the_eighth_note_pulse(tmp_path):
    # (Against the waltz this rhythm is truly ambiguous: 6/8 vs 3/4 from onsets alone. So
    # this checks the mechanics, bar length at the eighth pulse and "1", against a reel.)
    lib = [t for t in library(tmp_path) if t.name != "waltz 3/4"]
    m = grooves.recognise(played([0, 2 / 6, 3 / 6, 5 / 6], 6, 4, 4), lib, last_bars=2)
    assert m.template.meter_class == "6/8"
    # Heard as 6 eighths or as 2 dotted-quarter beats: either way, its 1s are the real 1s.
    assert m.bar_beats in (2, 6) and (4 - m.downbeat) % m.bar_beats == 0


def test_bar_lengths_can_be_restricted(tmp_path):
    lib = library(tmp_path)
    m = grooves.recognise(played([0, 2 / 3], 3, 6, 0), lib, bar_beats_allowed={4})
    assert m.bar_beats == 4
