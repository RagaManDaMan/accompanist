"""The piano: answers your phrases with a textbook arpeggio of the chord, on the beat.

It takes its turn after the guitar (the answer, response.py): once your phrase has ended and
the guitar has had its say (or let the pause go), the piano plays an arpeggio through the
chord of the moment, starting on the next beat, in steady eighths (triplets now and then,
quarters at fast tempi). It follows your idea's shape: up if your phrase went up, down if
it went down, about as long as your phrase, ending on a held chord tone. You playing again
stops it at once. The guitar is rubato and yours; the piano is strict and the band's.
"""
from __future__ import annotations

import heapq
import itertools
import random
from typing import Any, Optional

from .output import SafeOutput
from .responders import humanize_velocity

# The arpeggio: notes per beat for each subdivision; the share of each step a note sounds
# (legato); the last note's length in beats; how much louder the first note of each beat is;
# how often (at piano.variety = 1) triplets replace eighths.
SUBDIVISIONS = {"quarters": 1, "eighths": 2, "triplets": 3}
LEGATO = 0.95
LAST_NOTE_BEATS = 1.5
BEAT_ACCENT = 1.15
TRIPLET_CHANCE = 0.5
# Notes in the answer per note of your phrase, and the shortest answer (in notes).
NOTES_PER_NOTE = 1.0
MIN_NOTES = 4
SEMITONES_PER_TONE = 3.5   # an arpeggio climbs about this much per note


def textbook(pcs: set[int], root_pc: int) -> set[int]:
    """The chord's root, third, fifth and seventh: what an arpeggio outlines (colour tones
    like an added 9th make it sound like a scale)."""
    core = {pc for pc in pcs if (pc - root_pc) % 12 in (0, 3, 4, 6, 7, 8, 10, 11)}
    return core if len(core) >= 3 else pcs


def fold(pitch: int, low: int, high: int) -> int:
    """pitch moved by octaves into [low, high]."""
    while pitch < low:
        pitch += 12
    while pitch > high:
        pitch -= 12
    return pitch


def chord_tones(pcs: set[int], low: int, high: int) -> list[int]:
    """Every note in [low, high] whose pitch class is in the chord, low to high."""
    return [n for n in range(low, high + 1) if n % 12 in pcs]


def arpeggio(pcs: set[int], root_pc: int, start: int, count: int, up: bool,
             low: int, high: int) -> list[int]:
    """`count` chord tones from the chord tone nearest `start`, going up (or down), turning
    back at the edges of [low, high]; the last note is the root or a chord tone a step away
    from the one before (it should sound like an ending)."""
    tones = chord_tones(pcs, low, high)
    if not tones:
        return []
    i = min(range(len(tones)), key=lambda k: abs(tones[k] - start))
    step = 1 if up else -1
    out = []
    for _ in range(count):
        out.append(tones[i])
        if not 0 <= i + step < len(tones):
            step = -step                              # the edge of the range: turn back
        i += step
    roots = [n for n in tones if n % 12 == root_pc and n != out[-2]] if len(out) > 1 else []
    if roots and out[-1] % 12 != root_pc:             # land on the root nearest the last note
        out[-1] = min(roots, key=lambda n: abs(n - out[-2]))
    return out


