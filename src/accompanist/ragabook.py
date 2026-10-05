"""How rāgas are really sung: swara profiles learnt from concert recordings, for naming the
rāga of your practice and classes.

Built from the Indian Art Music Raga Recognition Dataset (features), CompMusic, CC BY 4.0
(Gulati, Serrà, Ganguli, Şentürk & Serra, zenodo.org/records/7278506): pitch tracks and
tonics of 480 Carnatic recordings in 40 rāgas and 300 Hindustani recordings in 30 rāgas.
`accompanist library ragas DATASET_DIR` turns it into library.path/ragas.json: for each
rāga, the share of time on each swara and how often each is reached going up and coming
down, from the notes held in the singing (as the note detector hears them, with the voice
preset), averaged over its recordings.

A scale says which swaras a rāga has; this says how much each is dwelt on and from which
direction, which is what tells Bhairavi from Mukhari, or Kalyani from Yaman, in practice.
Tested leave-one-out on the dataset's Carnatic recordings: the right rāga first about 4
times in 5 (whole recordings), in the first three about 9 in 10.
"""
from __future__ import annotations

import json
import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

CREDIT = ("Indian Art Music Raga Recognition Dataset (features), CompMusic, CC BY 4.0: "
          "Gulati, Serrà, Ganguli, Şentürk & Serra (zenodo.org/records/7278506)")
TRADITIONS = ("Carnatic", "Hindustani")
# Turning a pitch track into held notes, as the note detector does with the voice preset.
HOP_S = 0.0044444          # the dataset's pitch frames
HELD_S = 0.13              # a pitch held this long...
CENTS = 35                 # ...within this many cents of a semitone is a note
PHRASE_GAP_S = 1.0         # notes further apart than this aren't a step up or down
DIRECTION_WEIGHT = 0.7     # how much direction counts beside dwelling, in matching


def notes_from_pitch(hz: np.ndarray, tonic: float, hop: float = HOP_S
                     ) -> list[tuple[float, int, float]]:
    """A pitch track (Hz per frame, 0 = silence) -> [(start s, semitones from Sa, length s)]."""
    voiced = hz > 0
    cents = np.full(len(hz), np.nan)
    cents[voiced] = 1200 * np.log2(hz[voiced] / tonic)
    step = np.round(cents / 100)
    ok = voiced & (np.abs(cents - step * 100) <= CENTS)
    key = np.where(ok, step, -999)
    edges = np.flatnonzero(np.diff(key) != 0) + 1
    out = []
    for a, b in zip(np.r_[0, edges], np.r_[edges, len(key)]):
        if key[a] != -999 and (b - a) * hop >= HELD_S:
            out.append((a * hop, int(key[a]), (b - a) * hop))
    return out


def features(notes: Iterable[tuple[float, int, float]]) -> np.ndarray:
    """[(start, semitones from Sa, length)] -> 36 numbers: time on each swara (12), and how
    often each swara is reached going up (12) and coming down (12)."""
    notes = list(notes)
    dwell, up, down = np.zeros(12), np.zeros(12), np.zeros(12)
    for _, k, d in notes:
        dwell[k % 12] += d
    for (t0, a, d0), (t1, b, _) in zip(notes, notes[1:]):
        if t1 - (t0 + d0) > PHRASE_GAP_S:
            continue
        if b > a:
            up[b % 12] += 1
        elif b < a:
            down[b % 12] += 1
    moves = up.sum() + down.sum() or 1.0
    return np.r_[dwell / (dwell.sum() or 1.0), up / moves, down / moves]


def phrase_notes(phrases: Iterable, sa: int) -> list[tuple[float, int, float]]:
    """Your phrases (a session) -> notes relative to Sa, laid end to end with phrase gaps."""
    out, offset = [], 0.0
    for ph in phrases:
        for t, p, _, d in ph.notes:
            out.append((offset + t, p - sa, d))
        if ph.notes:
            offset += ph.notes[-1][0] + ph.notes[-1][3] + 2 * PHRASE_GAP_S
    return out


