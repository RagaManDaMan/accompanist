"""The rāga view of what you sing or play: Sa, swaras, and which rāga it is.

Pure analysis of notes (no clock): given phrases from a session (a class, a practice),
  sa_of        finds Sa: the note the phrases come home to and dwell on, held up by Pa
  profile      how much of each swara (pitch class relative to Sa) was sung, by time
  candidates   the rāgas (72 melakartas and the janya / Hindustani rāgas of indian/)
               whose swaras best explain it, with direction (ārohaṇa/avarohaṇa) as a
               tie-breaker; rāgas with the same swaras are reported together
A session's Sa is fixed (the śruti), so it is found once per session; give it when known.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

import numpy as np

from . import indian

# Finding Sa: phrase endings and held time count, and Pa a fifth above supports a candidate.
END_WEIGHT, HELD_WEIGHT, PA_WEIGHT = 1.0, 1.0, 0.5
# Fitting a rāga: what falls outside it costs this much per share; each swara of the rāga
# sung less than UNUSED_FLOOR costs; direction evidence breaks ties.
OUTSIDE_COST = 1.5
UNUSED_FLOOR = 0.03
UNUSED_COST = 0.15
DIRECTION_WEIGHT = 0.1
SAME = 0.005            # scores this close: the rāgas are reported together
SEGMENT_PHRASES = 30    # a session is read this many phrases at a time...
SEGMENT_MIN_SCORE = 0.5  # ...and a window fitting no rāga this well isn't named


def sa_of(phrases: Iterable) -> tuple[int, float]:
    """(Sa as a pitch class, confidence 0-1) from a session's phrases."""
    held, ends = np.zeros(12), np.zeros(12)
    for ph in phrases:
        for _, p, _, d in ph.notes:
            held[p % 12] += d
        ends[ph.notes[-1][1] % 12] += 1
    if held.sum() == 0:
        return 0, 0.0
    held, ends = held / held.sum(), ends / max(ends.sum(), 1)
    score = np.array([END_WEIGHT * ends[pc] + HELD_WEIGHT * held[pc] + PA_WEIGHT *
                      (held[(pc + 7) % 12] + ends[(pc + 7) % 12]) for pc in range(12)])
    best = int(np.argmax(score))
    second = float(np.partition(score, -2)[-2])
    return best, float((score[best] - second) / score[best]) if score[best] else 0.0


def profile(phrases: Iterable, sa: int) -> np.ndarray:
    """Share of time on each swara (index 0 = Sa)."""
    out = np.zeros(12)
    for ph in phrases:
        for _, p, _, d in ph.notes:
            out[(p - sa) % 12] += d
    return out / out.sum() if out.sum() else out


def directions(phrases: Iterable, sa: int) -> tuple[np.ndarray, np.ndarray]:
    """(ascending, descending): how often each swara is reached going up or going down."""
    up, down = np.zeros(12), np.zeros(12)
    for ph in phrases:
        ps = [p for _, p, _, _ in ph.notes]
        for a, b in zip(ps, ps[1:]):
            if b > a:
                up[(b - sa) % 12] += 1
            elif b < a:
                down[(b - sa) % 12] += 1
    return up, down


def _unique_ragas() -> dict[frozenset, list[indian.Raga]]:
    groups: dict[frozenset, list] = defaultdict(list)
    seen = set()
    for r in indian.ragas().values():
        if id(r) in seen:
            continue
        seen.add(id(r))
        groups[r.pitch_classes].append(r)
    return groups


def candidates(prof: np.ndarray, up: Optional[np.ndarray] = None,
               down: Optional[np.ndarray] = None, top: int = 3) -> list[tuple[float, list[str]]]:
    """[(score, [rāga names with the same swaras])] best first."""
    scored = []
    for pcs, ragas in _unique_ragas().items():
        inside = sum(prof[pc] for pc in pcs)
        unused = sum(max(0.0, UNUSED_FLOOR - prof[pc]) for pc in pcs) / UNUSED_FLOOR
        base = inside - OUTSIDE_COST * (1 - inside) - UNUSED_COST * unused
        for r in ragas:
            bonus = 0.0
            if up is not None and down is not None and set(r.arohana) != set(r.avarohana):
                asc, desc = {i % 12 for i in r.arohana}, {i % 12 for i in r.avarohana}
                only_up, only_down = asc - desc, desc - asc
                total = up.sum() + down.sum() or 1
                bonus = DIRECTION_WEIGHT * (sum(up[pc] for pc in only_up)
                                            + sum(down[pc] for pc in only_down)) / total * 10
            scored.append((base + bonus, r.name))
    scored.sort(reverse=True)
    out: list[tuple[float, list[str]]] = []
    for score, name in scored:
        if out and abs(out[-1][0] - score) < SAME:
            if name not in out[-1][1]:
                out[-1][1].append(name)
            continue
        if len(out) == top:
            break
        out.append((score, [name]))
    return out


def swaras_used(prof: np.ndarray, floor: float = UNUSED_FLOOR * 1.5) -> list[str]:
    """The swaras sung, named the Carnatic way: the shared positions (R2 or G1, G2 or R3,
    D2 or N1, N2 or D3) by which neighbours are also sung."""
    used = {i for i in range(12) if prof[i] >= floor}
    names = {0: "S", 1: "R1", 4: "G3", 5: "M1", 6: "M2", 7: "P", 8: "D1", 11: "N3"}
    names[2] = "G1" if 1 in used and 3 not in used and 4 not in used else "R2"
    names[3] = "R3" if 4 in used and 2 not in used else "G2"
    names[9] = "N1" if 8 in used and 10 not in used and 11 not in used else "D2"
    names[10] = "D3" if 11 in used and 9 not in used else "N2"
    return [names[i] for i in sorted(used)]


def identify(phrases: list, sa: int, book=None, top: int = 3) -> list[tuple[float, list[str]]]:
    """The rāgas these phrases fit best: from how rāgas are sung (a RagaBook, learnt from
    recordings) when there is one, else from their scales (candidates)."""
    if book is not None:
        from .ragabook import features, phrase_notes

        return [(score, [r.label]) for score, r in
                book.rank(features(phrase_notes(phrases, sa)), top)]
    up, down = directions(phrases, sa)
    return candidates(profile(phrases, sa), up, down, top)


def segments(phrases: list, sa: int, window: int = SEGMENT_PHRASES, book=None
             ) -> list[tuple[int, int, list[str]]]:
    """[(first, end, rāga names)]: the session read a few phrases at a time (phrases are kept in
    the order sung), neighbouring windows in the same rāga joined; a window too unclear to
    name (its best fit under SEGMENT_MIN_SCORE) joins its neighbour."""
    out: list[tuple[int, int, list[str]]] = []
    for i in range(0, len(phrases), window):
        part = phrases[i:i + window]
        best = identify(part, sa, book, top=1)
        floor = SEGMENT_MIN_SCORE if book is None else 0.0
        names = best[0][1] if best and best[0][0] >= floor else None
        end = i + len(part)
        if out and (names is None or names == out[-1][2]):
            out[-1] = (out[-1][0], end, out[-1][2])
        elif names is not None:
            out.append((i, end, names))
    return out
