"""The voices: a slow-moving pad, a tempo-locked pulse (bass) and drums."""
from __future__ import annotations

import heapq
import itertools
import random
from typing import Optional

from .config import PadCfg, PulseCfg
from .harmony import Voicing
from .output import SafeOutput

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
        if self._ready is not None and (self.cfg.change_on == "beat" or bar_position == 0):
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
        new = Voicing(chord.root_pc, chord.third, self._choose(chord, force_new), chord.name)
        ch = self.cfg.channel - 1
        old_notes = set(self.current.notes) if self.current else set()
        new_notes = set(new.notes)
        for n in sorted(new_notes - old_notes):
            self.out.note_on(ch, n, self.cfg.velocity)
        for n in sorted(old_notes - new_notes):
            self.out.note_off_at(now + self.cfg.overlap_s, ch, n)
        self.current, self._pending, self._ready, self._last_change = new, None, None, now
        self._bars_since_change = 0

    def release_all(self) -> None:
        if self.current is not None:
            for n in self.current.notes:
                self.out.note_off(self.cfg.channel - 1, n)
        self.reset()

    def reset(self) -> None:
        self.current, self._pending, self._ready, self._last_change = None, None, None, float("-inf")
        self._bars_since_change = 0


class PulseResponder:
    """A soft note on every beat, accented once per bar."""

    def __init__(self, cfg: PulseCfg, out: SafeOutput) -> None:
        self.cfg, self.out = cfg, out
        self.beat_count = 0

    def on_beat(self, now: float, root_pc: int, gain: float = 1.0) -> None:
        pos = self.beat_count % max(1, self.cfg.beats_per_bar)
        vel = round(self.cfg.velocity * gain) + (self.cfg.accent if pos == 0 else 0)
        vel = min(max(vel, 1), 127)
        note = 12 * (self.cfg.octave + 1) + root_pc
        ch = self.cfg.channel - 1
        self.out.note_on(ch, note, vel)
        self.out.note_off_at(now + self.cfg.note_length_s, ch, note)
        self.beat_count += 1

    def reset(self) -> None:
        self.beat_count = 0


class DrumResponder:
    """Plays a drum pattern (patterns.py) on the beat clock.

    Each beat, the steps of that beat are scheduled at their times within it (with swing
    on every second step), then played by tick(). The cycle restarts when the beat clock
    starts. Changing drums.pattern live takes effect at the next beat.
    """

    def __init__(self, cfg, out: SafeOutput) -> None:
        self.cfg, self.out = cfg, out
        self.beat_count = 0
        self._queue: list[tuple[float, int, int, int]] = []   # (time, seq, note, velocity)
        self._seq = itertools.count()
        self._pattern = None

    def pattern(self):
        from .patterns import load

        if self._pattern is None or self._pattern.name != self.cfg.pattern:
            self._pattern = load(self.cfg.pattern)
        return self._pattern

    def on_beat(self, beat_t: float, period: float, gain: float = 1.0) -> None:
        pat = self.pattern()
        spb = pat.steps_per_beat
        first = (self.beat_count % pat.beats) * spb
        for step, note, level in pat.hits:
            k = step - first
            if 0 <= k < spb:
                offset = (k + (self.cfg.swing if k % 2 == 1 else 0.0)) * period / spb
                vel = self.cfg.velocity * gain
                if level == "accent":
                    vel += self.cfg.accent
                elif level == "ghost":
                    vel *= self.cfg.ghost
                heapq.heappush(self._queue, (beat_t + offset, next(self._seq), note,
                                             min(max(round(vel), 1), 127)))
        self.beat_count += 1

    def tick(self, now: float) -> None:
        ch = self.cfg.channel - 1
        while self._queue and self._queue[0][0] <= now:
            t, _, note, vel = heapq.heappop(self._queue)
            self.out.note_on(ch, note, vel)
            self.out.note_off_at(t + self.cfg.note_length_s, ch, note)

    def reset(self) -> None:
        self.beat_count = 0
        self._queue.clear()
