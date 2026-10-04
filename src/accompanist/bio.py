"""Your body as an input: heartbeats and head gestures from a headband (Muse S Athena).

Pure signal processing on samples handed in with their times (no clock, no Bluetooth: see
muse_io.py for the headband). Tuned on a 10-minute recording of the owner sitting, nodding,
tilting and playing tenor sax:

- HeartTracker: beats from the forehead's red light sensors (PPG, 64 Hz), and the heart rate
  from the last few beats. Clean even while playing.
- GestureDetector: a nod (head down-up, on the pitch gyro) and a tilt left or right (gravity
  moving sideways on the accelerometer), each a burst of fast turns; playing never moves
  the head that fast. One event per gesture set, then a pause (refractory).
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import numpy as np

# Heart: the pulse wave's band (as moving averages), how far apart beats must be, how a
# beat must stand out, and how many beats make the heart rate.
PULSE_DETREND_S = 0.5      # subtract the 0.5 s moving average (drops breathing, drift)
PULSE_SMOOTH_S = 0.1       # then smooth over 0.1 s (drops jitter)
PEAK_HALF_S = 0.15         # a beat is the highest point within this either side
MIN_BEAT_S = 0.35          # at most ~170 bpm
BEAT_PROMINENCE = 0.5      # a beat rises at least this many standard deviations,
BEAT_HEIGHT_SHARE = 0.3    # at least this share as high as the recent beats,
BEAT_GAP_SHARE = 0.5      # and no sooner than this share of the recent beat interval
RATE_BEATS = 8             # heart rate: from the last this many beats
LOST_S = 2.0               # no beat for this long: start judging beats afresh
MIN_BPM, MAX_BPM = 40, 180
BUFFER_S = 6.0

# Gestures: how fast the head must turn (deg/s), how far gravity must move sideways (g) for a
# tilt, how little for a nod, and the pause after a gesture (a set of 3 counts once).
GYRO_FAST = 40.0
TILT_G = 0.28
TILT_HOLD_S = 0.12         # a tilt: gravity this far sideways for at least this long...
TILT_TIMES = 2             # ...this many times to the same side (and never the other way)
TILT_WINDOW_S = 3.0        # ...within this long: lifting the sax isn't a tilt
REARM_CALM_S = 1.0         # after a gesture, the head must be calm this long to count again
NOD_MAX_SIDEWAYS_G = 0.25
NOD_SWINGS = 2             # a nod: at least this many fast swings...
NOD_WINDOW_S = 1.2         # ...within this long
NOD_RETURN_SHARE = 0.6     # the swing back needs only this share of the speed
REFRACTORY_S = 3.0
BASELINE_S = 5.0           # gravity's resting direction: a slow average while still


class HeartTracker:
    def __init__(self, rate: float = 64.0) -> None:
        self.rate = rate
        self._t: deque[float] = deque()
        self._x: deque[float] = deque()
        self.beats: deque[float] = deque(maxlen=RATE_BEATS + 1)
        self._heights: deque[float] = deque(maxlen=RATE_BEATS)
        self._checked = float("-inf")     # samples up to here have been searched for beats

    def process(self, times, values) -> list[float]:
        """New samples (the red PPG, any units). Returns the beats found (their times)."""
        self._t.extend(float(t) for t in times)
        self._x.extend(float(v) for v in values)
        while self._t and self._t[0] < self._t[-1] - BUFFER_S:
            self._t.popleft()
            self._x.popleft()
        t, x = np.array(self._t), np.array(self._x)
        n_detrend = max(3, int(PULSE_DETREND_S * self.rate))
        n_smooth = max(1, int(PULSE_SMOOTH_S * self.rate))
        if len(x) < n_detrend * 3:
            return []
        x = x - x.mean()                           # (else the averages' zero padding at the
        y = x - np.convolve(x, np.ones(n_detrend) / n_detrend, "same")   # ends swamps it)
        y = np.convolve(y, np.ones(n_smooth) / n_smooth, "same")
        edge = n_detrend // 2 + n_smooth           # the moving averages are wrong at the ends
        half = max(1, int(PEAK_HALF_S * self.rate))
        sd = float(np.std(y[edge:-edge])) or 1.0
        found = []
        last = len(y) - edge - half                # a peak's window must be all good samples
        for i in range(edge + half, last):
            if t[i] <= self._checked:
                continue
            w = y[i - half:i + half + 1]
            if not (y[i] == w.max() and y[i] - w.min() > BEAT_PROMINENCE * sd):
                continue
            if self.beats and t[i] - self.beats[-1] > LOST_S:
                self._heights.clear()              # lost the beat: forget how tall beats were
            if self._heights and y[i] < BEAT_HEIGHT_SHARE * float(np.median(self._heights)):
                continue                           # a ripple, not a beat
            if self.beats and t[i] - self.beats[-1] < self._min_gap():
                continue
            self.beats.append(t[i])
            self._heights.append(float(y[i]))
            found.append(t[i])
        if last > edge + half:
            self._checked = t[last - 1]
        return found

    def _min_gap(self) -> float:
        """Beats can't come closer than MIN_BEAT_S, nor much faster than the recent rate."""
        gaps = np.diff(self.beats)
        if len(gaps) >= 3:
            return max(MIN_BEAT_S, BEAT_GAP_SHARE * float(np.median(gaps)))
        return MIN_BEAT_S

    @property
    def bpm(self) -> Optional[float]:
        gaps = np.diff(self.beats)
        gaps = gaps[(gaps >= 60 / MAX_BPM) & (gaps <= 60 / MIN_BPM)]
        return float(60 / np.median(gaps)) if len(gaps) >= 3 else None


