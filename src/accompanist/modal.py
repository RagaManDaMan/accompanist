"""ModalModel: a harmony model that picks chords from a key, by what you are playing.

Two memories of what you played: a long one (harmony.half_life_s) decides the key, a
short one (harmony.chord_memory_s) decides the chord. The key is your tonic
(harmony.root) in harmony.mode:

    major      the major scale
    minor      natural minor plus the raised 7th (so the dominant can be major)
    chromatic  anything: any root, any chord in the vocabulary
    auto       major or minor, whichever your playing fits (and the tonic too, if
               harmony.root = "auto"), using the Krumhansl-Kessler key profiles

Every chord of the vocabulary whose notes all lie in the key is scored against the
short memory: how much of what you played it contains, minus a little for what it
leaves out, with a cost for richer chords that harmony.color (0-1) lowers. The pad
adds its own lag and rate limit, so this proposes freely.

Nothing here is specific to one tradition: scales and chords are plain pitch-class
sets, so other scale systems can be added as further modes or models.
"""
from __future__ import annotations

import random
from typing import Any, Optional

from .harmony import Onset, PitchClassTracker, Voicing

MAJOR = frozenset({0, 2, 4, 5, 7, 9, 11})
MINOR = frozenset({0, 2, 3, 5, 7, 8, 10, 11})       # natural minor + raised 7th
CHROMATIC = frozenset(range(12))
SCALES = {"major": MAJOR, "minor": MINOR, "chromatic": CHROMATIC}

# (suffix, intervals, complexity): complexity is a cost that harmony.color scales down.
CHORDS: tuple[tuple[str, tuple[int, ...], float], ...] = (
    ("", (0, 4, 7), 0.0),
    ("m", (0, 3, 7), 0.0),
    ("5", (0, 7), 0.02),
    ("sus2", (0, 2, 7), 0.06),
    ("sus4", (0, 5, 7), 0.06),
    ("dim", (0, 3, 6), 0.06),
    ("aug", (0, 4, 8), 0.1),
    ("add9", (0, 4, 7, 2), 0.08),
    ("m(add9)", (0, 3, 7, 2), 0.08),
    ("maj7", (0, 4, 7, 11), 0.08),
    ("7", (0, 4, 7, 10), 0.08),
    ("m7", (0, 3, 7, 10), 0.08),
    ("m7b5", (0, 3, 6, 10), 0.1),
    ("dim7", (0, 3, 6, 9), 0.12),
)

# Krumhansl-Kessler key profiles (tonic first).
KK_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
KK_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)

# Chord scoring weights (shares of the recent notes).
OUTSIDE_COST = 0.3     # per unit of what you played that the chord leaves out
ROOT_BONUS = 0.3       # extra credit when the chord's root is what you played
UNHEARD_COST = 0.03    # per chord tone you haven't played (scaled down by color)
TONIC_BONUS = 0.03     # a slight pull home, toward chords on the tonic
WANDER_SCALE = 0.2     # score noise at wander = 1 (roughly the gap between near-equal fits)
MIN_KEY_EVIDENCE = 1.0  # weight of notes needed before auto key detection trusts itself


def _corr(a: list[float], b: tuple[float, ...]) -> float:
    ma, mb = sum(a) / 12, sum(b) / 12
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den > 0 else 0.0


def key_scores(hist: list[float], tonic: Optional[int], mode: str) -> list[tuple[float, int, str]]:
    """[(correlation, tonic, 'major'|'minor')] for the keys allowed by tonic/mode."""
    tonics = range(12) if tonic is None else (tonic,)
    modes = ("major", "minor") if mode == "auto" else (mode,)
    out = []
    for t in tonics:
        rotated = [hist[(t + i) % 12] for i in range(12)]
        for m in modes:
            out.append((_corr(rotated, KK_MAJOR if m == "major" else KK_MINOR), t, m))
    return out


def voice(root: int, intervals: tuple[int, ...], octave: int) -> tuple[int, ...]:
    """Open pad voicing: root low, the fifth next to it, the octave and colour tones above."""
    base = 12 * (octave + 1) + root
    ivs = {i % 12 for i in intervals}
    notes = {base, base + 12}
    if 7 in ivs:
        notes.add(base + 7)
    notes |= {base + 12 + i for i in ivs if i not in (0, 7)}
    return tuple(sorted(notes))


