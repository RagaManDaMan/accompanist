"""A free-running beat clock that leans toward the player's onsets.

The clock ticks at the estimated beat period. When an onset arrives that is
likely to sit on a beat, `hint()` nudges the clock's phase toward it. Result: a
pulse that keeps time by itself but drifts with you rather than against you.
"""
from __future__ import annotations

import math
from typing import Optional


class BeatClock:
    def __init__(self, phase_gain: float = 0.3) -> None:
        self.phase_gain = phase_gain
        self.period = 1.0
        self.next_beat: Optional[float] = None

    @property
    def running(self) -> bool:
        return self.next_beat is not None

    def start(self, onset_t: float, period: float, now: Optional[float] = None) -> None:
        """Start assuming `onset_t` was a beat. With `now`, the first beat is the next one
        on that grid at or after now (so a late start keeps the phase)."""
        self.period = period
        self.next_beat = onset_t + period
        if now is not None and self.next_beat < now:
            self.next_beat += math.ceil((now - self.next_beat) / period) * period

    def stop(self) -> None:
        self.next_beat = None

    def set_period(self, period: float) -> None:
        self.period = period

    def correct(self, shift: float) -> None:
        """We are `shift` seconds early (negative: late): move part of the way."""
        if self.next_beat is not None:
            self.next_beat += self.phase_gain * shift

    def hint(self, t: float, window: float = 0.15) -> bool:
        """An onset at time t: if it lands within `window` (a fraction of the period)
        of a predicted beat, pull the phase toward it. Returns True if it did."""
        if self.next_beat is None:
            return False
        n = round((t - self.next_beat) / self.period)
        err = t - (self.next_beat + n * self.period)
        if abs(err) > window * self.period:
            return False  # off the beat (a syncopation, an eighth): no phase information
        self.correct(err)
        return True

    def phase_error(self, ref_t: float) -> float:
        """How far our beats sit from a grid through ref_t (seconds, in -period/2..period/2)."""
        if self.next_beat is None:
            return 0.0
        err = (self.next_beat - ref_t) % self.period
        return err - self.period if err > self.period / 2 else err

    def align(self, ref_t: float) -> None:
        """Move our beats onto the grid through ref_t (the nearest such beat)."""
        if self.next_beat is not None:
            self.next_beat -= self.phase_error(ref_t)

    def due(self, now: float) -> list[float]:
        """Beat times that have arrived since the last call."""
        beats: list[float] = []
        if self.next_beat is None:
            return beats
        if now - self.next_beat > 2 * self.period:  # we fell behind: resync, don't burst
            self.next_beat = now
        while self.next_beat <= now:
            beats.append(self.next_beat)
            self.next_beat += self.period
        return beats
