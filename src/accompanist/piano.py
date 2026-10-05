"""The piano: answers your phrases with a textbook arpeggio of the chord, on the beat.

It shares the answering with the guitar (response.py): when your phrase ends it takes some
turns itself (piano.share), and it also fills some of the gaps where the pad would swell,
once it has been quiet all round for a moment (piano.chance). Either way it plays an arpeggio
through the chord of the moment, starting on the next beat, in steady eighths (triplets now and then,
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


# Comping (an interlude, you resting): chord hits per meter as (beat in the bar, length in
# beats); two patterns each, alternating bar by bar. A meter not listed: every beat but the 1.
# Comping rhythms per meter: (beat in the bar, length in beats). A new one each bar, never the
# same twice running; the sparse ones (fewer hits) are likelier when you play busily. A
# hit at 3.5 in 4/4 anticipates the next bar, as jazz pianists do. Other meters: every beat
# but the 1, or the group starts.
COMP_RHYTHMS = {
    4: (((0, 1.4), (1.5, 0.4)),                      # Charleston
        ((1, 0.5), (3, 0.5)),                        # on 2 and 4
        ((1.5, 1.0), (3.5, 0.9)),                    # pushed: and-of-2, and-of-4
        ((0, 3.5),),                                 # a held whole note
        ((0.5, 0.4), (2.5, 0.9)),                    # and-of-1, and-of-3
        ((3.5, 1.0),),                               # one anticipation into the next bar
        ((0, 0.9), (1.5, 0.4), (3, 0.9))),           # Charleston with a 4
    3: (((1, 0.9), (2, 0.9)), ((0, 2.5),), ((0, 0.9), (1.5, 1.3)), ((1.5, 0.9),),
        ((0, 0.9), (2.5, 0.4))),
    5: (((1, 0.9), (2, 0.9), (4, 0.9)), ((0, 1.4), (3, 1.4)), ((0, 2.5), (3, 1.9))),
    6: (((0, 1.4), (3, 1.4)), ((2, 0.9), (5, 0.9)), ((0, 5.5),)),
    7: (((1, 0.9), (2, 0.9), (4, 0.9), (6, 0.9)), ((0, 1.4), (3, 0.9), (5, 0.9)),
        ((0, 2.5), (3, 1.5), (5, 1.5))),
}
COMP_SOFTER = 0.8          # comping velocity, as a share of piano.velocity
COMP_VOICES = 4            # notes in a comping chord
COMP_RANGE = (50, 74)      # where comping voicings sit (D3 to D5): the pianist's middle
SPARSE_HITS = 2            # a rhythm with at most this many hits counts as sparse


def rootless(root: int, pcs: set[int], scale: Optional[set[int]]) -> list[list[int]]:
    """Jazz voicings without the root (the bass has it): the A form (3 5|13 7 9) and the B
    form (7 9 3 5|13), as intervals above the root; colour tones (9, 13) only where they
    belong to the key; a chord with no seventh gets its 6 (6/9 voicing). Empty if the
    chord has no third (sus, power chords): use close voicing."""
    rel = {(pc - root) % 12 for pc in pcs}
    third = 4 if 4 in rel else 3 if 3 in rel else None
    if third is None:
        return []
    ok = lambda i: scale is None or (root + i) % 12 in scale
    seventh = 10 if 10 in rel else 11 if 11 in rel else 9 if 9 in rel else None
    if seventh is None:
        seventh = 9 if ok(9) else (10 if third == 3 and ok(10) else None)
    if seventh is None:
        return []
    fifth = 6 if 6 in rel and 7 not in rel else 8 if 8 in rel and 7 not in rel else 7
    colour = 9 if third == 4 and seventh == 10 and ok(9) else fifth   # a dominant's 13
    ninth = 2 if ok(2) and fifth != 6 else 0                          # half-dim: the root
    a = [third, colour, seventh, ninth + 12]
    b = [seventh - 12, ninth, third, colour]
    return [a, b]


def comp_voicing(pcs: set[int], low: int, high: int, prev: Optional[tuple[int, ...]],
                 root: Optional[int] = None, scale: Optional[set[int]] = None,
                 style: str = "close") -> tuple[int, ...]:
    """A comping chord in [low, high], moving as little as possible from the previous one:
    rootless A/B jazz voicings (style rootless, or auto with a seventh chord), else a close
    voicing of up to COMP_VOICES chord tones."""
    if style != "close" and root is not None:
        forms = rootless(root, pcs, scale)
        if forms and (style == "rootless" or len(pcs) >= 4):
            options = []
            for form in forms:
                for octave in range(low // 12 - 1, high // 12 + 1):
                    notes = tuple(sorted(12 * octave + root + i for i in form))
                    if notes[0] >= low and notes[-1] <= high:
                        options.append(notes)
            if options:
                if prev is None:
                    return options[len(options) // 2]
                return min(options, key=lambda o: sum(abs(a - b) for a, b in zip(o, prev)))
    tones = chord_tones(pcs, low, high)
    options = [tuple(tones[i:i + COMP_VOICES]) for i in range(len(tones))
               if len({n % 12 for n in tones[i:i + COMP_VOICES]}) == min(len(pcs), COMP_VOICES)]
    if not options:
        return tuple(tones[:COMP_VOICES])
    if prev is None:
        return options[len(options) // 2]
    return min(options, key=lambda o: sum(abs(a - b) for a, b in zip(o, prev)))


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
        self._claimed: Optional[list] = None        # a phrase it took from the guitar
        self._comp_prev: Optional[tuple[int, ...]] = None   # the last comping chord
        self._comp_rhythm = None                    # this bar's comping rhythm
        self._comp_queue: list[tuple[float, int, int, int, float]] = []   # comping hits: your
                                                    # playing doesn't cancel them
        self._comp_notes: set[int] = set()          # sounding notes that are comping

    def claim(self, phrase: list[tuple[float, int, int]]) -> bool:
        """Your phrase just ended: take this turn from the guitar (piano.share of them)?
        If so it is answered at the next tick, starting on the next beat."""
        if not self.cfg.enabled or not phrase or self.rng.random() >= self.cfg.share:
            return False
        self._claimed = list(phrase)
        return True

    def hear(self, t: float) -> None:
        """You played: the piano gives way at once."""
        self.cancel()

    def playing(self, now: float) -> bool:
        return bool(self._queue) or any(end > now for end in self._sounding.values())

    def answering(self) -> bool:
        """An answer (arpeggio) still to come: comping waits for it."""
        return bool(self._queue)

    def tick(self, now: float, period: float, chord, scale_pcs, gain: float,
             next_beat: Optional[float], response, quiet: float = 0.0) -> None:
        """chord: the Voicing sounding (None: nothing to arpeggiate); response: the guitar,
        whose phrase tracking says what your last phrase was; quiet: Dynamics.quiet(), above 0
        once it has been quiet all round long enough for the pad to swell. That moment is the
        piano's: it takes some of them (piano.chance), and while it plays the pad stays back,
        so the gaps alternate between a swell and an arpeggio."""
        ch = self.cfg.channel - 1
        for q in (self._queue, self._comp_queue):
            while q and q[0][0] <= now:
                t, _, note, vel, dur = heapq.heappop(q)
                self.out.note_on(ch, note, vel)
                self.out.note_off_at(t + dur, ch, note)
                self._sounding[note] = t + dur
                if q is self._comp_queue:
                    self._comp_notes.add(note)
                else:
                    self._comp_notes.discard(note)
        for note in [n for n, end in self._sounding.items() if end <= now]:
            del self._sounding[note]
            self._comp_notes.discard(note)
        if self._claimed is not None:                   # its turn instead of the guitar's
            phrase, self._claimed = self._claimed, None
            if self.cfg.enabled and chord is not None and next_beat is not None:
                self._answered_phrase = response.phrase_count
                self._answer(now, period, chord, gain, next_beat, phrase)
            return
        if (not self.cfg.enabled or chord is None or next_beat is None or quiet <= 0
                or not response.phrase):
            return
        phrase_id = response.phrase_count
        if phrase_id == self._answered_phrase or response.playing(now) or self.answering():
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

    def comp_beat(self, now: float, period: float, bar: int, bar_pos: int, bpb: int, chord,
                  gain: float, scale: Optional[set[int]] = None, density: float = 1.0,
                  busy: float = 0.0) -> None:
        """A comping beat (an interlude, or under your solo): the chord hits of this bar's
        rhythm that fall in this beat. density (0-1) thins them out; busy (0-1, how busily you
        play) leans to the sparse rhythms."""
        if not self.cfg.enabled or chord is None:
            return
        if bar_pos == 0 or self._comp_rhythm is None:
            self._comp_rhythm = self._pick_rhythm(bpb, busy)
        c = self.cfg
        pcs = {n % 12 for n in chord.notes}
        low, high = c.comp_low, c.comp_high
        voicing = comp_voicing(pcs, low, high, self._comp_prev, chord.root_pc, scale, c.voicing)
        self._comp_prev = voicing
        for beat, length in self._comp_rhythm:
            if int(beat) != bar_pos or self.rng.random() > density:
                continue
            t = now + (beat - bar_pos) * period
            vel = humanize_velocity(min(max(round(c.velocity * COMP_SOFTER * gain), 1), 127),
                                    c.velocity_spread, self.rng)
            for note in voicing:
                heapq.heappush(self._comp_queue, (t, next(self._seq), note, vel, length * period))

    def _pick_rhythm(self, bpb: int, busy: float):
        options = list(COMP_RHYTHMS.get(bpb, (tuple((b, 0.9) for b in range(1, bpb)),)))
        if len(options) > 1 and self._comp_rhythm in options:
            options.remove(self._comp_rhythm)                 # never the same twice running
        weights = [1.0 + (2.0 * busy if len(r) <= SPARSE_HITS else 0.0) for r in options]
        return self.rng.choices(options, weights)[0]

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
        """You played: the answer gives way (what's to come, and what's sounding); comping
        goes on."""
        self._queue.clear()
        for note in [n for n in self._sounding if n not in self._comp_notes]:
            self.out.note_off(self.cfg.channel - 1, note)
            del self._sounding[note]

    def stop(self) -> None:
        """Everything stops, answers and comping (a break, the end)."""
        self._queue.clear()
        self._comp_queue.clear()
        for note in list(self._sounding):
            self.out.note_off(self.cfg.channel - 1, note)
        self._sounding.clear()

    def reset(self) -> None:
        self.stop()
        self._answered_phrase, self._claimed = None, None
