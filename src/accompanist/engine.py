"""The engine: listen (tempo + pitch memory), then respond (pad + pulse) with lag.

It is deliberately clock-agnostic: callers pass `now` in seconds, so the same
code runs live (time.monotonic) and in tests/simulation (virtual time).
"""
from __future__ import annotations

from typing import Optional

from .beatclock import BeatClock
from .config import Config, note_name, NOTE_NAMES
from .harmony import PitchClassTracker, choose_voicing
from .output import SafeOutput
from .responders import PadResponder, PulseResponder
from .tempo import TempoEstimator


class Engine:
    def __init__(self, cfg: Config, out: SafeOutput) -> None:
        self.cfg, self.out = cfg, out
        self.tempo = TempoEstimator(cfg.tempo)
        self.pitch = PitchClassTracker(cfg.harmony.half_life_s)
        self.clock = BeatClock(cfg.pulse.phase_gain)
        self.pad = PadResponder(cfg.pad, out)
        self.pulse = PulseResponder(cfg.pulse, out)
        self.muted = False
        self.last_onset_t: Optional[float] = None
        self.last_note: Optional[int] = None
        self._root_pc: Optional[int] = cfg.root_pc

    # ---- listening -------------------------------------------------------
    def on_note(self, t: float, note: int, velocity: int) -> None:
        """A note-on from a note_source input. Listening continues even while muted."""
        self.tempo.on_onset(t)
        self.pitch.add(t, note, velocity)
        self.last_onset_t, self.last_note = t, note
        self.clock.hint(t, self.cfg.pulse.hint_window)

    # ---- responding ------------------------------------------------------
    def tick(self, now: float) -> None:
        self.out.flush(now)
        if self.tempo.update(now):
            self.clock.set_period(self.tempo.period)
        if self.muted:
            return
        idle = float("inf") if self.last_onset_t is None else now - self.last_onset_t
        period = self.tempo.period
        hist = self.pitch.snapshot(now)

        if self.cfg.pad.enabled:
            if idle > self.cfg.pad.idle_release_s:
                self.pad.release_all()
            elif sum(hist) > 0:
                root = self._select_root(hist)
                voicing = choose_voicing(hist, root, self.cfg.pad.octave, self.cfg.harmony.third_threshold)
                self.pad.update(now, voicing, period)

        if self.cfg.pulse.enabled:
            active = (
                self.tempo.confidence >= self.cfg.pulse.min_confidence
                and idle <= self.cfg.pulse.idle_stop_s
                and self._root_pc is not None
            )
            if active and not self.clock.running and self.last_onset_t is not None:
                self.pulse.reset()
                self.clock.start(self.last_onset_t, period)
            elif not active and self.clock.running:
                self.clock.stop()
            if self.clock.running:
                # The pulse follows the harmony that is actually sounding, so pad and
                # pulse never disagree while the pad is still catching up.
                pulse_root = self.pad.current.root_pc if self.pad.current else self._root_pc
                for _ in self.clock.due(now):
                    self.pulse.on_beat(now, pulse_root)

    def _select_root(self, hist: list[float]) -> int:
        fixed = self.cfg.root_pc
        if fixed is not None:
            self._root_pc = fixed
            return fixed
        dominant = max(range(12), key=lambda pc: hist[pc])
        if self._root_pc is None or hist[dominant] > hist[self._root_pc] * self.cfg.harmony.switch_margin:
            self._root_pc = dominant
        return self._root_pc

    # ---- safety ----------------------------------------------------------
    def panic(self) -> None:
        """Kill switch: silence now and stay silent until resume()."""
        self.muted = True
        self.out.panic()
        self.pad.reset()
        self.pulse.reset()
        self.clock.stop()

    def resume(self) -> None:
        self.muted = False

    # ---- introspection ---------------------------------------------------
    def status(self, now: float) -> str:
        idle = None if self.last_onset_t is None else now - self.last_onset_t
        heard = "--" if self.last_note is None else f"{note_name(self.last_note)} ({idle:0.1f}s ago)"
        chord = self.pad.current.label() if self.pad.current else "--"
        root = "--" if self._root_pc is None else NOTE_NAMES[self._root_pc]
        pulse = "on " if self.clock.running else "off"
        flag = "  ** MUTED **" if self.muted else ""
        return (f"{self.tempo.bpm:5.1f} bpm  conf {self.tempo.confidence:4.0%}  "
                f"heard {heard:<16} root {root:<2}  pad {chord:<4} pulse {pulse}{flag}")
