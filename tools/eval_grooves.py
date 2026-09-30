"""How well does the groove library recognise grooves? Offline, on tunes it never saw.

For each held-out tune: its bars as notes in beats of the pulse (at the groove's natural
beat), numbered from an arbitrary beat (so '1' must be found), with timing slop added to
the (quantised) folk tunes. At every bar, the last 1, 2 or 4 bars are recognised; we count
how often the meter class and the downbeat are right. The current listening (Groove, which
needs ~24 beats) is scored on the same windows for comparison.

    python tools/eval_grooves.py
"""
from __future__ import annotations

import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from build_grooves import BEATS, GRID, collect, held_out   # noqa: E402
from accompanist import config as c, grooves          # noqa: E402
from accompanist.groove import Groove                  # noqa: E402

JITTER = 0.03          # timing slop added to quantised tunes, share of a beat (s.d.)


def ones_agree(pred_len, pred_down, true_len, true_down) -> bool:
    """Do the predicted 1s fall on true 1s (or vice versa, for bars of half/double length)?"""
    if true_len % pred_len == 0:
        return (true_down - pred_down) % pred_len == 0
    if pred_len % true_len == 0:
        return (pred_down - true_down) % true_len == 0
    return False


def tune_notes(rows, natural_beats, rng, jitter):
    """[(beat, pos)] for consecutive bars of one tune, numbered from a random offset."""
    offset = rng.randrange(natural_beats)
    notes, bar_starts = [], []
    for i, cells in enumerate(rows):
        base = offset + i * natural_beats
        bar_starts.append(base)
        for g in cells:
            x = g / GRID * natural_beats + rng.gauss(0, jitter)
            beat = int(x // 1)
            notes.append((base + beat, x - beat))
    return notes, offset, bar_starts


def main() -> int:
    lib = grooves.load()
    if lib is None:
        print("Build the library first: python tools/build_grooves.py")
        return 1
    bars, tunes = collect()
    rng = random.Random(1)
    per_tune = defaultdict(list)
    for name, rows in bars.items():
        for tune, cells in rows:
            if held_out(tune):
                per_tune[(name, tune)].append(cells)
    score = defaultdict(lambda: [0, 0, 0])            # (groove, window) -> [n, meter ok, downbeat ok]
    base = defaultdict(lambda: [0, 0, 0])
    for (name, tune), rows in sorted(per_tune.items()):
        rows = rows[:24]
        meter = name.split()[-1]
        true_class = grooves.METER_CLASS.get(meter, meter)   # (a dropped groove is still tested)
        L = BEATS[meter][0]
        jitter = JITTER if tune.startswith("nottingham") else 0.0
        notes, offset, starts = tune_notes(rows, L, rng, jitter)
        for k in range(4, len(rows)):
            end = starts[k] + L
            window = [(b, p) for b, p in notes if b < end]
            for last in (1, 2, 4):
                m = grooves.recognise(window, lib, last_bars=last)
                s = score[(name, last)]
                s[0] += 1
                if m and m.template.meter_class == true_class:
                    s[1] += 1
                    if ones_agree(m.bar_beats, m.downbeat, L, offset):
                        s[2] += 1
            g = Groove(c.from_dict({}).groove)                # the current listening
            for b, p in window:
                g.observe(float(b + p), b, p, 90)
            g.update(float(end), 1.0)
            meter = {3: "3", 4: "4"}.get(g.meter)
            bb = base[name]
            bb[0] += 1
            bb[1] += meter == true_class
            bb[2] += meter == true_class and ones_agree(g.meter, g.downbeat, L, offset)
    print(f"{'groove':16s} {'bars heard':>10s}   meter right   downbeat right   (current listening: meter / downbeat)")
    for name in sorted({n for n, _ in score}):
        for last in (1, 2, 4):
            n, mo, do = score[(name, last)]
            extra = ""
            if last == 4:
                bn, bm, bd = base[name]
                extra = f"   ({bm / bn:4.0%} / {bd / bn:4.0%})" if bn else ""
            print(f"{name:16s} {last:10d}   {mo / n:10.0%}   {do / n:13.0%}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