class PianoResponder:
    def __init__(self, cfg: Any, out: SafeOutput, seed: int = 0) -> None:
        self.cfg, self.out = cfg, out
        self.rng = random.Random(seed + 21)
        self._queue: list[tuple[float, int, int, int, float]] = []   # (t, seq, note, vel, dur)
        self._seq = itertools.count()
        self._sounding: dict[int, float] = {}       # note -> ends at
        self._answered_phrase: Optional[int] = None  # id of the phrase it has answered

    def hear(self, t: float) -> None:
        """You played: the piano gives way at once."""
        self.cancel()

    def playing(self, now: float) -> bool:
        return bool(self._queue) or any(end > now for end in self._sounding.values())

    def tick(self, now: float, period: float, chord, scale_pcs, gain: float,
             next_beat: Optional[float], response) -> None:
        """chord: the Voicing sounding (None: nothing to arpeggiate); response: the guitar,
        whose phrase tracking says when your phrase has ended and whether it is still
        answering."""
        ch = self.cfg.channel - 1
        while self._queue and self._queue[0][0] <= now:
            t, _, note, vel, dur = heapq.heappop(self._queue)
            self.out.note_on(ch, note, vel)
            self.out.note_off_at(t + dur, ch, note)
            self._sounding[note] = t + dur
        if (not self.cfg.enabled or chord is None or next_beat is None
                or not response.answered or not response.phrase):
            return
        phrase_id = response.phrase_count
        if phrase_id == self._answered_phrase or response.playing(now) or self.playing(now):
            return
        self._answered_phrase = phrase_id               # its turn, once per phrase
        if self.rng.random() >= self.cfg.chance:
            return
        self._answer(now, period, chord, gain, next_beat, response.phrase)

    def _answer(self, now: float, period: float, chord, gain: float, next_beat: float,
                phrase: list[tuple[float, int, int]]) -> None:
        c = self.cfg
        bpm = 60.0 / period
        sub = c.subdivision
        if sub == "auto":
            sub = "quarters" if bpm > c.fast_bpm else (
                "triplets" if self.rng.random() < TRIPLET_CHANCE * c.variety else "eighths")
        per_beat = SUBDIVISIONS[sub]
        notes_in = [n for _, n, _ in phrase]
        count = max(MIN_NOTES, round(len(notes_in) * NOTES_PER_NOTE))
        count = min(count, c.max_beats * per_beat)
        up = notes_in[-1] >= notes_in[0]               # your idea's direction...
        low = 12 * (c.octave + 1) - 5
        high = low + 12 * c.range_octaves + 5
        start = fold(notes_in[-1], low, high)          # ...picked up where you left off,
        span = SEMITONES_PER_TONE * count              # with room to run (an octave over)
        if up and start + span > high and start - 12 >= low:
            start -= 12
        elif not up and start - span < low and start + 12 <= high:
            start += 12
        pcs = textbook({n % 12 for n in chord.notes}, chord.root_pc)
        pitches = arpeggio(pcs, chord.root_pc, start, count, up, low, high)
        step = period / per_beat
        begin = next_beat
        while begin < now:
            begin += period
        for k, note in enumerate(pitches):
            t = begin + k * step
            if c.timing_ms > 0:
                t += self.rng.uniform(-c.timing_ms, c.timing_ms) / 1000 if k else 0.0
            last = k == len(pitches) - 1
            dur = LAST_NOTE_BEATS * period if last else LEGATO * step
            vel = c.velocity * gain * (BEAT_ACCENT if k % per_beat == 0 else 1.0)
            vel = humanize_velocity(min(max(round(vel), 1), 127), c.velocity_spread, self.rng)
            heapq.heappush(self._queue, (t, next(self._seq), note, vel, dur))

    def final_chord(self, now: float, chord, period: float) -> None:
        """The ending: the last chord, rolled up quickly from the root."""
        if not self.cfg.enabled or chord is None:
            return
        self.cancel()
        c = self.cfg
        low = 12 * (c.octave + 1)
        pitches = chord_tones({n % 12 for n in chord.notes}, low, low + 12)
        roll = period / 12
        for k, note in enumerate(pitches):
            heapq.heappush(self._queue, (now + k * roll, next(self._seq), note,
                                         min(127, c.velocity), 4 * period))

    def cancel(self) -> None:
        self._queue.clear()
        for note in list(self._sounding):
            self.out.note_off(self.cfg.channel - 1, note)
        self._sounding.clear()

    def reset(self) -> None:
        self.cancel()
        self._answered_phrase = None
