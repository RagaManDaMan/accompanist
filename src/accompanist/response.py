"""Call and response: when you pause after a phrase, answer it on its own channel.

By default the answer is one of *your own* phrases: every phrase you play is remembered
(response.memory of them), and the answer is an earlier one whose notes fit the harmony
of the moment (response.fit), played as you played it (your notes, your register; its
rhythm rounded to eighth notes and starting on the next beat when the beat is known). If
none fits, it echoes the phrase you just played. With probability response.variety it
plays a variation instead (make_answer: your motif moved a scale step, inverted or
reversed). It gives way to you as much as response.yield_to_you says (1 = stops at once,
0 = finishes over you). Seeded, so a replay answers exactly as the live run did.

`choose_phrase` and `make_answer` are pure; ResponseResponder does the listening, memory,
timing and MIDI (through SafeOutput).
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
REGISTER_NOTES = 40       # your register: the middle (median) of your last this many notes;
REGISTER_SPAN = 17        # answers and solos centre there, ranging up to this far from it
                          # (an octave and a fourth: room for a run, not the whole fretboard)
MAX_LEAP = 12             # and no jump between two notes wider than an octave
HELD_BEATS = 0.5          # outside a rāga, only notes held this long are kept in the key
# Your phrase is over after a pause: response.gap_beats (at least min_gap_s), and at least
# this many times your recent typical time between notes, so long sung notes and slow
# phrases aren't cut into single notes (the band hears only where notes start).
PHRASE_IOI_FACTOR = 1.6
PHRASE_IOI_NOTES = 12     # (the typical time: the median of this many recent intervals)
PHRASE_IOI_MAX_S = 4.0    # ...counting only intervals shorter than this (not the rests)
MAX_PHRASE_GAP_S = 2.5    # but never waiting longer than this
BLIP_S = 0.09             # a remembered phrase drops notes shorter than this
# Rhythmic variations of a phrase (response.rhythm_variety): name -> (interval scale, grid
# notes per beat). Double time only up to DOUBLE_MAX_BPM, half time only from HALF_MIN_BPM.
RHYTHMS = {"double": (0.5, 4), "half": (2.0, 1), "triplet": (1.0, 3)}
RHYTHM_WEIGHTS = {"double": 0.35, "half": 0.35, "triplet": 0.3}
DOUBLE_MAX_BPM = 150
HALF_MIN_BPM = 70
SOLO_BREATH_BEATS = 1.0   # a solo's phrases are this far apart
SOLO_MAX_NOTES = 64       # a solo is at most this long
OUTLIER = 12              # ...and folds notes this far from both neighbours


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

    if cfg.octave is not None:                  # into a fixed register
        centre = 12 * (cfg.octave + 1) + 4
        shift = round((centre - sum(pitches) / len(pitches)) / 12) * 12
        pitches = [_fold(p + shift, centre) for p in pitches]
        pitches[-1] = _fold(_nearest_in(pitches[-1], chord), centre)   # land on a chord tone
    else:                                        # your register
        pitches[-1] = _nearest_in(pitches[-1], chord)
    return render(pitches, iois, vels, cfg, period, gain, grid)


def render(pitches: list[int], iois: list[float], vels: list[int], cfg: Any, period: float,
           gain: float = 1.0, grid: bool = False,
           per_beat: int = 2) -> list[tuple[float, int, int, float]]:
    """Notes + intervals -> [(offset s, note, velocity, duration s)], one note at a time.
    grid: the rhythm moves toward a grid of per_beat notes a beat (eighths by default) by
    response.quantize (1 = on the grid)."""
    if grid:
        step = period / per_beat
        q = cfg.quantize
        iois = [q * min(max(round(x / step), 1), int(MAX_IOI_BEATS * per_beat)) * step
                + (1 - q) * x for x in iois]
    mean_vel = sum(vels) / len(vels)
    out, t = [], 0.0
    for i, p in enumerate(pitches):
        if i < len(iois):
            gap = min(max(iois[i], MIN_IOI_S), MAX_IOI_BEATS * period)
            dur = GATE * gap
        else:
            gap, dur = 0.0, LAST_NOTE_BEATS * period
        vel = min(max(round(cfg.velocity * vels[i] / mean_vel * gain), 1), 127)
        out.append((t, p, vel, dur))
        t += gap
    return out


def clean_phrase(phrase: list[tuple[float, int, int]]) -> list[tuple[float, int, int]]:
    """Tidy a phrase heard in audio before it is remembered: drop blips (a note lasting
    under BLIP_S before the next), and move a note that sits an octave or more away from
    *both* its neighbours by octaves toward them (a pitch-detection slip; a real one-sided
    octave leap is kept)."""
    notes = [p for i, p in enumerate(phrase)
             if i == len(phrase) - 1 or phrase[i + 1][0] - p[0] >= BLIP_S]
    out = list(notes)
    for i in range(len(out)):
        t, n, v = out[i]
        if i == 0 or i == len(out) - 1:
            continue                                    # one neighbour: a leap may be real
        near = [out[i - 1][1], out[i + 1][1]]
        if all(abs(n - m) >= OUTLIER for m in near):
            target = sum(near) / len(near)
            while n - target > 6:
                n -= 12
            while target - n > 6:
                n += 12
            out[i] = (t, n, v)
    return out


def fit(phrase: list[tuple[float, int, int]], allowed) -> float:
    """Share of the phrase's notes whose pitch class is allowed."""
    return sum(1 for _, n, _ in phrase if n % 12 in allowed) / len(phrase) if phrase else 0.0