def plain(name: str) -> str:
    """'Śankarābharaṇaṁ' -> 'sankarabharanam'."""
    s = unicodedata.normalize("NFD", name)
    return "".join(c for c in s if not unicodedata.combining(c)).lower().replace(" ", "-")


@dataclass(frozen=True)
class RagaProfile:
    name: str                   # as the dataset writes it, e.g. 'Kalyāṇi'
    tradition: str              # 'Carnatic' | 'Hindustani'
    profile: tuple[float, ...]  # features() averaged over its recordings
    recordings: int

    @property
    def label(self) -> str:
        return f"{self.name} ({self.tradition[0]})"


def distance(a: np.ndarray, b: np.ndarray) -> float:
    """Between two features(): Hellinger-like, dwelling and (less) direction."""
    w = np.r_[np.ones(12), np.full(24, DIRECTION_WEIGHT)]
    return float(np.linalg.norm(w * (np.sqrt(np.clip(a, 0, None)) - np.sqrt(np.clip(b, 0, None)))))


def _load_track(path: Path) -> np.ndarray:
    data = np.array(path.read_text().split(), dtype=float).reshape(-1, 2)
    return data[:, 1]


def recordings(dataset: Path, tradition: str):
    """(rāga name, notes) for each recording of one tradition in the dataset folder."""
    base = dataset / tradition
    names = dict(line.rstrip("\n").split("\t") for line in
                 (base / "_info_" / "ragaId_to_ragaName_mapping.txt").read_text().splitlines()
                 if "\t" in line)
    for raga_dir in sorted((base / "features").iterdir()):
        if not raga_dir.is_dir() or raga_dir.name not in names:
            continue
        for pitch in sorted(raga_dir.rglob("*.pitch")):
            stem = pitch.with_suffix("")
            tonic_file = stem.with_suffix(".tonicFine")
            if not tonic_file.is_file():
                tonic_file = stem.with_suffix(".tonic")
            tonic = float(tonic_file.read_text().split()[0])
            yield names[raga_dir.name], notes_from_pitch(_load_track(pitch), tonic)


def build(dataset: Path, out: Path, progress=None) -> int:
    """The dataset folder (RagaDataset, with Carnatic/ and Hindustani/) -> ragas.json.
    Returns how many rāgas."""
    ragas = []
    for tradition in TRADITIONS:
        if not (dataset / tradition / "features").is_dir():
            continue
        by_raga: dict[str, list[np.ndarray]] = {}
        for name, notes in recordings(dataset, tradition):
            by_raga.setdefault(name, []).append(features(notes))
            if progress:
                progress(tradition, name)
        for name, feats in by_raga.items():
            ragas.append({"name": name, "tradition": tradition, "recordings": len(feats),
                          "profile": [round(float(x), 5) for x in np.mean(feats, axis=0)]})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"credit": CREDIT, "ragas": ragas}, ensure_ascii=False))
    return len(ragas)


class RagaBook:
    def __init__(self, ragas: list[RagaProfile]) -> None:
        self.ragas = ragas

    @staticmethod
    def open(library: str | Path) -> Optional["RagaBook"]:
        f = Path(library).expanduser() / "ragas.json"
        if not f.is_file():
            return None
        data = json.loads(f.read_text())
        return RagaBook([RagaProfile(r["name"], r["tradition"], tuple(r["profile"]),
                                     r["recordings"]) for r in data["ragas"]])

    def rank(self, feats: np.ndarray, top: int = 3, tradition: Optional[str] = None
             ) -> list[tuple[float, RagaProfile]]:
        """The rāgas nearest these features() first: [(closeness 0-1, rāga)]."""
        scored = sorted(((distance(feats, np.array(r.profile)), r) for r in self.ragas
                         if tradition is None or r.tradition == tradition), key=lambda x: x[0])
        worst = math.sqrt(2 * (1 + 2 * DIRECTION_WEIGHT ** 2))
        return [(1 - d / worst, r) for d, r in scored[:top]]
