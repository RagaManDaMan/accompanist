"""Tempo from the last half-minute of playing, not from the last interval.

Idea: keep the recent note onsets (older ones count less), turn them into a
smoothed pulse train, and autocorrelate it. A tempo is plausible when the
signal repeats at one beat, and again at two, three and four beats. Each
candidate bpm is scored on that comb, times a gentle prior toward `prior_bpm`
that settles the beat "octave" (quarters at 60 vs eighths at 120) when the
playing alone cannot.

It is a pure function of the onset times and `now`: callers feed onsets with
on_onset() and call update(now) as often as they like; the estimate is
recomputed about once per `update_s`.

The output is smoothed twice: an EMA while we stay on the same peak, and
hysteresis before we jump to a different one (a real tempo change, or an
octave switch), so a single ambiguous second does not flip the beat.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Optional

import numpy as np

from .config import TempoCfg

# Weight of the 1-, 2-, 3- and 4-beat lags in a tempo's score. The shape of the
# comb is part of the method, not a player preference, so it is not a parameter.
HARMONIC_WEIGHTS = (1.0, 0.7, 0.5, 0.4)
# Lags need at least this share of the signal overlapping to be trusted.
MIN_OVERLAP = 0.25
# Timing slop allowed when fitting the beat phase, as a fraction of the beat.
PHASE_SIGMA = 0.08
# A wholesale realign of the pulse needs the new phase to fit this much better than the
# current one; otherwise equally plausible phases (even eighths) would flip it back and forth.
REALIGN_ADVANTAGE = 1.5
# How far back the phase fit listens, as a half-life in beats.
PHASE_HALFLIFE_BEATS = 4.0
# Grid fit (refine): period step (log) and number of phases tried per period.
REFINE_STEP = 0.001
PHASE_STEPS = 64


class TempoEstimator:
    def __init__(self, cfg: Optional[TempoCfg] = None) -> None:
        # Holds a reference, not a copy: parameters changed live apply at the next update.
        self.cfg = cfg or TempoCfg()
        c = self.cfg
        self._bpm = min(max(c.initial_bpm, c.min_bpm), c.max_bpm)
        self.confidence = 0.0
        self._onsets: deque[float] = deque()
        self._last_onset: Optional[float] = None
        self._last_update: Optional[float] = None
        self._locked_on_peak = False     # have we ever produced a real estimate?
        self._challenger_since: Optional[float] = None
        self._smoothed: Optional[np.ndarray] = None

    @property
    def period(self) -> float:
        return 60.0 / self._bpm

    @property
    def onsets(self) -> tuple[float, ...]:
        return tuple(self._onsets)

    @property
    def bpm(self) -> float:
        return self._bpm

    def set_bpm(self, bpm: float) -> None:
        """Force the estimate (tap tempo). Later updates track from here."""
        c = self.cfg
        self._bpm = min(max(bpm, c.min_bpm), c.max_bpm)
        self._locked_on_peak = True

    # ---- input ------------------------------------------------------------
    def on_onset(self, t: float) -> None:
        """Feed one note onset. Onsets closer than min_ioi_s (chords, grace notes) count once."""
        if self._last_onset is not None and t - self._last_onset < self.cfg.min_ioi_s:
            return
        self._last_onset = t
        self._onsets.append(t)

    # ---- estimation -------------------------------------------------------
    def update(self, now: float, rate: float = 1.0, locked: bool = False) -> bool:
        """Recompute if update_s has passed. `rate` (0-1) scales how far the estimate may move.

        locked=True: the beat level is settled (groove lock). No jumps to another tempo or
        octave; instead the estimate is refined by fitting a beat grid to the onsets
        (refine()), which is more precise than the autocorrelation peak. Confidence is held.

        Returns True if the estimate was recomputed.
        """
        c = self.cfg
        if self._last_update is not None and now - self._last_update < c.update_s:
            return False
        dt = 0.0 if self._last_update is None else now - self._last_update
        self._last_update = now
        while self._onsets and self._onsets[0] < now - c.window_s:
            self._onsets.popleft()
        fresh = self._onsets and self._onsets[-1] > now - dt
        if len(self._onsets) < c.min_onsets or not fresh:
            # Nothing new to go on: hold the tempo (silence must not move it); trust fades.
            self.confidence *= 0.5 ** (dt / c.halflife_s)
            return False
        if locked and self._locked_on_peak:
            target = 60.0 / self.refine(now)[0]
            a = c.alpha * rate
            self._bpm = math.exp(math.log(self._bpm) + a * (math.log(target) - math.log(self._bpm)))
            return True
        scored = self.scores(now)
        if scored is None:
            return False
        bpms, score = scored
        if self._smoothed is not None and self._smoothed.shape == score.shape and c.score_memory > 0:
            prev = np.where(np.isfinite(self._smoothed), self._smoothed, score)
            score = c.score_memory * prev + (1.0 - c.score_memory) * score
        self._smoothed = score
        finite = np.isfinite(score)
        best = int(np.nanargmax(np.where(finite, score, np.nan)))
        target, jump = best, not self._locked_on_peak
        if self._locked_on_peak:
            near = (np.abs(np.log2(bpms / self._bpm)) <= math.log2(1.0 + c.peak_width)) & finite
            if near.any() and not near[best]:
                local = int(np.nanargmax(np.where(near, score, np.nan)))
                if score[best] - score[local] > c.switch_margin * abs(score[local]):
                    # A clearly better peak elsewhere: it must stay better for switch_hold_s.
                    if self._challenger_since is None:
                        self._challenger_since = now
                    jump = now - self._challenger_since >= c.switch_hold_s
                else:
                    self._challenger_since = None
                if not jump:
                    target = local                    # stay on our peak
            else:
                self._challenger_since = None
                jump = not near.any()
        new = float(bpms[target])
        if jump:
            self._challenger_since = None
            self._bpm += rate * (new - self._bpm)
        else:
            a = c.alpha * rate
            self._bpm = math.exp(math.log(self._bpm) + a * (math.log(new) - math.log(self._bpm)))
        self._locked_on_peak = True
        median = float(np.median(score[finite]))
        self.confidence = float(min(max((score[target] - median) / c.confidence_scale, 0.0), 1.0))
        return True

    def beat_reference(self, now: float, period: Optional[float] = None,
                       current: Optional[float] = None) -> Optional[tuple[float, float]]:
        """(beat time, advantage): the phase of the beat grid that the last few beats of
        onsets line up with best. Beats are that time + k * period.

        More notes start on the beat than between beats in most music, so the best-fitting
        grid is taken as the beat. `advantage` is how much better it fits than a grid
        through `current` (1.0 if not given): a caller can ignore a phase that is only
        marginally better. None if there are no onsets in the window."""
        if not self._onsets:
            return None
        c = self.cfg
        p = period or self.period
        t = np.fromiter(self._onsets, dtype=float)
        # Only the last few beats: an older onset's phase is blurred by any small tempo error.
        w = 0.5 ** ((now - t) / (PHASE_HALFLIFE_BEATS * p))
        sigma = max(c.smooth_s, PHASE_SIGMA * p)

        def fit(phases: np.ndarray) -> np.ndarray:
            d = (t[None, :] - phases[:, None]) % p
            d = np.minimum(d, p - d)
            return (w[None, :] * np.exp(-0.5 * (d / sigma) ** 2)).sum(axis=1)

        phases = np.arange(0.0, p, c.bin_s)
        scores = fit(phases)
        i = int(np.argmax(scores))
        ref = float(phases[i] + math.floor(t[-1] / p) * p)
        advantage = 1.0
        if current is not None:
            here = float(fit(np.array([current % p]))[0])
            advantage = float(scores[i]) / here if here > 0 else float("inf")
        return ref, advantage

    def refine(self, now: float) -> tuple[float, float]:
        """(period, beat time): the beat grid within peak_width of the estimate that best
        fits the recent onsets (recency-weighted). Used once the beat level is settled."""
        c = self.cfg
        t = np.fromiter(self._onsets, dtype=float)
        w = 0.5 ** ((now - t) / c.halflife_s)
        p0 = self.period
        periods = p0 * np.exp(np.arange(-c.peak_width, c.peak_width + 1e-9, REFINE_STEP))
        frac = np.arange(PHASE_STEPS) / PHASE_STEPS
        best = (p0, float(t[-1]), -1.0)
        for p in periods:
            sigma = max(c.smooth_s, PHASE_SIGMA * p)
            d = (t[None, :] - frac[:, None] * p) % p
            d = np.minimum(d, p - d)
            fit = (w[None, :] * np.exp(-0.5 * (d / sigma) ** 2)).sum(axis=1)
            i = int(np.argmax(fit))
            if fit[i] > best[2]:
                best = (float(p), float(frac[i] * p + math.floor(t[-1] / p) * p), float(fit[i]))
        return best[0], best[1]

    def scores(self, now: float) -> Optional[tuple[np.ndarray, np.ndarray]]:
        """(bpm candidates, score) for the onsets currently in the window. NaN = not scorable."""
        c = self.cfg
        t = np.fromiter(self._onsets, dtype=float)
        start = float(t[0])
        end = min(now, float(t[-1]) + 60.0 / c.min_bpm)   # a silent tail carries no rhythm
        n = int(math.ceil((end - start) / c.bin_s)) + 1
        if n < 4:
            return None
        x = np.zeros(n)
        idx = np.clip(np.round((t - start) / c.bin_s).astype(int), 0, n - 1)
        np.add.at(x, idx, 0.5 ** ((now - t) / c.halflife_s))
        sig = c.smooth_s / c.bin_s
        half = int(math.ceil(4 * sig))
        kernel = np.exp(-0.5 * (np.arange(-half, half + 1) / sig) ** 2)
        x = np.convolve(x, kernel, mode="same")
        x -= x.mean()

        nfft = 1 << int(math.ceil(math.log2(2 * n)))
        spec = np.fft.rfft(x, nfft)
        ac = np.fft.irfft(spec * np.conj(spec), nfft)[:n]
        ac /= n - np.arange(n)                      # unbiased
        if ac[0] <= 0:
            return None
        ac /= ac[0]                                 # normalised: AC(0) = 1
        max_lag = int(n * (1.0 - MIN_OVERLAP))

        bpms = np.arange(c.min_bpm, c.max_bpm + c.bpm_step / 2, c.bpm_step)
        w = np.asarray(HARMONIC_WEIGHTS)
        m = np.arange(1, len(w) + 1)
        lags = (60.0 / bpms)[:, None] * m[None, :] / c.bin_s
        valid = lags <= max_lag
        vals = np.interp(lags, np.arange(max_lag + 1), ac[: max_lag + 1])
        wv = np.where(valid, w[None, :], 0.0)
        wsum = wv.sum(axis=1)
        comb = np.where(valid.sum(axis=1) >= 2, (vals * wv).sum(axis=1) / np.maximum(wsum, 1e-12), np.nan)
        prior = np.exp(-0.5 * (np.log2(bpms / c.prior_bpm) / c.prior_sigma_oct) ** 2)
        return bpms, comb * prior
