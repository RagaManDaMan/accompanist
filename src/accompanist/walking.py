"""Walking bass, and the two-feel: how a jazz bassist moves through the changes.

Pure (no clock, no MIDI): for each beat, the notes to play, given the chord now, the scale,
and (when known: a chart) the next chord and how many beats until it.

Walking (pulse.line = "walk"): a note on every beat.
  - a new chord: its root on the beat it arrives (now and then its third or fifth, when the
    chord just carries on over the bar line);
  - the beat before a new chord: an approach into its root: a half step from below or above,
    a step from the scale, or its fifth (the dominant approach); now and then an enclosure
    over the last two beats (a half step above, then below);
  - between: chord tones and steps of the scale, moving on from the last note, never the
    same note twice, heading for where the next root lies, kept in the bass's range;
  - with pulse.rhythm, now and then a "skip": a swung eighth before the next beat.
Two-feel (pulse.line = "two"): half notes, the root and then the fifth (or an approach
into the next chord), with now and then an eighth-note pickup.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

# Approaches into the next root, and how likely each is.
APPROACHES = (("below", 0.35), ("above", 0.25), ("scale", 0.2), ("fifth", 0.2))
ENCLOSURE_CHANCE = 0.15          # the last two beats: above, then below the target
KEEP_ROOT_CHANCE = 0.7           # a chord carrying on over the bar line: its root again on 1
CHORD_TONE_WEIGHT = 2.0          # between: chord tones over scale steps
STEP_WEIGHT = 1.6                # a step (1-2 semitones) over a leap
ON_COURSE_WEIGHT = 1.8           # heading for the next root
SAME_WAY_WEIGHT = 1.3            # carrying on in the same direction
MAX_MOVE = 7                     # no leaps wider than a fifth between beats
SKIP_CHANCE = 0.3                # pulse.rhythm 1: a skip on this share of the beats
SWING_AT = 2 / 3                 # where a swung eighth falls in the beat
RANGE_BELOW, RANGE_ABOVE = 8, 19 # the walking range around the bass octave's C


@dataclass(frozen=True)
class Note:
    at: float          # beats from this beat
    pitch: int
    length: float      # beats
    level: float = 1.0 # share of the beat's velocity


class Walker:
    def __init__(self, base: int, rng: random.Random) -> None:
        """base: the bass octave's C (pulse.octave)."""
        self.low, self.high = base - RANGE_BELOW, base + RANGE_ABOVE
        self.rng = rng
        self.prev: Optional[int] = None
        self.prev_root: Optional[int] = None
        self.direction = 1
        self._enclose: Optional[int] = None    # the target of an enclosure under way

    def _near(self, pc: int, to: Optional[int]) -> int:
        """The pitch of class pc in range nearest `to` (or the lowest one above low)."""
        options = [n for n in range(self.low, self.high + 1) if n % 12 == pc]
        to = self.low + RANGE_BELOW if to is None else to        # (else near the octave's C)
        return min(options, key=lambda n: (abs(n - to), n))

    def _approach(self, target: int, scale: set[int]) -> int:
        names = [n for n, _ in APPROACHES]
        weights = [w for _, w in APPROACHES]
        for _ in range(4):
            kind = self.rng.choices(names, weights)[0]
            if kind == "below":
                note = target - 1
            elif kind == "above":
                note = target + 1
            elif kind == "scale":
                steps = [target + d for d in (-2, 2, -1, 1) if (target + d) % 12 in scale]
                note = steps[0] if steps else target - 1
            else:
                note = min((target + 7, target - 5), key=lambda n: abs(n - (self.prev or target)))
            if self.low <= note <= self.high and note != self.prev:
                return note
        return target - 1 if target - 1 >= self.low and target - 1 != self.prev else target + 1

    def _between(self, chord_pcs: set[int], scale: set[int], toward: int) -> int:
        prev = self.prev if self.prev is not None else toward
        options = []
        for n in range(max(self.low, prev - MAX_MOVE), min(self.high, prev + MAX_MOVE) + 1):
            if n == prev or (n % 12 not in chord_pcs and n % 12 not in scale):
                continue
            w = CHORD_TONE_WEIGHT if n % 12 in chord_pcs else 1.0
            if abs(n - prev) <= 2:
                w *= STEP_WEIGHT
            if (n - prev) * (toward - prev) > 0 or n == toward:
                w *= ON_COURSE_WEIGHT
            if (n - prev) * self.direction > 0:
                w *= SAME_WAY_WEIGHT
            if n == toward:
                w *= 0.3                        # arriving early would repeat it on the change
            options.append((n, w))
        if not options:
            return self._near(next(iter(chord_pcs)), prev)
        notes, weights = zip(*options)
        return self.rng.choices(notes, weights)[0]

    def walk(self, pos: int, root_pc: int, chord_pcs: set[int], scale: Optional[set[int]],
             next_root_pc: Optional[int], beats_to_change: Optional[int],
             skip: float = 0.0) -> list[Note]:
        """This beat's notes. beats_to_change: beats until the next chord starts, counting
        from this one (1: the next beat is a new chord); None: unknown (the bar's end)."""
        scale = set(scale or ()) | chord_pcs
        target_pc = root_pc if next_root_pc is None else next_root_pc
        new_chord = root_pc != self.prev_root
        self.prev_root = root_pc
        if new_chord or self.prev is None:
            note = self._near(root_pc, self.prev)
            self._enclose = None
        elif pos == 0 and self.rng.random() > KEEP_ROOT_CHANCE:
            others = [pc for pc in chord_pcs if pc != root_pc]
            note = self._near(self.rng.choice(others), self.prev) if others else self._near(root_pc, self.prev)
        elif pos == 0:
            note = self._near(root_pc, self.prev)
        else:
            target = self._near(target_pc, self.prev)
            if self._enclose is not None and beats_to_change == 1:
                note, self._enclose = self._enclose - 1, None          # below, after above
            elif beats_to_change == 2 and self.rng.random() < ENCLOSURE_CHANCE \
                    and target + 1 <= self.high and target - 1 >= self.low:
                note, self._enclose = target + 1, target
            elif beats_to_change == 1:
                note = self._approach(target, scale)
            else:
                note = self._between(chord_pcs, scale, target)
        if note == self.prev and not new_chord:                       # never twice
            note = self._between(chord_pcs, scale, note)
        if self.prev is not None and note != self.prev:
            self.direction = 1 if note > self.prev else -1
        self.prev = note
        out = [Note(0.0, note, 0.9)]
        if skip > 0 and beats_to_change != 1 and self.rng.random() < skip * SKIP_CHANCE:
            out.append(Note(SWING_AT, note, 0.25, 0.6))               # the skip: a ghost
        return out

    def two(self, pos: int, bpb: int, root_pc: int, chord_pcs: set[int], scale: Optional[set[int]],
            next_root_pc: Optional[int], beats_to_change: Optional[int],
            pickup: float = 0.0) -> list[Note]:
        """The two-feel: the root on 1, the fifth (or an approach) halfway, half notes."""
        half = bpb // 2 if bpb >= 4 else None
        if pos != 0 and pos != half:
            return []
        new_chord = root_pc != self.prev_root
        self.prev_root = root_pc
        if pos == 0 or new_chord:
            note = self._near(root_pc, self.prev)
        else:
            to_change = beats_to_change if beats_to_change is not None else bpb - pos
            if next_root_pc is not None and to_change <= bpb - pos:
                note = self._approach(self._near(next_root_pc, self.prev), set(scale or ()) | chord_pcs)
            else:
                fifth = (root_pc + 7) % 12 if (root_pc + 7) % 12 in chord_pcs or not chord_pcs \
                    else (root_pc + 6) % 12
                note = self._near(fifth, self.prev)
        self.prev = note
        span = (half if pos == 0 and half else bpb - pos)
        out = [Note(0.0, note, span - 0.1)]
        if pickup > 0 and self.rng.random() < pickup * SKIP_CHANCE:   # a pickup into the next
            out.append(Note(span - 1 + SWING_AT, note + self.rng.choice((-1, 1)), 0.25, 0.7))
        return out

    def reset(self) -> None:
        self.prev = self.prev_root = self._enclose = None
        self.direction = 1
