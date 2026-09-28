"""Tempo following that breathes with the player instead of snapping to a grid.

Idea: every interval between your note onsets is *probably* a simple multiple or
fraction of the beat (1/4, 1/2, 1, 2, 4). We snap each interval to whichever of
those fits the current beat estimate best, and nudge the estimate toward what
that snap implies. Intervals that fit nothing (rubato, ornaments) are ignored
and lower our confidence instead of corrupting the estimate.

Known limit: the beat "octave" is a prior, not a fact. A steady stream of notes
could be quarter notes at 60 or eighths at 120. initial_bpm and min/max_bpm
decide which reading wins.
"""
from __future__ import annotations

import math
from typing import Optional

BEAT_RATIOS = (0.25, 0.5, 1.0, 2.0, 4.0)


class TempoEstimator:
    def __init__(
        self,
        initial_bpm: float = 70.0,
        min_bpm: float = 40.0,
        max_bpm: float = 180.0,
        alpha: float = 0.25,
        tolerance: float = 0.3,
        min_ioi_s: float = 0.06,
        max_gap_beats: float = 6.0,
        confidence_alpha: float = 0.2,
    ) -> None:
        self.min_period = 60.0 / max_bpm
        self.max_period = 60.0 / min_bpm
        self._period = min(max(60.0 / initial_bpm, self.min_period), self.max_period)
        self.alpha = alpha
        self.tolerance = tolerance
        self.min_ioi_s = min_ioi_s
        self.max_gap_beats = max_gap_beats
        self.confidence_alpha = confidence_alpha
        self.confidence = 0.0
        self._last_onset: Optional[float] = None

    @property
    def period(self) -> float:
        return self._period

    @property
    def bpm(self) -> float:
        return 60.0 / self._period

    def _snap(self, ioi: float) -> tuple[float, float]:
        best_ratio, best_err = BEAT_RATIOS[0], float("inf")
        for r in BEAT_RATIOS:
            err = abs(math.log2(ioi / (self._period * r)))
            if err < best_err:
                best_ratio, best_err = r, err
        return best_ratio, best_err

    def on_onset(self, t: float) -> Optional[float]:
        """Feed one note onset. Returns the beat ratio it was read as, or None."""
        if self._last_onset is None:
            self._last_onset = t
            return None
        ioi = t - self._last_onset
        if ioi < self.min_ioi_s:
            return None  # same gesture (chord / grace note): keep the earlier onset
        self._last_onset = t
        if ioi > self.max_gap_beats * self._period:
            self.confidence *= 0.5  # phrase break: no tempo information in it
            return None
        ratio, err = self._snap(ioi)
        hit = err <= self.tolerance
        self.confidence += self.confidence_alpha * ((1.0 if hit else 0.0) - self.confidence)
        if not hit:
            return None
        target = ioi / ratio
        log_p = math.log(self._period) + self.alpha * (math.log(target) - math.log(self._period))
        self._period = min(max(math.exp(log_p), self.min_period), self.max_period)
        return ratio
