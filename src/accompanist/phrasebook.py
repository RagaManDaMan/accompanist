"""Your phrase library: every phrase you play or sing, remembered across runs, for the band to
riff on.

Sources: takes (every `run`, and `replay` files), recordings (practice, classes: WAV, run
through the note detector), and live capture (`accompanist practice`). Notes are cut into
phrases at rests, each phrase is given the key it sits in (from the notes around it), and
phrases that don't look like music (speech in a class: short, unsteady, off-pitch syllables)
are left out. Only notes are kept: no audio.

At play time a phrase is moved to the key of the moment and chosen by how well it fits the
chord and key and how much it resembles what you just played (Phrasebook.choose).

Stored as JSON Lines in library.path (default ~/.accompanist/library), outside the project:
phrases.jsonl (one phrase a line) and sources.json (what has been added, so nothing is
added twice).
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np

from .modal import key_scores

Note = tuple[float, int, int, float]          # (onset s, MIDI note, velocity, length s)

# Cutting notes into phrases, and what counts as one.
PHRASE_GAP_S = 0.6        # a rest this long ends a phrase
MIN_NOTES = 3
MAX_NOTES = 24
TAKE_NOTE_S = 0.8         # a take's notes: assumed to last at most this long
KEY_WINDOW_S = 20.0       # the key of a phrase: from the notes this far around it
# Music, not speech: notes that last, sit in tune, and stay within a singable range.
MIN_MEDIAN_NOTE_S = 0.11
MAX_MEAN_CENTS = 28.0
MAX_RANGE = 26
# Rubato (ālāp): notes this far apart in the middle, and this uneven (std / mean of the gaps).
RUBATO_MEDIAN_S = 0.45
RUBATO_SPREAD = 0.5
# Choosing: how much likeness to what you just played counts against fit.
LIKENESS_WEIGHT = 0.5
TOP = 24                  # choose among the best this many


@dataclass(frozen=True)
class Phrase:
    notes: tuple[Note, ...]
    tonic: int                    # the key it was played in
    mode: str                     # 'major' | 'minor'
    source: str = ""
    tags: tuple[str, ...] = ()

    def to_json(self) -> str:
        return json.dumps({"n": [[round(t, 3), p, v, round(d, 3)] for t, p, v, d in self.notes],
                           "k": [self.tonic, self.mode], "s": self.source,
                           "tags": list(self.tags)})

    @staticmethod
    def from_json(line: str) -> "Phrase":
        d = json.loads(line)
        return Phrase(tuple((float(t), int(p), int(v), float(l)) for t, p, v, l in d["n"]),
                      int(d["k"][0]), str(d["k"][1]), d.get("s", ""), tuple(d.get("tags", ())))


# ---- from notes to phrases ---------------------------------------------------------------

def phrases_from_notes(notes: list[Note], source: str = "", tags: Iterable[str] = (),
                       cents: Optional[list[float]] = None) -> list[Phrase]:
    """Cut notes [(t, note, vel, length)] into phrases at rests, give each its key, and keep
    the ones that look like music. cents: each note's distance from the tempered pitch (from
    audio), to tell singing from speech."""
    notes = sorted(notes)
    if not notes:
        return []
    groups, cur = [], [0]
    for i in range(1, len(notes)):
        gap = notes[i][0] - (notes[i - 1][0] + notes[i - 1][3])
        if gap >= PHRASE_GAP_S or len(cur) >= MAX_NOTES:
            groups.append(cur)
            cur = []
        cur.append(i)
    groups.append(cur)
    times = np.array([n[0] for n in notes])
    out = []
    for g in groups:
        ph = [notes[i] for i in g]
        if len(ph) < MIN_NOTES:
            continue
        if not musical(ph, None if cents is None else [cents[i] for i in g]):
            continue
        mid = ph[len(ph) // 2][0]
        near = [notes[i] for i in np.nonzero(np.abs(times - mid) <= KEY_WINDOW_S)[0]]
        tonic, mode = key_of(near)
        t0 = ph[0][0]
        extra = ("rubato",) if rubato(ph) and "rubato" not in tags else ()
        out.append(Phrase(tuple((t - t0, p, v, d) for t, p, v, d in ph), tonic, mode, source,
                          tuple(tags) + extra))
    return out


def musical(phrase: list[Note], cents: Optional[list[float]] = None) -> bool:
    """Singing or playing, not talking: notes long enough, in tune, within a range."""
    lengths = sorted(d for _, _, _, d in phrase)
    if lengths[len(lengths) // 2] < MIN_MEDIAN_NOTE_S:
        return False
    pitches = [p for _, p, _, _ in phrase]
    if max(pitches) - min(pitches) > MAX_RANGE:
        return False
    if cents is not None and np.mean(np.abs(cents)) > MAX_MEAN_CENTS:
        return False
    return True


def rubato(phrase: list[Note]) -> bool:
    """Free time (ālāp, ālāpana, a cadenza): long notes, no steady pulse between them."""
    iois = np.diff([t for t, _, _, _ in phrase])
    if len(iois) < 2:
        return False
    return float(np.median(iois)) >= RUBATO_MEDIAN_S and float(np.std(iois) / np.mean(iois)) >= RUBATO_SPREAD


def key_of(notes: list[Note]) -> tuple[int, str]:
    hist = [0.0] * 12
    for _, p, v, d in notes:
        hist[p % 12] += max(d, 0.05) * v
    _, tonic, mode = max(key_scores(hist, None, "auto"))
    return tonic, mode


def notes_from_onsets(onsets: list[tuple[float, int, int]]) -> list[Note]:
    """A take's note-ons [(t, note, vel)] (a take keeps no note lengths): each note lasts
    until the next, at most TAKE_NOTE_S, so a longer silence still ends a phrase."""
    out = []
    for i, (t, p, v) in enumerate(onsets):
        nxt = onsets[i + 1][0] if i + 1 < len(onsets) else t + TAKE_NOTE_S
        out.append((t, p, v, min(nxt - t, TAKE_NOTE_S)))
    return out


# ---- the library ------------------------------------------------------------------------

@dataclass
class Phrasebook:
    path: Path
    phrases: list[Phrase] = field(default_factory=list)
    _index: Optional[dict] = None

    @staticmethod
    def open(path: str | os.PathLike) -> "Phrasebook":
        p = Path(path).expanduser()
        book = Phrasebook(p)
        f = p / "phrases.jsonl"
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        book.phrases.append(Phrase.from_json(line))
                    except (ValueError, KeyError, TypeError):
                        continue                       # a damaged line: skip it
        return book

    def sources(self) -> dict:
        f = self.path / "sources.json"
        try:
            return json.loads(f.read_text()) if f.is_file() else {}
        except ValueError:
            return {}

    def add(self, phrases: list[Phrase], source_key: Optional[str] = None) -> int:
        """Append phrases (and remember the source, if given). Returns how many."""
        self.path.mkdir(parents=True, exist_ok=True)
        with open(self.path / "phrases.jsonl", "a", encoding="utf-8") as f:
            for ph in phrases:
                f.write(ph.to_json() + "\n")
        self.phrases += phrases
        self._index = None
        if source_key is not None:
            src = self.sources()
            src[source_key] = len(phrases)
            (self.path / "sources.json").write_text(json.dumps(src, indent=0))
        return len(phrases)

    def has(self, source_key: str) -> bool:
        return source_key in self.sources()

    # ---- recall --------------------------------------------------------------------
    def _build(self) -> dict:
        """Each phrase's pitch-class profile relative to its tonic, and its shape."""
        if self._index is None:
            rel = np.zeros((len(self.phrases), 12))
            for i, ph in enumerate(self.phrases):
                for _, p, v, d in ph.notes:
                    rel[i, (p - ph.tonic) % 12] += 1
                rel[i] /= max(rel[i].sum(), 1)
            self._index = {"rel": rel, "mode": np.array([ph.mode for ph in self.phrases])}
        return self._index

    def choose(self, current: list[tuple[float, int, int]], key: Optional[tuple[int, str]],
               allowed: set[int], rng: random.Random, min_fit: float,
               max_notes: int, register: Optional[float] = None
               ) -> Optional[list[tuple[float, int, int]]]:
        """A phrase from the library for this moment: moved to `key`'s tonic (same mode
        first), fitting `allowed` pitch classes at least min_fit, likelier the more it fits
        and the more its shape resembles `current`. [(t, note, vel)] or None."""
        if not self.phrases or key is None:
            return None
        idx = self._build()
        tonic, mode = key
        allowed_rel = np.zeros(12)
        for pc in allowed:
            allowed_rel[(pc - tonic) % 12] = 1
        fit = idx["rel"] @ allowed_rel                     # share of notes allowed now
        fit = np.where(idx["mode"] == mode, fit, fit - 0.1)
        ok = np.nonzero(fit >= min_fit)[0]
        if len(ok) == 0:
            return None
        best = ok[np.argsort(-fit[ok])][: TOP * 4]
        cur = _shape(current)
        scores = [fit[i] + LIKENESS_WEIGHT * _likeness(cur, _shape(self.phrases[i].notes))
                  for i in best]
        order = np.argsort(scores)[::-1][:TOP]
        pick = best[rng.choices(list(order), [scores[j] for j in order])[0]]
        ph = self.phrases[pick]
        shift = (tonic - ph.tonic) % 12
        shift = shift - 12 if shift > 6 else shift
        notes = [(t, p + shift, v) for t, p, v, _ in ph.notes[:max_notes]]
        if register is not None:                           # near your register
            mean = sum(p for _, p, _ in notes) / len(notes)
            octave = round((register - mean) / 12) * 12
            notes = [(t, p + octave, v) for t, p, v in notes]
        return notes


