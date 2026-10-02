"""How loudly and how busily you are playing, and what that means for the accompaniment.

Pure (no clock of its own): feed note velocities with their times, ask for levels at `now`.
Both measures fade with a half-life of dynamics.memory_s, so a pause lets the
accompaniment come forward again and a burst of notes makes the pad step back.

The pad also makes room (dynamics.pad_space): it sits back while anyone plays, you or the
answer (heard()), and only swells once it has been quiet all round for swell_after_s.
"""
from __future__ import annotations

import math
from typing import Any, Optional


class Dynamics:
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg                 # the dynamics section, read live
        self._sum = 0.0                # decaying sum of velocities
        self._count = 0.0              # decaying count of notes
        self._t: Optional[float] = None
        self._sound_t: Optional[float] = None    # when you, or the answer, last played
        self._level: Optional[float] = None      # the pad's level last time, and when
        self._level_t = 0.0

    def _decay_to(self, t: float) -> None:
        if self._t is not None and t > self._t:
            f = 0.5 ** ((t - self._t) / self.cfg.memory_s)
            self._sum *= f
            self._count *= f
        self._t = t if self._t is None else max(self._t, t)

    def observe(self, t: float, velocity: int) -> None:
        self._decay_to(t)
        self._sum += velocity
        self._count += 1.0
        self.heard(t)

    def heard(self, t: float) -> None:
        """Someone (you, or the answer) is playing at time t: not quiet all round."""
        self._sound_t = t if self._sound_t is None else max(self._sound_t, t)

    def loudness(self) -> Optional[float]:
        """Your recent average velocity (0-127), or None before any notes."""
        return self._sum / self._count if self._count > 1e-9 else None

    def busyness(self, now: float) -> float:
        """0 (silent) .. 1 (at or above busy_notes_per_s)."""
        self._decay_to(now)
        rate = self._count * math.log(2) / self.cfg.memory_s     # decaying count -> notes/s
        return min(rate / self.cfg.busy_notes_per_s, 1.0)

    def follow_gain(self) -> float:
        """How much louder (>1) or softer (<1) than set the accompaniment should be."""
        v = self.loudness()
        if v is None:
            return 1.0
        g = 1.0 + self.cfg.follow * (v / self.cfg.reference_velocity - 1.0)
        return min(max(g, MIN_GAIN), MAX_GAIN)

    def quiet(self, now: float) -> float:
        """0 while anyone plays (you or the answer) .. 1 once it has been quiet all round for
        swell_after_s + swell_s: the room the band has to step forward."""
        c = self.cfg
        if self._sound_t is None:
            return 1.0
        return min(max((now - self._sound_t - c.swell_after_s) / c.swell_s, 0.0), 1.0)

    def pad_level(self, now: float) -> float:
        """The pad's level, 0-1 of full expression: follows you, steps back when busy, sits
        back further while anyone plays and swells when it's quiet all round (pad_space)."""
        c = self.cfg
        level = NOMINAL_LEVEL * self.follow_gain() * (1.0 - c.duck * self.busyness(now))
        level = min(max(level, c.pad_floor), 1.0)
        if c.pad_space <= 0:
            return level
        low = c.pad_floor + (level - c.pad_floor) * (1.0 - c.pad_space)
        high = level + (1.0 - level) * c.pad_space
        target = low + (high - low) * self.quiet(now)
        if self._level is not None and target < self._level:    # recede quickly, not at once
            target = max(target, self._level - (now - self._level_t) / c.recede_s)
        self._level, self._level_t = target, now
        return target


# The pad's level when you play at reference_velocity and aren't busy (leaves headroom to
# swell), and the limits on following your loudness.
NOMINAL_LEVEL = 0.8
MIN_GAIN, MAX_GAIN = 0.3, 1.6
