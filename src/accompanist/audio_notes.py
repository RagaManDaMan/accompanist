"""Hearing notes in audio (sax first): pitch with YIN, onsets from level and pitch changes.

Pure, like tempo.py: feed blocks of samples with the time of their first sample, get back
note events. No clock, no audio hardware (that is audio_io.py), so it runs the same live,
on a WAV file, and in tests on synthetic tones.

A note starts when the sound is loud enough (audio.gate_db), has a clear pitch (YIN
aperiodicity below audio.yin_threshold) and holds one semitone (within
audio.cents_tolerance) for audio.min_note_ms. It is reported at the moment it began, not
when it was confirmed, so tempo following sees the real attack. A new note starts on a
change of pitch (legato) or a fresh attack at the same pitch (tonguing: the level jumps by
audio.attack_db). A note ends after audio.release_ms of silence or unclear pitch.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np


@dataclass(frozen=True)
class AudioEvent:
    t: float            # seconds: when the note began (on) or ended (off)
    kind: str           # "on" | "off"
    note: int           # MIDI note number
    velocity: int = 0   # 1-127 for "on", from the level
    cents: float = 0.0  # how far from the tempered note it was sung/played
    db: float = 0.0     # level, dBFS


def yin(frame: np.ndarray, sr: float, min_hz: float, max_hz: float,
        threshold: float) -> tuple[Optional[float], float]:
    """(f0 in Hz or None, aperiodicity 0..1) of one frame, by YIN (de Cheveigne & Kawahara)."""
    n = len(frame)
    tau_max = min(int(sr / min_hz), n // 2)
    tau_min = max(int(sr / max_hz), 2)
    if tau_max <= tau_min + 2:
        return None, 1.0
    x = frame - frame.mean()
    w = n - tau_max                                  # comparison length
    # Difference function via FFT: d(tau) = sum (x[j] - x[j+tau])^2, j < w.
    size = 1 << int(math.ceil(math.log2(n + w)))
    spec_x = np.fft.rfft(x, size)
    spec_w = np.fft.rfft(x[:w], size)
    cross = np.fft.irfft(spec_x * np.conj(spec_w), size)[: tau_max + 1]
    sq = np.cumsum(np.concatenate(([0.0], x * x)))
    energy_head = sq[w]                               # sum x[0:w]^2
    energy_lag = sq[np.arange(tau_max + 1) + w] - sq[np.arange(tau_max + 1)]
    d = energy_head + energy_lag - 2 * cross
    d[0] = 0.0
    # Cumulative mean normalised difference.
    cmnd = np.ones_like(d)
    running = np.cumsum(d[1:])
    cmnd[1:] = d[1:] * np.arange(1, tau_max + 1) / np.maximum(running, 1e-12)
    below = np.nonzero(cmnd[tau_min:tau_max] < threshold)[0]
    if below.size:
        tau = tau_min + int(below[0])
        while tau + 1 < tau_max and cmnd[tau + 1] < cmnd[tau]:
            tau += 1                                  # walk to the dip's bottom
    else:
        tau = tau_min + int(np.argmin(cmnd[tau_min:tau_max]))
        if cmnd[tau] >= threshold:
            return None, float(cmnd[tau])
    # Parabolic interpolation for sub-sample accuracy.
    if 1 <= tau < tau_max:
        a, b, c = cmnd[tau - 1], cmnd[tau], cmnd[tau + 1]
        denom = a - 2 * b + c
        shift = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
    else:
        shift = 0.0
    return sr / (tau + shift), float(cmnd[tau])


def db_of(frame: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(frame * frame)))
    return 20 * math.log10(max(rms, 1e-9))


class NoteTracker:
    def __init__(self, cfg: Any, sample_rate: float) -> None:
        self.cfg = cfg                       # the [audio] section, read live
        self.sr = float(sample_rate)
        self._buf = np.zeros(0, dtype=np.float32)
        self._buf_t: Optional[float] = None  # time of _buf[0]
        self.note: Optional[int] = None      # the note sounding now
        self._note_t = 0.0
        self._cand: Optional[int] = None     # a pitch waiting to be confirmed
        self._cand_t = 0.0
        self._cand_ok = 0                    # frames of the candidate that were in tune
        self._cand_n = 0
        self._quiet_since: Optional[float] = None
        self._levels: deque[tuple[float, float]] = deque()   # (t, dB) of recent frames
        self._attack_armed = True
        self._attack: Optional[tuple[float, float]] = None   # (onset, seen) of a fresh attack

    # ---- input --------------------------------------------------------------------------
    def process(self, samples: np.ndarray, t0: float) -> list[AudioEvent]:
        """Feed mono samples whose first sample was at time t0. Returns note events."""
        samples = np.asarray(samples, dtype=np.float32).ravel()
        if self._buf_t is None:
            self._buf_t = t0
        self._buf = np.concatenate((self._buf, samples))
        win, hop = self.cfg.window, self.cfg.hop
        events: list[AudioEvent] = []
        while len(self._buf) >= win:
            frame = self._buf[:win]
            t = self._buf_t + (win / 2) / self.sr      # the frame's centre
            events += self._frame(frame, t)
            self._buf = self._buf[hop:]
            self._buf_t += hop / self.sr
        return events

    def flush(self, t: float) -> list[AudioEvent]:
        """End of input: close a sounding note."""
        if self.note is None:
            return []
        ev = AudioEvent(t, "off", self.note)
        self.note = None
        return [ev]

    # ---- per frame ----------------------------------------------------------------------
    def _frame(self, frame: np.ndarray, t: float) -> list[AudioEvent]:
        c = self.cfg
        level = db_of(frame)
        f0, _ = (None, 1.0) if level < c.gate_db else yin(frame, self.sr, c.min_hz, c.max_hz, c.yin_threshold)
        # Attacks are judged on a short window at the newest end of the frame: the pitch
        # window is too long to see the brief dip between tongued notes.
        short = max(int(self.sr * c.level_ms / 1000), 16)
        t_end = t + (len(frame) / 2) / self.sr
        self._levels.append((t_end, db_of(frame[-short:])))
        while self._levels and self._levels[0][0] < t_end - c.attack_window_ms / 1000:
            self._levels.popleft()
        now_level = self._levels[-1][1]
        events: list[AudioEvent] = []
        if f0 is None:                                    # silence or no clear pitch
            if self._quiet_since is None:
                self._quiet_since = t
            self._cand = self._attack = None
            if self.note is not None and t - self._quiet_since >= c.release_ms / 1000:
                events.append(AudioEvent(self._quiet_since, "off", self.note))
                self.note = None
            return events
        self._quiet_since = None
        midi = 69 + 12 * math.log2(f0 / 440.0)
        semitone = round(midi)
        cents = (midi - semitone) * 100
        in_tune = abs(cents) <= c.cents_tolerance

        # A fresh attack on the note that is sounding (tonguing, a re-sung syllable).
        low = min(db for _, db in self._levels)
        if now_level - low < c.attack_db / 2:
            self._attack_armed = True
        if (self.note is not None and self._attack_armed and self._attack is None
                and now_level - low >= c.attack_db and t - self._note_t >= c.min_note_ms / 1000):
            # A fresh attack: a new note, but which one? Wait min_note_ms to see whether the
            # pitch stays (tonguing: same note again) or moves (the next note of a line).
            self._attack_armed = False
            self._attack = (self._rise_start(low), t)
        if self._attack is not None:
            onset, seen = self._attack
            if semitone != self.note:
                self._attack = None
                if self._cand != semitone:                # the next note began at the attack
                    self._cand, self._cand_t, self._cand_ok, self._cand_n = semitone, onset, 0, 0
            elif t - seen >= c.min_note_ms / 1000:
                self._attack = None
                events.append(AudioEvent(onset, "off", self.note))
                events.append(AudioEvent(onset, "on", semitone, self._velocity(level), cents, level))
                self._note_t = onset
                return events

        if semitone == self.note:
            self._cand = None
            return events
        if self._cand != semitone:                        # a new pitch: start confirming it
            self._cand, self._cand_t, self._cand_ok, self._cand_n = semitone, t, 0, 0
        self._cand_n += 1
        self._cand_ok += in_tune
        if t - self._cand_t >= c.min_note_ms / 1000 and self._cand_ok >= self._cand_n / 2:
            start = self._cand_t - (c.window / 2) / self.sr * ONSET_BACKDATE
            start = max(start, self._note_t)              # never before the previous note began
            if self.note is not None:
                events.append(AudioEvent(start, "off", self.note))
            events.append(AudioEvent(start, "on", semitone, self._velocity(level), cents, level))
            self.note, self._note_t, self._cand = semitone, start, None
            self._attack_armed = False
        return events

    def _rise_start(self, low: float) -> float:
        """When the level began its rise from `low` (for backdating a re-attack)."""
        for t, db in reversed(self._levels):
            if db <= low + 1.0:
                return t
        return self._levels[0][0]

    def _velocity(self, level: float) -> int:
        c = self.cfg
        x = (level - c.velocity_floor_db) / max(c.velocity_ceiling_db - c.velocity_floor_db, 1e-6)
        return int(round(VELOCITY_MIN + min(max(x, 0.0), 1.0) * (127 - VELOCITY_MIN)))


# A pitch is first seen in the frame whose centre is half a window after the sound began:
# report onsets this fraction of half a window earlier than first seen.
ONSET_BACKDATE = 0.5
VELOCITY_MIN = 20
