"""The voices: a slow-moving pad, a tempo-locked pulse (bass) and drums."""
from __future__ import annotations

import heapq
import itertools
import random
from typing import Optional

from .config import PadCfg, PulseCfg
from .harmony import Voicing
from .output import SafeOutput

def humanize_velocity(velocity: int, spread: int, rng: random.Random) -> int:
    """velocity, up to `spread` softer or louder (0 = unchanged)."""
    if spread <= 0:
        return velocity
    return min(max(velocity + rng.randint(-spread, spread), 1), 127)


# Pad voicing range (MIDI notes) and how many semitones of voice movement variation=1 may
# trade for novelty.
VOICE_LOW, VOICE_HIGH = 36, 88
VARIATION_SEMITONES = 6.0


class PadResponder:
    """Sustained harmony that follows what you've been playing, with deliberate lag.

    A new chord must stay the leading candidate for `lag_beats` before we move, and we
    never move more often than `min_change_beats`. When the beat is known, the change
    lands on the next beat or bar (`change_on`). Common tones are held, not retriggered,
    so changes feel like the harmony shifting, not restarting.

    The pad also chooses its own voicing of each chord: root in the bass, the other
    chord tones above in some inversion and spacing. It prefers smooth voice-leading from
    what is sounding, with `variation` adding randomness, and re-voices a chord that has
    stood still for `revoice_bars`, so a static harmony still moves.
    """

    def __init__(self, cfg: PadCfg, out: SafeOutput, seed: int = 0) -> None:
        self.cfg, self.out = cfg, out
        self.rng = random.Random(seed)
        self.current: Optional[Voicing] = None
        self._pending: Optional[tuple[Voicing, float]] = None
        self._ready: Optional[Voicing] = None       # passed its lag: waiting for the beat/bar
        self._last_change = float("-inf")
        self._bars_since_change = 0

    def update(self, now: float, candidate: Voicing, period_s: float, beat_known: bool = False) -> None:
        if self.current is not None and self._same_chord(candidate, self.current):
            self._pending = self._ready = None
            return
        if candidate.scheduled:                 # a chart change: it is due now, on this beat
            self._pending = None
            if beat_known and self.cfg.change_on != "now":
                self._ready = candidate
            else:
                self._apply(now, candidate)
            return
        # The lag clock is keyed on the ROOT: a wobbling third (major/minor/open) must not
        # restart it. The latest voicing wins when the lag expires.
        if self._pending is None or self._pending[0].root_pc != candidate.root_pc:
            self._pending = (candidate, now)
            return
        self._pending = (candidate, self._pending[1])
        waited = now - self._pending[1]
        if waited >= self.cfg.lag_beats * period_s and now - self._last_change >= self.cfg.min_change_beats * period_s:
            if beat_known and self.cfg.change_on != "now":
                self._ready = candidate
            else:
                self._apply(now, candidate)

    def on_beat(self, now: float, bar_position: int) -> None:
        """The beat clock ticked: apply a waiting change, or re-voice a static chord."""
        if bar_position == 0:
            self._bars_since_change += 1
        due = self.cfg.change_on == "beat" or bar_position == 0 or (
            self._ready is not None and self._ready.scheduled)
        if self._ready is not None and due:
            self._apply(now, self._ready)
        elif (bar_position == 0 and self.current is not None and self.cfg.revoice_bars > 0
              and self._bars_since_change >= self.cfg.revoice_bars):
            self._apply(now, self.current, force_new=True)

    @staticmethod
    def _same_chord(a: Voicing, b: Voicing) -> bool:
        return (a.root_pc, a.label(), {n % 12 for n in a.notes}, min(a.notes) // 12) == \
               (b.root_pc, b.label(), {n % 12 for n in b.notes}, min(b.notes) // 12)

    def _voicings(self, chord: Voicing) -> list[tuple[int, ...]]:
        """Ways to voice the chord: root in the bass at pad.octave, the other tones above it
        in each inversion, close or with one voice dropped an octave (open)."""
        bass = 12 * (self.cfg.octave + 1) + chord.root_pc
        pcs = sorted({n % 12 for n in chord.notes})
        options = []
        for start in range(bass + 7, bass + 19):          # where the upper structure begins
            upper = sorted(start + (pc - start) % 12 for pc in pcs)
            options.append(tuple([bass] + upper))
            if len(upper) >= 3:                           # open: second voice up an octave
                opened = sorted(upper[:1] + [upper[1] + 12] + upper[2:])
                options.append(tuple([bass] + opened))
        return [o for o in dict.fromkeys(options) if all(VOICE_LOW <= n <= VOICE_HIGH for n in o)]

    def _choose(self, chord: Voicing, force_new: bool) -> tuple[int, ...]:
        options = self._voicings(chord) or [chord.notes]
        prev = self.current.notes if self.current else chord.notes
        if force_new and len(options) > 1:
            options = [o for o in options if o != prev] or options

        def cost(notes: tuple[int, ...]) -> float:
            move = sum(min(abs(n - p) for p in prev) for n in notes) / len(notes)
            return move + self.cfg.variation * VARIATION_SEMITONES * self.rng.random()

        return min(options, key=cost)

    def _apply(self, now: float, chord: Voicing, force_new: bool = False) -> None:
        new = Voicing(chord.root_pc, chord.third, self._choose(chord, force_new), chord.name,
                      chord.scheduled)
        ch = self.cfg.channel - 1
        old_notes = set(self.current.notes) if self.current else set()
        new_notes = set(new.notes)
        for n in sorted(new_notes - old_notes):
            vel = humanize_velocity(self.cfg.velocity, self.cfg.velocity_spread, self.rng)
            if self.cfg.strum_ms > 0:                # humanize: a slight strum, low to high
                self.out.note_on_at(now + self.rng.uniform(0, self.cfg.strum_ms) / 1000, ch, n, vel)
            else:
                self.out.note_on(ch, n, vel)
        for n in sorted(old_notes - new_notes):
            self.out.note_off_at(now + self.cfg.overlap_s, ch, n)
        if not force_new:                  # a re-voicing is not a change of harmony: it must
            self._last_change = now        # not hold back the next real change (min_change_beats)
        self.current, self._pending, self._ready = new, None, None
        self._bars_since_change = 0

    def release_all(self) -> None:
        self.out.cancel_ons(self.cfg.channel - 1)     # a strummed note not yet started
        if self.current is not None:
            for n in self.current.notes:
                self.out.note_off(self.cfg.channel - 1, n)
        self.reset()

    def reset(self) -> None:
        self.current, self._pending, self._ready, self._last_change = None, None, None, float("-inf")
        self._bars_since_change = 0


# Bass shapes, one character per beat: R root, 8 the root an octave up, 2 3 4 5 6 7 the
# chord's or scale's second, third, fourth, fifth, sixth and seventh (6 and 7 just under the
# octave, walking down), A a step into the next bar's root, e two eighths (the fifth, then
# a step into the next beat's note). A shape for a meter not listed is made from its groups
# (see _generic_shape).
BASS_SHAPES = {
    2: ("R5", "R8", "RA", "Re", "85", "R3"),
    3: ("R55", "R58", "R35", "R5A", "R85", "R3A", "R23", "R5e", "876", "R45", "853", "8e5",
        "R3e"),
    4: ("R5R5", "R585", "R35A", "R5RA", "RR5A", "R853", "R358", "R587", "R3R5", "R235",
        "8765", "R345", "R5Re", "R35e", "853A", "R45A", "R8e5", "R2e5"),
    6: ("R55R55", "R58R5A", "R35R3A", "R5eR5A", "R23R5A", "876R5A"),
}
# A plain shape (the root on every beat) comes up with chance (1 - movement) ** this.
PLAIN_POWER = 2
# Eighth notes in a shape: their length (share of an eighth, at most note_length_s) and the
# second one's velocity (share of the first).
EIGHTH_LENGTH = 0.9
EIGHTH_SOFTER = 0.8


class PulseResponder:
    """The bass: a note on every beat, accented once per bar.

    With pulse.movement above 0 it plays shapes: the root on the 1 (and whenever the chord
    changes), then fifths, thirds, octaves and a step into the next bar, one shape repeated
    for pulse.shape_bars bars. At 0 it plays the root on every beat.
    """

    def __init__(self, cfg: PulseCfg, out: SafeOutput, seed: int = 0) -> None:
        self.cfg, self.out = cfg, out
        self.rng = random.Random(seed + 1)
        self.shape_rng = random.Random(seed + 11)
        self.beat_count = 0
        self._shape = ""
        self._shape_left = 0
        self._last_root: Optional[int] = None
        self._last_interval = 0

    def on_beat(self, now: float, root_pc: int, gain: float = 1.0,
                bar_position: Optional[int] = None, boost: int = 0, group_start: bool = False,
                chord: Optional[Voicing] = None, beats_per_bar: Optional[int] = None,
                scale: Optional[set[int]] = None, period: Optional[float] = None) -> None:
        """group_start: a beat that starts a group within the bar (the 4 of 3+2): half an accent.
        chord, beats_per_bar, scale: what the bass shapes are made of (the root alone without);
        period: the beat's length, for shapes with eighth notes."""
        bpb = max(1, beats_per_bar or self.cfg.beats_per_bar)
        pos = self.beat_count % bpb if bar_position is None else bar_position
        accent = self.cfg.accent if pos == 0 else self.cfg.accent // 2 if group_start else 0
        vel = round(self.cfg.velocity * gain) + accent + boost
        vel = humanize_velocity(min(max(vel, 1), 127), self.cfg.velocity_spread, self.rng)
        root = 12 * (self.cfg.octave + 1) + root_pc
        note = self._shape_note(root, root_pc, pos, bpb, chord, scale)
        ch = self.cfg.channel - 1
        eighths = period is not None and pos < len(self._shape) and self._shape[pos] == "e" \
            and note != root
        length = min(self.cfg.note_length_s, EIGHTH_LENGTH * period / 2) if eighths \
            else self.cfg.note_length_s
        lay_back = self.rng.uniform(0, self.cfg.timing_ms) / 1000 if self.cfg.timing_ms > 0 else 0.0
        if lay_back > 0:                             # humanize: laid back, a little each time
            self.out.note_on_at(now + lay_back, ch, note, vel, off_at=now + lay_back + length)
        else:
            self.out.note_on(ch, note, vel)
            self.out.note_off_at(now + length, ch, note)
        if eighths:                                  # the "and": a step into the next note
            nxt = self._shape[pos + 1] if pos + 1 < len(self._shape) else "R"
            target = root + (0 if nxt == "R" else self._interval(nxt, root_pc, chord, scale))
            passing = self._step_to(target, note, scale)
            t = now + period / 2 + lay_back
            self.out.note_on_at(t, ch, passing, max(1, round(vel * EIGHTH_SOFTER)),
                                off_at=t + length)
        self.beat_count += 1

    def _step_to(self, target: int, current: int, scale: Optional[set[int]]) -> int:
        """A note a step from target, on the side `current` comes from (in the scale if known)."""
        direction = 1 if current > target else -1
        for step in (1, 2) if scale is None else (2, 1):
            if scale is None or (target + direction * step) % 12 in scale:
                return target + direction * step
        return target + direction * 2

    def _shape_note(self, root: int, root_pc: int, pos: int, bpb: int,
                    chord: Optional[Voicing], scale: Optional[set[int]]) -> int:
        changed = root_pc != self._last_root
        self._last_root = root_pc
        if self.cfg.movement <= 0:
            self._last_interval = 0
            return root
        if len(self._shape) != bpb or (pos == 0 and self._shape_left <= 0):
            self._shape = self._pick_shape(bpb)
            self._shape_left = self.cfg.shape_bars
        if pos == 0:
            self._shape_left -= 1
        if changed or pos >= len(self._shape):
            self._last_interval = 0
            return root                              # a new chord: land on its root
        degree = self._shape[pos]
        if degree == "8" and self._last_interval < 0:
            degree = "R"                             # stepped up from below: land, don't leap
        self._last_interval = self._interval(degree, root_pc, chord, scale)
        return root + self._last_interval

    def _pick_shape(self, bpb: int) -> str:
        """A new shape, never the one just played (so a long chord doesn't loop one riff)."""
        if self.shape_rng.random() < (1 - self.cfg.movement) ** PLAIN_POWER:
            return "R" * bpb
        shapes = BASS_SHAPES.get(bpb) or (_generic_shape(bpb, self.shape_rng),)
        fresh = [x for x in shapes if x != self._shape] or list(shapes)
        return self.shape_rng.choice(fresh)

    def _interval(self, degree: str, root_pc: int, chord: Optional[Voicing],
                  scale: Optional[set[int]]) -> int:
        pcs = {n % 12 for n in chord.notes} if chord else set()
        fifth = 7 if not pcs or (root_pc + 7) % 12 in pcs else 6 if (root_pc + 6) % 12 in pcs else 7
        if degree in "5e":
            return fifth
        if degree == "8":
            return 12
        if degree in "246":                          # from the scale (or the chord), else plain
            options = {"2": (2, 1), "4": (5, 6), "6": (9, 8)}[degree]
            for i in options:
                if (root_pc + i) % 12 in (pcs | (scale or set())):
                    return i
            return options[0]
        if degree == "3":
            third = chord.third if chord and chord.third else None
            return third if third else fifth         # an open chord: no third to play
        if degree == "7":
            for i in (10, 11):
                if (root_pc + i) % 12 in (pcs | (scale or set())):
                    return i                         # just under the octave: a walk down
            return fifth
        if degree == "A":                            # a step into the next bar's root
            below = [i for i in (-2, -1) if scale is None or (root_pc + i) % 12 in scale]
            above = [i for i in (2, 1) if scale is None or (root_pc + i) % 12 in scale]
            if self._last_interval >= 5 and above:   # coming down from up high: from above
                return above[0]
            steps = above[:1] + below[:1]
            return self.shape_rng.choice(steps) if steps else fifth
        return 0

    def reset(self) -> None:
        self.beat_count = 0
        self._shape, self._shape_left, self._last_root = "", 0, None


def _generic_shape(bpb: int, rng: random.Random) -> str:
    """A shape for any meter: the root at the start of each group, fifths and octaves between,
    and a step into the next bar on the last beat."""
    from .groove import GROUPS

    starts = GROUPS.get(bpb, (0,))
    cells = ["R" if i in starts else rng.choice("58") for i in range(bpb)]
    if bpb > 1:
        cells[-1] = "A"
    return "".join(cells)


# Drum dynamics (drums.dynamics = d, 0-1): how much harder the drums follow your loudness
# (gain ** (1 + FOLLOW_BOOST*d)); how far they lift when you play busily and drop back when
# you rest; how much softer the off-beat steps are; the crescendo over a phrase; where a fill
# starts (share of the hit); and the chance of a fill at a phrase end (times d).
FOLLOW_BOOST = 1.5
BUSY_LIFT = 0.4
OFFBEAT_SOFT = 0.3
PHRASE_CRESCENDO = 0.2
FILL_FROM = 0.55
FILL_CHANCE = 1.2
FILL_NOTES = {"low": "tom_low", "mid": "tom_mid", "snare": "snare"}


class DrumResponder:
    """Plays a drum pattern (patterns.py) on the beat clock.

    Each beat, the steps of that beat are scheduled at their times within it (with swing
    on every second step), then played by tick(). The cycle restarts when the beat clock
    starts. Changing drums.pattern live takes effect at the next beat.

    drums.dynamics shapes the loudness: following you, lifting when you're busy, softer
    off-beats, and phrases of drums.phrase_bars bars that build, may end in a fill, and
    start with a crash.
    """

    def __init__(self, cfg, out: SafeOutput, seed: int = 0) -> None:
        self.cfg, self.out = cfg, out
        self.rng = random.Random(seed + 2)
        self.fill_rng = random.Random(seed + 12)
        self.beat_count = 0
        self._queue: list[tuple[float, int, int, int]] = []   # (time, seq, note, velocity)
        self._seq = itertools.count()
        self._pattern = None
        self._crash_next = False
        self.pattern_name: Optional[str] = None     # chosen by the groove, over drums.pattern

    def pattern(self):
        from .patterns import load

        name = self.pattern_name or self.cfg.pattern
        if self._pattern is None or self._pattern.name != name:
            self._pattern = load(name)
        return self._pattern

    def on_beat(self, beat_t: float, period: float, gain: float = 1.0,
                form_beat: Optional[int] = None, swing: Optional[float] = None,
                boost: int = 0, beats_per_bar: Optional[int] = None, busy: float = 0.5) -> None:
        """Schedule this beat's steps. form_beat: beats since a downbeat (so the cycle lines
        up with the bar); swing: overrides drums.swing; boost: extra velocity on this beat;
        beats_per_bar, busy (0-1, how busily you play): for the dynamics."""
        from .patterns import GM_DRUMS

        pat = self.pattern()
        spb = pat.steps_per_beat
        where = self.beat_count if form_beat is None else form_beat
        first = (where % pat.beats) * spb
        swing = self.cfg.swing if swing is None else swing
        d = self.cfg.dynamics
        energy, fill = gain, False
        if d > 0:
            energy = gain ** (1 + FOLLOW_BOOST * d) * (1 + BUSY_LIFT * d * (busy - 0.5))
            bpb = max(1, beats_per_bar or pat.beats)
            phrase, bar_pos = self.cfg.phrase_bars, where % bpb
            bar_in_phrase = (where // bpb) % phrase if phrase > 0 else 0
            if phrase > 1:
                energy *= 1 + PHRASE_CRESCENDO * d * bar_in_phrase / (phrase - 1)
                if bar_in_phrase == phrase - 1 and bar_pos == bpb - 1:
                    fill = self.fill_rng.random() < FILL_CHANCE * d
                    self._crash_next = self._crash_next or fill
            if bar_pos == 0 and self._crash_next:
                self._crash_next = False
                self._push(beat_t, GM_DRUMS["crash"], self.cfg.velocity * energy + self.cfg.accent + boost)
        for step, note, level in pat.hits:
            k = step - first
            if 0 <= k < spb:
                if fill and note != GM_DRUMS["kick"]:
                    continue                         # the fill replaces all but the kick
                offset = (k + (swing if k % 2 == 1 else 0.0)) * period / spb
                if self.cfg.timing_ms > 0:           # humanize: a little early or late
                    offset += self.rng.uniform(-self.cfg.timing_ms, self.cfg.timing_ms) / 1000
                    offset = max(offset, 0.0) if k == 0 else offset
                vel = self.cfg.velocity * energy + (boost if k == 0 else 0)
                if level == "accent":
                    vel += self.cfg.accent
                elif level == "ghost":
                    vel *= self.cfg.ghost
                if k > 0:
                    vel *= 1 - OFFBEAT_SOFT * d
                self._push(beat_t + offset, note, vel)
        if fill:
            self._fill(beat_t, period, spb, self.cfg.velocity * energy + self.cfg.accent)
        self.beat_count += 1

    def _fill(self, beat_t: float, period: float, spb: int, top: float) -> None:
        """A short fill across the beat, rising to `top`: snare, or down the toms."""
        from .patterns import GM_DRUMS

        n = spb if spb >= 3 else 4                   # triplets in a triplet feel, else 16ths
        voices = self.fill_rng.choice((("snare",) * n,
                                       tuple(("snare", "mid", "low")[min(i * 3 // n, 2)] for i in range(n))))
        for i, v in enumerate(voices):
            vel = top * (FILL_FROM + (1 - FILL_FROM) * i / max(1, n - 1))
            self._push(beat_t + i * period / n, GM_DRUMS[FILL_NOTES[v]], vel)

    def _push(self, t: float, note: int, vel: float) -> None:
        vel = humanize_velocity(min(max(round(vel), 1), 127), self.cfg.velocity_spread, self.rng)
        heapq.heappush(self._queue, (t, next(self._seq), note, vel))

    def tick(self, now: float) -> None:
        ch = self.cfg.channel - 1
        while self._queue and self._queue[0][0] <= now:
            t, _, note, vel = heapq.heappop(self._queue)
            self.out.note_on(ch, note, vel)
            self.out.note_off_at(t + self.cfg.note_length_s, ch, note)

    def reset(self) -> None:
        self.beat_count = 0
        self._crash_next = False
        self._queue.clear()
