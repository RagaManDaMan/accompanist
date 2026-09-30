"""Build the groove library: where melody notes start within a bar, per groove.

Reads open datasets downloaded into ./datasets/ (not shipped with accompanist; see README):
  - Weimar Jazz Database, datasets/wjazzd.db (Open Database License; Jazzomat Research
    Project): jazz solos with performed onsets and beat times -> swing, jazz waltz, latin,
    funk, ballad, two-beat. Positions are *performed* (so swing is in the template).
  - Nottingham Music Database, cleaned by Jukedeck, datasets/nottingham-dataset-master/MIDI
    (GPL-3.0): folk tunes -> reel, hornpipe, 2/4, waltz, jig (6/8), slip jig (9/8).
Writes grooves/library.json (local, not in git: built from the datasets on each machine).

Each template: for each of GRID positions in a bar, the share of bars with a note starting
there; plus which bar lengths (in beats of the band's pulse) the groove can be heard at.
One tune in five is held out (tests), listed in the file, for honest evaluation.

    python tools/build_grooves.py
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import mido

MIN_TUNES = 10                 # a groove learnt from fewer tunes is not trusted
GRID = 144                     # positions per bar: 16ths and triplets in 4/4, 8ths in 6/8 and 9/8
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "datasets"
OUT = ROOT / "grooves" / "library.json"

# Bar lengths, in beats of the band's pulse, each groove can be heard at (the pulse may be
# the quarter, or for fast tunes half-time / for compound meters the eighth).
BEATS = {"4/4": [4, 2, 8], "3/4": [3, 6], "2/4": [2, 4], "2/2": [2, 4], "6/8": [2, 6],
         "9/8": [3, 9]}


def held_out(name: str) -> bool:
    return int(hashlib.md5(name.encode()).hexdigest(), 16) % 5 == 0


def weimar(bars: dict, tunes: dict) -> None:
    db = sqlite3.connect(DATA / "wjazzd.db")
    feel_names = {"SWING": "swing", "LATIN": "latin", "FUNK": "funk", "TWOBEAT": "two-beat",
                  "BALLAD": "ballad"}
    for melid, sig, feel, title in db.execute(
            "select melid, signature, rhythmfeel, title from solo_info"):
        if sig not in ("4/4", "3/4") or feel not in feel_names:
            continue
        beats = db.execute("select bar, beat, onset from beats where melid=? order by onset",
                           (melid,)).fetchall()
        beat_t = {(b, k): t for b, k, t in beats}
        order = [(b, k) for b, k, _ in beats]
        nxt = {order[i]: beat_t[order[i + 1]] for i in range(len(order) - 1)}
        n = int(sig[0])
        per_bar = defaultdict(set)
        for onset, bar, beat in db.execute("select onset, bar, beat from melody where melid=?",
                                           (melid,)):
            key = (bar, beat)
            if bar < 1 or key not in beat_t or key not in nxt:
                continue
            pos = (onset - beat_t[key]) / (nxt[key] - beat_t[key])
            x = ((beat - 1) + min(max(pos, 0.0), 0.999)) / n
            per_bar[bar].add(int(round(x * GRID)) % GRID)
        name = f"{feel_names[feel]} {sig}" if sig == "4/4" else f"jazz waltz {sig}" if feel == "SWING" else f"{feel_names[feel]} {sig}"
        tune = f"weimar:{melid}:{title}"
        tunes[tune] = name
        for b, cells in per_bar.items():
            bars[name].append((tune, sorted(cells)))


def nottingham(bars: dict, tunes: dict) -> None:
    kinds = {"jigs": "jig", "hpps": "hornpipe", "waltzes": "waltz", "slip": "slip jig",
             "reels": "reel", "morris": "morris", "ashover": "folk", "playford": "folk",
             "xmas": "folk"}
    for f in sorted((DATA / "nottingham-dataset-master" / "MIDI").glob("*.mid")):
        stem = "".join(ch for ch in f.stem if not ch.isdigit())
        kind = next((v for k, v in kinds.items() if stem.startswith(k)), "folk")
        m = mido.MidiFile(f)
        sigs = [(x.numerator, x.denominator) for tr in m.tracks for x in tr if x.type == "time_signature"]
        if len(set(sigs)) != 1:
            continue
        num, den = sigs[0]
        sig = f"{num}/{den}"
        if sig not in BEATS:
            continue
        bar_ticks = m.ticks_per_beat * 4 * num / den
        melody = m.tracks[0]
        t, per_bar = 0, defaultdict(set)
        for msg in melody:
            t += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                bar, x = divmod(t / bar_ticks, 1.0)
                per_bar[int(bar)].add(int(round(x * GRID)) % GRID)
        name = {"4/4": {"hornpipe": "hornpipe 4/4"}.get(kind, "reel 4/4"), "2/4": "march 2/4",
                "2/2": "reel 2/2", "3/4": "waltz 3/4", "6/8": "jig 6/8", "9/8": "slip jig 9/8"}[sig]
        tune = f"nottingham:{f.stem}"
        tunes[tune] = name
        for b, cells in per_bar.items():
            if b > 0:                                     # skip a pickup bar
                bars[name].append((tune, sorted(cells)))


def collect() -> tuple[dict, dict]:
    """(groove name -> [(tune, occupied cells of one bar)] in order, tune -> groove name)."""
    bars: dict[str, list] = defaultdict(list)
    tunes: dict[str, str] = {}
    weimar(bars, tunes)
    nottingham(bars, tunes)
    return bars, tunes


def main() -> int:
    if not (DATA / "wjazzd.db").exists() or not (DATA / "nottingham-dataset-master").exists():
        print("Download the datasets into ./datasets first (see README: Groove library).")
        return 1
    bars, tunes = collect()
    templates = []
    for name, rows in sorted(bars.items()):
        train = [cells for tune, cells in rows if not held_out(tune)]
        n_tunes = len({tune for tune, _ in rows if not held_out(tune)})
        if n_tunes < MIN_TUNES:
            print(f"{name:16s} skipped: only {n_tunes} tunes")
            continue                                      # too little to trust
        counts = [0] * GRID
        for cells in train:
            for g in cells:
                counts[g] += 1
        meter = name.split()[-1]
        templates.append({"name": name, "meter": meter, "beats": BEATS[meter],
                          "p": [round(c / len(train), 4) for c in counts], "bars": len(train)})
        print(f"{name:16s} {len(train):6d} bars from {len({t for t, _ in rows if not held_out(t)})} tunes")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "grid": GRID, "templates": templates,
        "held_out": sorted(t for t in tunes if held_out(t)), "labels": tunes,
        "sources": ["Weimar Jazz Database (WJazzD), The Jazzomat Research Project, Open Database "
                    "License 1.0 / Database Contents License 1.0",
                    "Nottingham Music Database (E. Foxley), cleaned by Jukedeck, GPL-3.0"],
    }, indent=1))
    print(f"\nWrote {OUT} ({len(templates)} grooves)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