class ModalModel:
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        h = cfg.harmony
        self.key_memory = PitchClassTracker(h.half_life_s)
        self.chord_memory = PitchClassTracker(h.chord_memory_s)
        self.key: Optional[tuple[int, str]] = None        # (tonic, 'major'|'minor'|'chromatic')
        self.current: Optional[tuple[int, str]] = None    # (root, suffix) last proposed
        self.rng = random.Random(h.seed)
        self._noise: dict[tuple[int, str], float] = {}
        self._noise_until: Optional[float] = None

    @property
    def key_label(self) -> Optional[str]:
        from .config import NOTE_NAMES

        return None if self.key is None else f"{NOTE_NAMES[self.key[0]]} {self.key[1]}"

    def observe(self, onset: Onset) -> None:
        self._sync()
        self.key_memory.add(onset.t, onset.note, onset.velocity)
        self.chord_memory.add(onset.t, onset.note, onset.velocity)

    def _sync(self) -> None:
        self.key_memory.half_life = self.cfg.harmony.half_life_s
        self.chord_memory.half_life = self.cfg.harmony.chord_memory_s

    def heard_key(self, now: float) -> Optional[tuple[int, str]]:
        """The key your recent playing fits best, whatever key is set: (tonic, mode), or None
        before enough has been heard. For ending a song where you actually are."""
        hist = self.key_memory.snapshot(now)
        if sum(hist) < MIN_KEY_EVIDENCE:
            return None
        _, tonic, mode = max(key_scores(hist, None, "auto"))
        return tonic, mode

    def _update_key(self, hist: list[float]) -> None:
        h = self.cfg.harmony
        tonic = self.cfg.root_pc
        mode = h.mode
        if mode == "chromatic":
            self.key = (tonic if tonic is not None else max(range(12), key=lambda pc: hist[pc]), "chromatic")
            return
        if mode != "auto" and tonic is not None:
            self.key = (tonic, mode)
            return
        if sum(hist) < MIN_KEY_EVIDENCE and self.key is not None:
            return
        scores = key_scores(hist, tonic, mode)
        best = max(scores)
        current = next((s for s in scores if self.key and (s[1], s[2]) == self.key), None)
        if current is None or best[0] > current[0] + h.key_margin:
            self.key = (best[1], best[2])

    def propose(self, now: float) -> Optional[Voicing]:
        self._sync()
        h = self.cfg.harmony
        recent = self.chord_memory.snapshot(now)
        total = sum(recent)
        if total <= 0:
            return None
        self._update_key(self.key_memory.snapshot(now))
        tonic, mode = self.key
        scale = {(tonic + i) % 12 for i in SCALES[mode]}
        share = [x / total for x in recent]
        plain = 1.0 - h.color
        if self._noise_until is None or now >= self._noise_until:   # a new wandering choice
            self._noise = {}
            self._noise_until = now + h.wander_every_s
        best, best_score = None, float("-inf")
        for root in range(12):
            if root not in scale:
                continue
            for suffix, ivs, complexity in CHORDS:
                pcs = {(root + i) % 12 for i in ivs}
                if not pcs <= scale:
                    continue
                inside = sum(share[pc] for pc in pcs)
                score = (inside - OUTSIDE_COST * (1.0 - inside) + ROOT_BONUS * share[root]
                         - plain * (complexity + UNHEARD_COST * sum(1 for pc in pcs if share[pc] < 0.02)))
                if root == tonic:
                    score += TONIC_BONUS
                if (root, suffix) == self.current:
                    score += h.chord_stickiness
                if h.wander > 0:
                    if (root, suffix) not in self._noise:
                        self._noise[(root, suffix)] = self.rng.random()
                    score += h.wander * WANDER_SCALE * self._noise[(root, suffix)]
                if score > best_score:
                    best, best_score = (root, suffix, ivs), score
        root, suffix, ivs = best
        self.current = (root, suffix)
        third = 4 if 4 in ivs else 3 if 3 in ivs else None
        from .config import NOTE_NAMES

        return Voicing(root, third, voice(root, ivs, self.cfg.pad.octave), f"{NOTE_NAMES[root]}{suffix}")
