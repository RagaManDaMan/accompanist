"""Real drummers' grooves and fills, for the drums to play instead of a fixed pattern.

Built from the Groove MIDI Dataset (Google Magenta, CC BY 4.0: Gillick, Roberts, Engel,
Eck & Bamman, "Learning to Groove with Inverse Sequence Transformations", ICML 2019):
13.6 hours of professional drummers on an electronic kit, in many styles, with their
own timing and dynamics. `accompanist library drums DATASET_DIR` turns it into a groove
library (library.path/drums.json); the drums then play its bars, in 4/4, choosing a groove
of the song's style (drums.style) near the tempo, and a fill bar at phrase ends.

A bar is [(beat within the bar, GM note, velocity)], with each hit's offset from the grid
kept: the drummer's feel moves with the tempo.
"""
from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CREDIT = ("Groove MIDI Dataset, Google Magenta, CC BY 4.0 "
          "(magenta.withgoogle.com/datasets/groove)")
BEATS = 4                              # 4/4 only (nearly all of the dataset)
MIN_HITS = 4                           # a bar with fewer is a pickup or the end
# The electronic kit's notes -> General MIDI (edges, rims and second cymbals folded in).
KIT_TO_GM = {22: 42, 26: 46, 58: 43, 47: 45, 50: 48, 55: 49, 57: 49, 52: 49, 59: 51, 40: 38}
TEMPO_RATIO = 1.35                     # a groove fits tempi within this ratio of its own


@dataclass(frozen=True)
class Groove:
    style: str                         # e.g. "jazz/swing", "latin/samba", "funk"
    bpm: float
    kind: str                          # "beat" | "fill"
    drummer: str
    bars: tuple[tuple[tuple[float, int, int], ...], ...]

    @property
    def family(self) -> str:
        return self.style.split("/")[0]


def bars_from_midi(path: Path) -> list[tuple[tuple[float, int, int], ...]]:
    import mido

    m = mido.MidiFile(str(path))
    tpb, t, hits = m.ticks_per_beat, 0, []
    for msg in mido.merge_tracks(m.tracks):
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            hits.append((t / tpb, KIT_TO_GM.get(msg.note, msg.note), msg.velocity))
    bars: dict[int, list] = {}
    for beat, note, vel in hits:
        bar = int(beat // BEATS)
        bars.setdefault(bar, []).append((round(beat - bar * BEATS, 3), note, vel))
    return [tuple(sorted(b)) for _, b in sorted(bars.items()) if len(b) >= MIN_HITS]


def build(dataset: Path, out: Path) -> int:
    """The dataset folder (with info.csv) -> drums.json. Returns how many grooves."""
    grooves = []
    with open(dataset / "info.csv", newline="") as f:
        for row in csv.DictReader(f):
            if row["time_signature"] != "4-4":
                continue
            bars = bars_from_midi(dataset / row["midi_filename"])
            if not bars:
                continue
            if row["beat_type"] == "fill":
                bars = bars[-1:]                       # the fill itself: the last bar
            grooves.append({"style": row["style"], "bpm": float(row["bpm"]),
                            "kind": row["beat_type"], "drummer": row["drummer"],
                            "bars": [[list(h) for h in b] for b in bars]})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"credit": CREDIT, "grooves": grooves}))
    return len(grooves)


class DrumBook:
    def __init__(self, grooves: list[Groove]) -> None:
        self.grooves = grooves

    @staticmethod
    def open(library: str | Path) -> Optional["DrumBook"]:
        f = Path(library).expanduser() / "drums.json"
        if not f.is_file():
            return None
        data = json.loads(f.read_text())
        return DrumBook([Groove(g["style"], g["bpm"], g["kind"], g["drummer"],
                                tuple(tuple((float(b), int(n), int(v)) for b, n, v in bar)
                                      for bar in g["bars"]))
                         for g in data["grooves"]])

    def styles(self) -> list[str]:
        return sorted({g.style for g in self.grooves} | {g.family for g in self.grooves})

    def choose(self, style: str, bpm: float, kind: str, rng: random.Random,
               avoid: Optional[Groove] = None) -> Optional[Groove]:
        """A groove (or fill) of this style ("jazz", or exactly "jazz/swing"), near the tempo."""
        same = [g for g in self.grooves if g.kind == kind and (g.style == style or g.family == style)]
        if not same:
            return None
        near = [g for g in same if 1 / TEMPO_RATIO <= g.bpm / bpm <= TEMPO_RATIO and g is not avoid]
        pool = near or sorted(same, key=lambda g: abs(g.bpm - bpm))[:3]
        return rng.choice(pool)
