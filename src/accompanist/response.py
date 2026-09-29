"""Call and response: when you pause after a phrase, answer it on its own channel.

The answer is made from what you just played: its rhythm (rounded to eighth notes when the
beat is known, starting on the next beat) and contour, moved a scale step or two, and now
and then (response.variety) turned upside down or played backwards; kept in the key,
ending on a chord tone, in the response voice's register. It never plays over you: the moment you
play again it stops. Seeded, so a replay answers exactly as the live run did.

`make_answer` is pure (phrase, chord, scale, random source -> notes); ResponseResponder
does the listening, timing and MIDI (through SafeOutput).
"""
from __future__ import annotations

import heapq
import itertools
import random
from typing import Any, Optional

from .output import SafeOutput

MIN_IOI_S = 0.08          # answer notes are at least this far apart
MAX_IOI_BEATS = 1.5       # ...and at most this many beats
LAST_NOTE_BEATS = 1.5     # the answer's last note rings this long
GATE = 0.9                # notes sound for this share of their interval
SPAN = 9                  # answer notes stay within this many semitones of the voice's centre


def _nearest_in(pitch: int, pcs) -> int:
    """The pitch nearest to `pitch` whose pitch class is in pcs (ties: lower)."""
    if not pcs:
        return pitch
    for d in range(12):
        for p in (pitch - d, pitch + d):
            if p % 12 in pcs:
                return p
    return pitch


def _fold(pitch: int, centre: int) -> int:
    """Octave-fold a pitch to within SPAN semitones of the voice's centre."""
    while pitch > centre + SPAN:
        pitch -= 12
    while pitch < centre - SPAN:
        pitch += 12
    return pitch


def _step(pitch: int, steps: int, scale) -> int:
    """Move `pitch` (on the scale) by `steps` scale degrees."""
    ladder = sorted(scale)
    p = pitch
    for _ in range(abs(steps)):
        p += 1 if steps > 0 else -1
        while p % 12 not in ladder:
            p += 1 if steps > 0 else -1
    return p


def make_answer(phrase: list[tuple[float, int, int]], chord_pcs, scale_pcs, rng: random.Random,
                cfg: Any, period: float, gain: float = 1.0,
                grid: bool = False) -> list[tuple[float, int, int, float]]:
    """[(offset s, note, velocity, duration s)] answering `phrase` [(t, note, velocity)].
    grid=True: the rhythm is rounded to eighth notes of `period` (the beat is known)."""
    src = phrase[-cfg.max_notes:]
    if not src:
        return []
    scale = set(scale_pcs) if scale_pcs else {n % 12 for _, n, _ in src} | set(chord_pcs or ())
    chord = set(chord_pcs) if chord_pcs else scale
    pitches = [_nearest_in(n, scale) for _, n, _ in src]
    iois = [b[0] - a[0] for a, b in zip(src, src[1:])]
    vels = [v for _, _, v in src]
    mean_vel = sum(vels) / len(vels)

    # Mostly the motif itself, moved a step or two (recognisably yours); now and then, with
    # probability `variety`, turned upside down or played backwards.
    kind = "sequence"
    if rng.random() < cfg.variety:
        kind = rng.choice(("inversion", "retrograde"))
    if kind == "sequence":
        k = rng.choice((-2, -1, 1, 2))
        pitches = [_step(p, k, scale) for p in pitches]
    elif kind == "inversion":
        first = pitches[0]
        pitches = [_nearest_in(2 * first - p, scale) for p in pitches]
    else:
        pitches, iois, vels = pitches[::-1], iois[::-1], vels[::-1]

    # Into the response voice's register: centre the line on its octave.
    centre = 12 * (cfg.octave + 1) + 4
    shift = round((centre - sum(pitches) / len(pitches)) / 12) * 12
    pitches = [_fold(p + shift, centre) for p in pitches]
    pitches[-1] = _fold(_nearest_in(pitches[-1], chord), centre)   # land on a chord tone

    if grid:
        # On the beat: each interval rounded to whole eighth notes (at least one, at most
        # MAX_IOI_BEATS), so the answer lands exactly on the grid whatever the timing heard.
        step = period / 2
        max_steps = int(MAX_IOI_BEATS * 2)
        iois = [min(max(round(x / step), 1), max_steps) * step for x in iois]
    out, t = [], 0.0
    for i, p in enumerate(pitches):
        if i < len(iois):
            gap = min(max(iois[i], MIN_IOI_S), MAX_IOI_BEATS * period)
            dur = GATE * gap
        else:
            gap, dur = 0.0, LAST_NOTE_BEATS * period
        vel = min(max(round(cfg.velocity * vels[i] / mean_vel * gain), 1), 127)   # your accents
        out.append((t, p, vel, dur))
        t += gap
    return out


class ResponseResponder:
    def __init__(self, cfg: Any, out: SafeOutput, seed: int = 0) -> None:
        self.cfg, self.out = cfg, out
        self.rng = random.Random(seed)
        self.phrase: list[tuple[float, int, int]] = []
        self.last_t: Optional[float] = None
        self.answered = False
        self._queue: list[tuple[float, int, int, int, float]] = []   # (t, seq, note, vel, dur)
        self._seq = itertools.count()
        self._sounding: dict[int, float] = {}                       # note -> ends at

    def hear(self, t: float, note: int, velocity: int, period: float) -> None:
        """You played: stop answering at once (unless yield_to_you is off); the note joins your
        phrase (or starts one)."""
        if self.cfg.yield_to_you:
            self.cancel()
        if self.answered or (self.last_t is not None and t - self.last_t >= self._gap(period)):
            self.phrase, self.answered = [], False
        self.phrase.append((t, note, velocity))
        self.last_t = t

    def _gap(self, period: float) -> float:
        return max(self.cfg.gap_beats * period, self.cfg.min_gap_s)

    def tick(self, now: float, period: float, chord_pcs, scale_pcs, gain: float = 1.0,
             next_beat: Optional[float] = None) -> None:
        ch = self.cfg.channel - 1
        while self._queue and self._queue[0][0] <= now:
            t, _, note, vel, dur = heapq.heappop(self._queue)
            for other in list(self._sounding):          # one voice: one note at a time
                self.out.note_off(ch, other)
                del self._sounding[other]
            self.out.note_on(ch, note, vel)
            self.out.note_off_at(t + dur, ch, note)
            self._sounding[note] = t + dur
        if (not self.cfg.enabled or self.answered or self.last_t is None
                or len(self.phrase) < self.cfg.min_notes or now - self.last_t < self._gap(period)):
            return
        self.answered = True
        if self.rng.random() >= self.cfg.chance:
            return                                          # let this pause breathe
        self.cancel()                                       # a new answer replaces an old one
        start = now
        if next_beat is not None:                           # come in on the next beat
            start = next_beat - period * int((next_beat - now) / period)
        for off, note, vel, dur in make_answer(self.phrase, chord_pcs, scale_pcs, self.rng,
                                               self.cfg, period, gain, grid=next_beat is not None):
            heapq.heappush(self._queue, (start + off, next(self._seq), note, vel, dur))

    def cancel(self) -> None:
        """Stop the answer: drop what is still to come, silence what is sounding."""
        self._queue.clear()
        for note in list(self._sounding):
            self.out.note_off(self.cfg.channel - 1, note)
        self._sounding.clear()

    def reset(self) -> None:
        self.cancel()
        self.phrase, self.last_t, self.answered = [], None, False
