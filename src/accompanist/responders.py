"""The two V0 voices: a slow-moving pad and a tempo-locked pulse."""
from __future__ import annotations

from typing import Optional

from .config import PadCfg, PulseCfg
from .harmony import Voicing
from .output import SafeOutput


class PadResponder:
    """Sustained harmony that follows what you've been playing, with deliberate lag.

    A new voicing must stay the leading candidate for `lag_beats` before we move,
    and we never move more often than `min_change_beats`. Common tones are held,
    not retriggered, so changes feel like the harmony shifting, not restarting.
    """

    def __init__(self, cfg: PadCfg, out: SafeOutput) -> None:
        self.cfg, self.out = cfg, out
        self.current: Optional[Voicing] = None
        self._pending: Optional[tuple[Voicing, float]] = None
        self._last_change = float("-inf")

    def update(self, now: float, candidate: Voicing, period_s: float) -> None:
        if self.current is not None and candidate.key == self.current.key:
            self._pending = None
            return
        # The lag clock is keyed on the ROOT: a wobbling third (major/minor/open) must not
        # restart it. The latest voicing wins when the lag expires.
        if self._pending is None or self._pending[0].root_pc != candidate.root_pc:
            self._pending = (candidate, now)
            return
        self._pending = (candidate, self._pending[1])
        waited = now - self._pending[1]
        if waited >= self.cfg.lag_beats * period_s and now - self._last_change >= self.cfg.min_change_beats * period_s:
            self._apply(now, candidate)

    def _apply(self, now: float, new: Voicing) -> None:
        ch = self.cfg.channel - 1
        old_notes = set(self.current.notes) if self.current else set()
        new_notes = set(new.notes)
        for n in sorted(new_notes - old_notes):
            self.out.note_on(ch, n, self.cfg.velocity)
        for n in sorted(old_notes - new_notes):
            self.out.note_off_at(now + self.cfg.overlap_s, ch, n)
        self.current, self._pending, self._last_change = new, None, now

    def release_all(self) -> None:
        if self.current is not None:
            for n in self.current.notes:
                self.out.note_off(self.cfg.channel - 1, n)
        self.reset()

    def reset(self) -> None:
        self.current, self._pending, self._last_change = None, None, float("-inf")


class PulseResponder:
    """A soft note on every beat, accented once per bar."""

    def __init__(self, cfg: PulseCfg, out: SafeOutput) -> None:
        self.cfg, self.out = cfg, out
        self.beat_count = 0

    def on_beat(self, now: float, root_pc: int) -> None:
        pos = self.beat_count % max(1, self.cfg.beats_per_bar)
        vel = self.cfg.velocity + (self.cfg.accent if pos == 0 else 0)
        vel = min(max(vel, 1), 127)
        note = 12 * (self.cfg.octave + 1) + root_pc
        ch = self.cfg.channel - 1
        self.out.note_on(ch, note, vel)
        self.out.note_off_at(now + self.cfg.note_length_s, ch, note)
        self.beat_count += 1

    def reset(self) -> None:
        self.beat_count = 0
