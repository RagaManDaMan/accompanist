"""A free-running beat clock that leans toward the player's onsets.

The clock ticks at the estimated beat period. When an onset arrives that is
likely to sit on a beat, `hint()` nudges the clock's phase toward it. Result: a
pulse that keeps time by itself but drifts with you rather than against you.
"""
from __future__ import annotations

from typing import Optional


class BeatClock:
    def __init__(self, phase_gain: float = 0.3) -> None:
        self.phase_gain = phase_gain
        self.period = 1.0
        self.next_beat: Optional[float] = None

    @property
    def running(self) -> bool:
        return self.next_beat is not None

    def start(self, onset_t: float, period: float) -> None:
        """Start assuming `onset_t` was a beat."""
        self.period = period
        self.next_beat = onset_t + period

    def stop(self) -> None:
        self.next_beat = None

    def set_period(self, period: float) -> None:
        self.period = period

    def hint(self, t: float) -> None:
        """An onset at time t probably fell on a beat: pull the phase toward it."""
        if self.next_beat is None:
            return
        n = round((t - self.next_beat) / self.period)
        err = t - (self.next_beat + n * self.period)
        self.next_beat += self.phase_gain * err

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
