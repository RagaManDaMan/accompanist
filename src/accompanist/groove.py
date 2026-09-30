"""Hearing the groove: meter (in 3 or in 4), where "1" is, and straight vs swing.

Pure, like tempo.py: the engine tells it where each of your notes fell against the running
beat (which beat, and how far into it), and asks for the groove about once a second.

- Feel: your in-between notes. Straight eighths fall halfway through the beat; swung ones
  later (2/3 of the way for triplet swing). swing = 2 * position - 1, as drums.swing uses.
- Meter: how strongly each beat is marked (your notes near it, weighted by how loud and how
  long they are), and how well that pattern repeats every 3 beats vs every 4. 2/4 counts as
  duple ("in 4"): the two cannot be told apart from the beats alone.
- Downbeat: the beat of the bar that is marked most strongly.

It commits: a new meter or downbeat must win clearly (groove.switch_margin) for
groove.hold_s before it takes over, so the band does not flip-flop.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Optional

METERS = (3, 4)           # meters listening can tell apart (a count-off can set 5, 6, 7 too)
METER_LABELS = {3: "3/4", 4: "4/4", 5: "5/4", 6: "6/8", 7: "7 (3+2+2)"}
# Where the groups of a bar start (1 is always one): these beats get a lighter accent.
GROUPS = {5: (0, 3), 6: (0, 3), 7: (0, 3, 5)}
ON_BEAT = 0.15            # a note within this share of a beat of it marks that beat
OFF_BEAT = (0.35, 0.85)   # a note this far into a beat is an in-between note (feel)
LONG_NOTE_BEATS = 1.0     # a note this long (to the next) counts double for marking a beat
MIN_OFFBEATS = 4          # in-between notes needed to judge the feel
STRAIGHT_BELOW = 0.6      # once swinging, straight again only below this share of the threshold


class Groove:
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg                          # the [groove] section, read live
        self._notes: deque[tuple[float, int, float, int]] = deque()   # (t, beat, pos, velocity)
        self.meter: Optional[int] = None
        self.downbeat = 0                       # beat numbers b with b % meter == downbeat are "1"
        self.swing = 0.0
        self.confidence = 0.0                   # 0-1: how clearly the meter was heard
        self.pinned = False                     # a count-off set the meter
        self._swinging = False
        self._challenger: Optional[tuple[int, int]] = None
        self._challenger_since: Optional[float] = None

    @property
    def feel(self) -> str:
        return "swing" if self._swinging else "straight"

    def label(self) -> Optional[str]:
        return None if self.meter is None else f"{METER_LABELS.get(self.meter, self.meter)} {self.feel}"

    def pin(self, meter: int) -> None:
        """A count-off said the meter: beat number 0 is 1. Listening keeps judging the feel,
        but the meter stays until the next count (or reset)."""
        self.meter, self.downbeat, self.confidence, self.pinned = meter, 0, 1.0, True
        self._challenger = self._challenger_since = None

    def observe(self, t: float, beat: int, pos: float, velocity: int) -> None:
        """A note at time t, `pos` (0-1) of the way from beat number `beat` to the next."""
        self._notes.append((t, beat, pos, velocity))

    def reset(self) -> None:
        self._notes.clear()
        self.meter, self.downbeat, self.swing, self.confidence = None, 0, 0.0, 0.0
        self._swinging = False
        self.pinned = False
        self._challenger = self._challenger_since = None

    # ---- estimation ---------------------------------------------------------------------
    def update(self, now: float, period: float) -> None:
        c = self.cfg
        while self._notes and self._notes[0][0] < now - c.window_beats * period:
            self._notes.popleft()
        notes = list(self._notes)
        if len(notes) < c.min_notes:
            return
        self._update_feel(notes)
        if self.pinned:
            return                                   # the count-off said the meter
        strength = self._beat_strengths(notes, period)
        if not strength:
            return
        best = self._best_meter(strength)
        if best is None:
            return
        meter, down, score, runner_up = best
        self.confidence = max(0.0, min(1.0, (score - runner_up) / max(score, 1e-9) / c.switch_margin))
        if self.meter is None:
            self.meter, self.downbeat = meter, down
            return
        if (meter, down) == (self.meter, self.downbeat):
            self._challenger = self._challenger_since = None
            return
        current = self._score(strength, self.meter, self.downbeat)
        if score < current * (1 + c.switch_margin):
            self._challenger = self._challenger_since = None
            return
        if self._challenger != (meter, down):
            self._challenger, self._challenger_since = (meter, down), now
        elif now - self._challenger_since >= c.hold_s:
            self.meter, self.downbeat = meter, down
            self._challenger = self._challenger_since = None

    def _update_feel(self, notes) -> None:
        offs = sorted(p for _, _, p, _ in notes if OFF_BEAT[0] <= p <= OFF_BEAT[1])
        if len(offs) >= MIN_OFFBEATS:
            pos = offs[len(offs) // 2]                       # median: robust to stray notes
            # Measured against where *your* on-beat notes fall, not the pulse: if the pulse
            # sits a little ahead of you, straight eighths would otherwise look swung.
            ons = sorted(p if p < 0.5 else p - 1 for _, _, p, _ in notes
                         if p <= ON_BEAT or p >= 1 - ON_BEAT)
            lean = ons[len(ons) // 2] if len(ons) >= MIN_OFFBEATS else 0.0
            self.swing = max(0.0, min(0.5, 2 * (pos - lean) - 1))
        # Hysteresis: swing from swing_threshold, back to straight only well below it.
        if self.swing >= self.cfg.swing_threshold:
            self._swinging = True
        elif self.swing <= self.cfg.swing_threshold * STRAIGHT_BELOW:
            self._swinging = False

    def _beat_strengths(self, notes, period) -> dict[int, float]:
        """beat number -> how strongly it was marked."""
        s: dict[int, float] = {}
        for i, (t, beat, pos, vel) in enumerate(notes):
            if pos <= ON_BEAT:
                b = beat
            elif pos >= 1 - ON_BEAT:
                b = beat + 1
            else:
                continue
            length = (notes[i + 1][0] - t) / period if i + 1 < len(notes) else 1.0
            w = vel / 127 * (2.0 if length >= LONG_NOTE_BEATS else 1.0)
            s[b] = s.get(b, 0.0) + w
        return s

    def _score(self, strength: dict[int, float], meter: int, down: int) -> float:
        """How well `meter` with "1" at `down` explains the pattern: the downbeat's mean
        strength over the others', plus how alike consecutive bars are."""
        lo, hi = min(strength), max(strength)
        if hi - lo + 1 < 2 * meter:
            return 0.0
        seq = [strength.get(b, 0.0) for b in range(lo, hi + 1)]
        ones = [v for b, v in zip(range(lo, hi + 1), seq) if (b - down) % meter == 0]
        rest = [v for b, v in zip(range(lo, hi + 1), seq) if (b - down) % meter != 0]
        accent = (sum(ones) / len(ones)) / (sum(rest) / len(rest) + 1e-6) if ones and rest else 0.0
        return accent * (1.0 + self._repeat(seq, meter))

    @staticmethod
    def _repeat(seq: list[float], lag: int) -> float:
        """Correlation of the beat pattern with itself one bar later (0-1)."""
        a, b = seq[:-lag], seq[lag:]
        if len(a) < 2:
            return 0.0
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
        return max(0.0, num / den) if den > 0 else 0.0

    def _best_meter(self, strength):
        scored = sorted(((self._score(strength, m, d), m, d) for m in METERS for d in range(m)),
                        reverse=True)
        if not scored or scored[0][0] <= 0:
            return None
        score, meter, down = scored[0]
        other = next((s for s, m, _ in scored if m != meter), 0.0)   # best of the other meter
        return meter, down % meter, score, other
