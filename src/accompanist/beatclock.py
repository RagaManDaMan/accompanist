"""A free-running beat clock that leans toward the player's onsets.

The clock ticks at the estimated beat period. When an onset arrives that is
likely to sit on a beat, `hint()` nudges the clock's phase toward it. Result: a
pulse that keeps time by itself but drifts with you rather than against you.
"""
from __future__ import annotations

import math
from typing import Optional


class BeatClock:
    """Beats at `period`, leaning toward the player without lurching.

    Tempo changes set a target the beat spacing eases toward (at most max_tempo_step of a
    beat per beat); phase corrections are queued and paid out at most max_nudge of a beat
    per beat. So the pulse follows you, but a listener hears a steady beat, not hiccups.
    (0 for either means: apply at once.)"""

    def __init__(self, phase_gain: float = 0.3, max_nudge: float = 0.0,
                 max_tempo_step: float = 0.0) -> None:
        self.phase_gain = phase_gain
        self.max_nudge = max_nudge
        self.max_tempo_step = max_tempo_step
        self.period = 1.0
        self.target_period = 1.0
        self.next_beat: Optional[float] = None
        self._shift = 0.0                   # phase correction still to be paid out (s)

    @property
    def running(self) -> bool:
        return self.next_beat is not None

    @property
    def _aim(self) -> float:
        """Where the next beat will be once pending corrections are applied."""
        return self.next_beat + self._shift

    def start(self, onset_t: float, period: float, now: Optional[float] = None) -> None:
        """Start assuming `onset_t` was a beat. With `now`, the first beat is the next one
        on that grid at or after now (so a late start keeps the phase)."""
        self.period = self.target_period = period
        self._shift = 0.0
        self.next_beat = onset_t + period
        if now is not None and self.next_beat < now:
            self.next_beat += math.ceil((now - self.next_beat) / period) * period

    def stop(self) -> None:
        self.next_beat = None
        self._shift = 0.0

    def set_period(self, period: float) -> None:
        self.target_period = period
        if self.next_beat is None or self.max_tempo_step <= 0:
            self.period = period

    def correct(self, shift: float) -> None:
        """We are `shift` seconds early (negative: late): move part of the way."""
        if self.next_beat is not None:
            self._nudge(self.phase_gain * shift)

    def _nudge(self, shift: float) -> None:
        if self.max_nudge <= 0:
            self.next_beat += shift
        else:
            self._shift += shift

    def hint(self, t: float, window: float = 0.15) -> bool:
        """An onset at time t: if it lands within `window` (a fraction of the period)
        of a predicted beat, pull the phase toward it. Returns True if it did."""
        if self.next_beat is None:
            return False
        n = round((t - self._aim) / self.period)
        err = t - (self._aim + n * self.period)
        if abs(err) > window * self.period:
            return False  # off the beat (a syncopation, an eighth): no phase information
        self.correct(err)
        return True

    def phase_error(self, ref_t: float) -> float:
        """How far our beats sit from a grid through ref_t (seconds, in -period/2..period/2)."""
        if self.next_beat is None:
            return 0.0
        err = (self._aim - ref_t) % self.period
        return err - self.period if err > self.period / 2 else err

    def align(self, ref_t: float) -> None:
        """Move our beats onto the grid through ref_t (the nearest such beat), gradually."""
        if self.next_beat is not None:
            self._nudge(-self.phase_error(ref_t))

    def due(self, now: float) -> list[float]:
        """Beat times that have arrived since the last call."""
        beats: list[float] = []
        if self.next_beat is None:
            return beats
        if now - self.next_beat > 2 * self.period:  # we fell behind: resync, don't burst
            self.next_beat = now
        while self.next_beat <= now:
            beats.append(self.next_beat)
            self._ease()
            step = self._shift
            if self.max_nudge > 0:
                limit = self.max_nudge * self.period
                step = min(max(step, -limit), limit)
            self._shift -= step
            self.next_beat += self.period + step
        return beats

    def _ease(self) -> None:
        """Beat spacing one step toward the tempo target."""
        if self.max_tempo_step <= 0:
            self.period = self.target_period
            return
        limit = self.max_tempo_step * self.period
        self.period += min(max(self.target_period - self.period, -limit), limit)