def choose_phrase(current: list[tuple[float, int, int]], memory: list[list[tuple[float, int, int]]],
                  chord_pcs, scale_pcs, rng: random.Random, cfg: Any,
                  avoid: Optional[int] = None) -> tuple[list[tuple[float, int, int]], Optional[int]]:
    """(phrase to play, its index in memory or None for an echo of `current`).

    Candidates are remembered phrases (not the one just played) whose notes fit the harmony
    of the moment: the chord and the key if known, else the chord and what you are playing
    now. Among those, a fitter phrase is likelier; the one used last time is skipped."""
    allowed = set(chord_pcs or ()) | set(scale_pcs or ())
    if not scale_pcs:
        allowed |= {n % 12 for _, n, _ in current}
    options = [(i, fit(ph[: cfg.max_notes], allowed)) for i, ph in enumerate(memory) if i != avoid]
    options = [(i, f) for i, f in options if f >= cfg.fit]
    if not options:
        return current[: cfg.max_notes], None
    i = rng.choices([i for i, _ in options], [f for _, f in options])[0]
    return memory[i][: cfg.max_notes], i


class ResponseResponder:
    def __init__(self, cfg: Any, out: SafeOutput, seed: int = 0) -> None:
        self.cfg, self.out = cfg, out
        self.rng = random.Random(seed)
        self.phrase: list[tuple[float, int, int]] = []
        self.memory: list[list[tuple[float, int, int]]] = []       # your phrases, oldest first
        self._last_used: Optional[int] = None
        self.last_t: Optional[float] = None
        self.answered = False
        self.phrase_count = 0                                       # phrases heard so far
        self.partner = None            # another answerer (the piano) that may take a turn
        self.book = None               # your phrase library (phrasebook.Phrasebook), if any
        self.key: Optional[tuple[int, str]] = None   # the key of the moment (from the engine)
        self._queue: list[tuple[float, int, int, int, float]] = []   # (t, seq, note, vel, dur)
        self._seq = itertools.count()
        self._sounding: dict[int, float] = {}                       # note -> ends at
        self._heard: list[int] = []                                 # your recent notes
        self._iois: list[float] = []                                # ...and their spacing
        self.first_t: Optional[float] = None                        # when you began

    def hear(self, t: float, note: int, velocity: int, period: float, lead: bool = True) -> None:
        """You played: the answer gives way (yield_to_you); the note joins your phrase (or
        starts one). lead=False: give way only (another instrument under your lead)."""
        self._give_way(self.cfg.yield_to_you)
        if not lead:
            return
        if self.answered or (self.last_t is not None and t - self.last_t >= self._gap(period)):
            self.phrase, self.answered = [], False
            self.phrase_count += 1
        if self.first_t is None:
            self.first_t = t
        if self.last_t is not None and 0 < t - self.last_t < PHRASE_IOI_MAX_S:
            self._iois = (self._iois + [t - self.last_t])[-PHRASE_IOI_NOTES:]
        self.phrase.append((t, note, velocity))
        self.last_t = t
        self._heard = (self._heard + [note])[-REGISTER_NOTES:]

    @property
    def register(self) -> Optional[float]:
        """The middle of your recent playing (a median: an octave slip doesn't move it)."""
        if not self._heard:
            return None
        h = sorted(self._heard)
        return float(h[len(h) // 2])

    def in_key(self, pitches: list[int], iois: list[float], chord_pcs, scale_pcs,
               period: float) -> list[int]:
        """Notes outside the key (a slip in what was heard, a phrase from another key) moved
        to the nearest note of the scale or chord. In a rāga every note; otherwise only held
        ones (half a beat or more): a quick chromatic passing note is jazz, a held one is a
        wrong note."""
        allowed = set(scale_pcs or ()) | set(chord_pcs or ())
        if not allowed:
            return pitches
        from .modal import MODES
        raga = self.key is not None and self.key[1] not in MODES
        out = []
        for i, p in enumerate(pitches):
            held = i >= len(iois) or iois[i] >= HELD_BEATS * period
            out.append(_nearest_in(p, allowed) if (raga or held) and p % 12 not in allowed else p)
        return out

    def centred(self, pitches: list[int]) -> list[int]:
        """Tastefully human: the phrase moved by octaves so its middle sits where you sing
        (response.octave unset) or in that octave. A run may climb or fall an octave or more,
        as a guitarist's does, but stays within REGISTER_SPAN of the centre, and a note that
        would jump more than MAX_LEAP from the one before (an octave slip in what was heard,
        or a phrase stitched from two registers) moves an octave toward it."""
        centre = (self.register + self.cfg.above_you if self.register is not None else None) \
            if self.cfg.octave is None else 12 * (self.cfg.octave + 1) + 4
        if centre is None or not pitches:
            return pitches
        mid = sorted(pitches)[len(pitches) // 2]
        shift = round((centre - mid) / 12) * 12
        out: list[int] = []
        for p in pitches:
            p += shift
            while p > centre + REGISTER_SPAN:
                p -= 12
            while p < centre - REGISTER_SPAN:
                p += 12
            if out:                                  # continuity over the edge of the span
                while p - out[-1] > MAX_LEAP:
                    p -= 12
                while out[-1] - p > MAX_LEAP:
                    p += 12
            out.append(p)
        return out

    def _give_way(self, amount: float) -> None:
        """1: stop at once. 0: carry on. In between: keep that share (1 - amount) of the notes
        still to come, softer by half of amount; the sounding note rings on."""
        if amount <= 0 or not self._queue:
            if amount >= 1:
                self.cancel()
            return
        if amount >= 1:
            self.cancel()
            return
        rest = sorted(self._queue)
        keep = rest[: round((1 - amount) * len(rest))]
        soften = 1 - amount / 2
        self._queue = [(t, q, n, max(1, round(v * soften)), d) for t, q, n, v, d in keep]
        heapq.heapify(self._queue)

    def _gap(self, period: float) -> float:
        gap = max(self.cfg.gap_beats * period, self.cfg.min_gap_s)
        if len(self._iois) >= 4:                    # your pace: long notes, slow phrases
            typical = sorted(self._iois)[len(self._iois) // 2]
            gap = max(gap, min(PHRASE_IOI_FACTOR * typical, MAX_PHRASE_GAP_S))
        return gap

    def _remember(self, phrase: list[tuple[float, int, int]]) -> None:
        phrase = clean_phrase(phrase)
        if not phrase:
            return
        self.memory.append(phrase)
        if len(self.memory) > self.cfg.memory:
            self.memory.pop(0)
            if self._last_used is not None:
                self._last_used -= 1

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
        if (self.answered or self.last_t is None or now - self.last_t < self._gap(period)
                or len(self.phrase) < self.cfg.min_notes):
            return
        self.answered = True                                # your phrase is over
        if self.first_t is None or now - self.first_t < self.cfg.warmup_s:
            self._remember(clean_phrase(self.phrase) or list(self.phrase))
            return                                          # still listening to you
        current = clean_phrase(self.phrase) or list(self.phrase)
        if self.partner is not None and self.partner.claim(current):
            self._remember(current)
            return                                          # the piano takes this one
        if not self.cfg.enabled or self.rng.random() >= self.cfg.chance:
            self._remember(current)
            return                                          # let this pause breathe
        grid = next_beat is not None
        if self.rng.random() < self.cfg.variety:            # a variation of what you just played
            notes = make_answer(current, chord_pcs, scale_pcs, self.rng, self.cfg, period, gain, grid)
            notes = [(o, n, v, d) for (o, _, v, d), n in zip(notes, self.centred([x[1] for x in notes]))]
        else:                                               # one of your own phrases
            if self.cfg.curate >= 1 or self.rng.random() < self.cfg.curate:   # an earlier one...
                phrase, used = self._recall(current, chord_pcs, scale_pcs)
            else:                                           # ...or an echo of the last one
                phrase, used = current[: self.cfg.max_notes], None
            self._last_used = used
            notes = self._phrase_notes(phrase, period, gain, grid, chord_pcs, scale_pcs)
        self._remember(current)
        self._schedule(notes, now, period, next_beat)

    def _phrase_notes(self, phrase, period: float, gain: float, grid: bool,
                      chord_pcs=None, scale_pcs=None):
        pitches = self.centred([n for _, n, _ in phrase])   # where you sing (or the octave)
        pitches = self.in_key(pitches, [b[0] - a[0] for a, b in zip(phrase, phrase[1:])],
                              chord_pcs, scale_pcs, period)
        iois = [b[0] - a[0] for a, b in zip(phrase, phrase[1:])]
        rhythm = self._pick_rhythm(period) if grid else None
        scale, per_beat = RHYTHMS.get(rhythm, (1.0, 2))
        return render(pitches, [x * scale for x in iois], [v for _, _, v in phrase], self.cfg,
                      period, gain, grid, per_beat)

    def _pick_rhythm(self, period: float) -> Optional[str]:
        """As you played it, or now and then (response.rhythm_variety) in double time, half
        time or triplets."""
        v = self.cfg.rhythm_variety
        if v <= 0 or self.rng.random() >= v:
            return None
        bpm = 60.0 / period
        options = {k: w for k, w in RHYTHM_WEIGHTS.items()
                   if not (k == "double" and bpm > DOUBLE_MAX_BPM)
                   and not (k == "half" and bpm < HALF_MIN_BPM)}
        if not options:
            return None
        names = list(options)
        return self.rng.choices(names, [options[k] for k in names])[0]

    def _schedule(self, notes, now: float, period: float, next_beat: Optional[float]) -> None:
        self.cancel()                                       # a new answer replaces an old one
        start = now
        if next_beat is not None:                           # come in on the next beat
            start = next_beat - period * int((next_beat - now) / period)
        for off, note, vel, dur in notes:
            heapq.heappush(self._queue, (start + off, next(self._seq), note, vel, dur))

    def _recall(self, current, chord_pcs, scale_pcs):
        """(phrase, index in this run's memory or None): from your whole library now and
        then (response.library), moved to the key of the moment; else from this run."""
        if (self.book is not None and self.book.phrases and self.key is not None
                and self.rng.random() < self.cfg.library):
            allowed = set(chord_pcs or ()) | set(scale_pcs or ())
            register = (sum(n for _, n, _ in current) / len(current)) if current else None
            found = self.book.choose(current, self.key, allowed, self.rng, self.cfg.fit,
                                     self.cfg.max_notes, register)
            if found:
                return found, None
        if not self.memory:
            return current[: self.cfg.max_notes], None
        return choose_phrase(current, self.memory, chord_pcs, scale_pcs, self.rng, self.cfg,
                             self._last_used)

    def play_from_memory(self, now: float, period: float, chord_pcs, scale_pcs, gain: float,
                         next_beat: Optional[float], beats: float = 0.0) -> bool:
        """In an interlude (you resting): a solo of your remembered phrases that fit the
        harmony, on the beat, each in its own rhythm (as played, double, half or triplets),
        strung together to last about `beats` beats (0: one phrase). False if nothing is
        remembered yet."""
        if not self.cfg.enabled or not (self.memory or (self.book and self.book.phrases)):
            return False
        notes, end = [], 0.0
        while not notes or end < beats * period:
            phrase, used = self._recall(self.memory[-1] if self.memory else [], chord_pcs,
                                        scale_pcs)
            if not phrase:
                break
            self._last_used = used
            part = self._phrase_notes(phrase, period, gain, next_beat is not None, chord_pcs,
                                      scale_pcs)
            if not part:
                break
            start = end + (SOLO_BREATH_BEATS * period if notes else 0.0)
            if notes and start + part[-1][0] > beats * period:
                break                                  # the next phrase wouldn't fit
            notes += [(start + off, n, v, d) for off, n, v, d in part]
            end = start + part[-1][0] + part[-1][3]
            if len(notes) > SOLO_MAX_NOTES:
                break
        self._schedule(notes, now, period, next_beat)
        return True

    def playing(self, now: float) -> bool:
        """An answer is sounding or still to come."""
        return bool(self._queue) or any(end > now for end in self._sounding.values())

    def cancel(self) -> None:
        """Stop the answer: drop what is still to come, silence what is sounding."""
        self._queue.clear()
        for note in list(self._sounding):
            self.out.note_off(self.cfg.channel - 1, note)
        self._sounding.clear()

    def reset(self) -> None:
        self.cancel()
        self.phrase, self.last_t, self.answered = [], None, False