def _shape(notes) -> list[int]:
    ps = [n[1] for n in notes]
    return [max(-5, min(5, b - a)) for a, b in zip(ps, ps[1:])]


def _likeness(a: list[int], b: list[int]) -> float:
    """0-1: how alike two interval shapes are (direction counts most)."""
    k = min(len(a), len(b), 8)
    if k == 0:
        return 0.0
    same_dir = sum(1 for x, y in zip(a[:k], b[:k]) if (x > 0) == (y > 0) and (x < 0) == (y < 0))
    close = sum(1 for x, y in zip(a[:k], b[:k]) if abs(x - y) <= 1)
    return 0.6 * same_dir / k + 0.4 * close / k


def source_key(path: Path) -> str:
    st = path.stat()
    return f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}"


def from_take(path: Path, tags: Iterable[str] = ()) -> list[Phrase]:
    from .recording import load_take

    return phrases_from_notes(notes_from_onsets(load_take(path)), path.name, tags)


def from_audio(path: Path, cfg: Any, tags: Iterable[str] = ()) -> list[Phrase]:
    """A recording (practice, class): the note detector, then phrases; speech left out."""
    from .audio_io import read_wav
    from .audio_notes import NoteTracker

    samples, rate = read_wav(path)
    tracker = NoteTracker(cfg.audio, rate)
    events, block = [], 2048
    for i in range(0, len(samples), block):
        events += tracker.process(samples[i:i + block], i / rate)
    events += tracker.flush(len(samples) / rate)
    return phrases_from_events(events, path.name, tags)


def phrases_from_events(events, source: str = "", tags: Iterable[str] = ()) -> list[Phrase]:
    """Note-detector events (on/off, with cents) -> phrases."""
    notes, cents, open_ = [], [], {}
    for e in events:
        if e.kind == "on":
            if e.corrected:                         # an octave fix: forget the slipped note
                open_ = {n: o for n, o in open_.items() if o[0] != e.t}
            open_[e.note] = (e.t, e.velocity, e.cents)
        elif e.note in open_:
            t, v, c = open_.pop(e.note)
            notes.append((t, e.note, v, e.t - t))
            cents.append(c)
    order = sorted(range(len(notes)), key=lambda i: notes[i][0])
    return phrases_from_notes([notes[i] for i in order], source, tags, [cents[i] for i in order])