class GestureDetector:
    def __init__(self) -> None:
        self._gravity: Optional[np.ndarray] = None
        self._last_t: Optional[float] = None
        self._swings: deque[tuple[float, float]] = deque()   # (t, pitch speed) fast swings
        self._tilts: deque[tuple[float, int]] = deque()       # (end, side) of each tilt
        self._tilt_start: Optional[tuple[float, int]] = None  # a tilt in progress
        self._quiet_until = float("-inf")

    def process(self, t: float, acc, gyro) -> Optional[str]:
        """One sample: acc (g) and gyro (deg/s), x y z. Returns 'nod', 'tilt_left',
        'tilt_right' when a gesture is recognised, else None."""
        acc, gyro = np.asarray(acc, float), np.asarray(gyro, float)
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        still = np.all(np.abs(gyro) < GYRO_FAST / 4)
        if self._gravity is None:
            self._gravity = acc.copy()
        elif still:
            k = min(1.0, dt / BASELINE_S)
            self._gravity += k * (acc - self._gravity)
        sideways = acc[1] - self._gravity[1]
        # A nod: a fast swing of the head down (or up), then back the other way, a little
        # slower is fine (the way back is often gentler).
        need = GYRO_FAST if not self._swings else GYRO_FAST * NOD_RETURN_SHARE
        if abs(gyro[1]) > need:
            if not self._swings or np.sign(self._swings[-1][1]) != np.sign(gyro[1]):
                self._swings.append((t, gyro[1]))
        while self._swings and self._swings[0][0] < t - NOD_WINDOW_S:
            self._swings.popleft()
        side = int(np.sign(sideways)) if abs(sideways) > TILT_G else 0
        if self._tilt_start and side != self._tilt_start[1]:        # a tilt ends
            begun, was = self._tilt_start
            if t - begun >= TILT_HOLD_S:
                self._tilts.append((t, was))
            self._tilt_start = None
        if side and self._tilt_start is None:
            self._tilt_start = (t, side)
        while self._tilts and self._tilts[0][0] < t - TILT_WINDOW_S:
            self._tilts.popleft()
        if np.any(np.abs(gyro) > GYRO_FAST) and t < self._quiet_until:
            self._quiet_until = max(self._quiet_until, t + REARM_CALM_S)   # still moving
        if t < self._quiet_until:
            return None
        sides = {s for _, s in self._tilts}
        if len(self._tilts) >= TILT_TIMES and len(sides) == 1:
            self._quiet_until = t + REFRACTORY_S
            self._tilts.clear()
            self._swings.clear()
            return "tilt_left" if sides.pop() < 0 else "tilt_right"
        if (len(self._swings) >= NOD_SWINGS and abs(sideways) < NOD_MAX_SIDEWAYS_G
                and not self._tilts and self._tilt_start is None):
            self._quiet_until = t + REFRACTORY_S
            self._swings.clear()
            return "nod"
        return None
