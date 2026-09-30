"""Recognising grooves by their signature: the groove library (tools/build_grooves.py).

Each template says, for each position in a bar, how often a melody note starts there in that
groove (swing, waltz, jig, march, reel, ...). To recognise the groove in the last bar or two
of your playing, every template is tried at every bar length it can be heard at (in beats
of the pulse) and every downbeat; the one under which your notes are most likely wins.

Pure: notes in as (beat number, position 0-1 within the beat), no clock, no MIDI. The
library is built locally from open datasets (see README); without it, nothing is loaded.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "grooves" / "library.json"
FLOOR = 0.002           # no note position is ever impossible
METER_CLASS = {"4/4": "4", "2/4": "4", "2/2": "4", "3/4": "3", "6/8": "6/8", "9/8": "9/8"}


@dataclass
class Template:
    name: str
    meter: str
    beats: list[int]            # bar lengths (in pulse beats) it can be heard at
    p: np.ndarray               # blurred onset probability per grid position

    @property
    def meter_class(self) -> str:
        return METER_CLASS.get(self.meter, self.meter)


@dataclass
class Match:
    template: Template
    bar_beats: int              # bar length in pulse beats
    downbeat: int               # beat numbers b with (b - downbeat) % bar_beats == 0 are 1
    score: float                # log-likelihood ratio per beat, vs a featureless groove
    margin: float               # over the best match of a different meter class


def load(path: Optional[Path] = None, blur: float = 0.035) -> Optional[list[Template]]:
    """The library, each template blurred by `blur` of a bar (timing slop) and floored.
    None if it has not been built."""
    p = Path(path or DEFAULT_PATH)
    if not p.is_file():
        return None
    data = json.loads(p.read_text())
    grid = data["grid"]
    sigma = blur * grid
    k = np.arange(-int(3 * sigma) - 1, int(3 * sigma) + 2)
    kernel = np.exp(-0.5 * (k / sigma) ** 2)
    kernel /= kernel.sum()
    out = []
    for t in data["templates"]:
        raw = np.asarray(t["p"], dtype=float)
        smooth = np.zeros(grid)
        for off, w in zip(k, kernel):                  # circular blur
            smooth += w * np.roll(raw, off)
        smooth = np.clip(smooth, FLOOR, 1 - FLOOR)
        out.append(Template(t["name"], t["meter"], t["beats"], smooth))
    return out


def bars_of(notes: list[tuple[int, float]], bar_beats: int, downbeat: int, grid: int,
            last_bars: int) -> list[np.ndarray]:
    """Your notes as occupied grid cells, one array per bar (the last `last_bars` bars)."""
    if not notes:
        return []
    by_bar: dict[int, set] = {}
    for beat, pos in notes:
        x = (beat - downbeat) + pos
        bar, within = divmod(x, bar_beats)
        by_bar.setdefault(int(bar), set()).add(int(round(within / bar_beats * grid)) % grid)
    newest = max(by_bar)
    out = []
    for b in range(newest - last_bars + 1, newest + 1):
        cells = np.zeros(grid, dtype=bool)
        cells[list(by_bar.get(b, ()))] = True
        out.append(cells)
    return out


def recognise(notes: list[tuple[int, float]], templates: list[Template],
              last_bars: int = 2, bar_beats_allowed: Optional[set[int]] = None) -> Optional[Match]:
    """The best (template, bar length, downbeat) for the last `last_bars` bars of `notes`
    [(beat number, position in beat)], with its margin over the best other meter class."""
    if not notes or not templates:
        return None
    grid = len(templates[0].p)
    results = []
    for t in templates:
        # Score the template's *shape*: log-likelihood ratio against a featureless template
        # with the same note density, per beat (so bar lengths and busy vs sparse grooves
        # compete fairly).
        q = float(t.p.mean())
        on_w = np.log(t.p / q)
        off_w = np.log((1 - t.p) / (1 - q))
        for L in t.beats:
            if bar_beats_allowed and L not in bar_beats_allowed:
                continue
            for d in range(L):
                bars = bars_of(notes, L, d, grid, last_bars)
                if not bars:
                    continue
                s = sum(float(np.where(b, on_w, off_w).sum()) for b in bars) / (len(bars) * L)
                results.append((s, t, L, d))
    if not results:
        return None
    results.sort(key=lambda r: -r[0])
    s, t, L, d = results[0]
    other = next((r[0] for r in results if r[1].meter_class != t.meter_class), None)
    return Match(t, L, d % L, s, (s - other) if other is not None else math.inf)
