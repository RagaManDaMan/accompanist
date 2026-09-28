"""The engine: listen (tempo + harmony model), then respond (pad + pulse) with lag.

It is deliberately clock-agnostic: callers pass `now` in seconds, so the same
code runs live (time.monotonic) and in tests/simulation (virtual time).

Every component holds a reference to the one live Config, so a parameter changed
through the Controller applies at the next tick. Control (panic, tap tempo, ...)
comes in through controller.Controller, never directly from the CLI.
"""
from __future__ import annotations

from typing import Optional

from .beatclock import BeatClock
from .config import Config, note_name, NOTE_NAMES
from .harmony import Onset, Voicing, make_model
from .output import SafeOutput
from .responders import PadResponder, PulseResponder
from .tempo import TempoEstimator


class Engine:
    def __init__(self, cfg: Config, out: SafeOutput) -> None:
        self.cfg, self.out = cfg, out
        self.tempo = TempoEstimator(cfg.tempo)
        self.harmony = make_model(cfg)
        self.clock = BeatClock(cfg.pulse.phase_gain)
        self.pad = PadResponder(cfg.pad, out)
        self.pulse = PulseResponder(cfg.pulse, out)
        self.muted = False
        self.last_onset_t: Optional[float] = None
        self.last_note: Optional[int] = None
        self.proposal: Optional[Voicing] = None   # what the harmony model last suggested

    # ---- listening -------------------------------------------------------
    def on_note(self, t: float, note: int, velocity: int) -> None:
        """A note-on from a note_source input. Listening continues even while muted."""
        self.tempo.on_onset(t)
        self.harmony.observe(Onset(t, note, velocity))
        self.last_onset_t, self.last_note = t, note
        self.clock.hint(t, self.cfg.pulse.hint_window)

    # ---- responding ------------------------------------------------------
    def tick(self, now: float) -> None:
        self.out.flush(now)
        if self.tempo.update(now):
            self.clock.set_period(self.tempo.period)
        self.clock.phase_gain = self.cfg.pulse.phase_gain
        self.proposal = self.harmony.propose(now)
        if self.muted:
            return
        idle = float("inf") if self.last_onset_t is None else now - self.last_onset_t
        period = self.tempo.period

        if not self.cfg.pad.enabled:
            self.pad.release_all()
        elif idle > self.cfg.pad.idle_release_s:
            self.pad.release_all()
        elif self.proposal is not None:
            self.pad.update(now, self.proposal, period)

        if not self.cfg.pulse.enabled:
            self.clock.stop()
            return
        active = (
            self.tempo.confidence >= self.cfg.pulse.min_confidence
            and idle <= self.cfg.pulse.idle_stop_s
            and self.proposal is not None
        )
        if active and not self.clock.running and self.last_onset_t is not None:
            self.pulse.reset()
            self.clock.start(self.last_onset_t, period)
        elif not active and self.clock.running:
            self.clock.stop()
        if self.clock.running:
            # The pulse follows the harmony that is actually sounding, so pad and
            # pulse never disagree while the pad is still catching up.
            pulse_root = self.pad.current.root_pc if self.pad.current else self.proposal.root_pc
            for _ in self.clock.due(now):
                self.pulse.on_beat(now, pulse_root)

    # ---- control (called by the Controller) --------------------------------
    def panic(self) -> None:
        """Kill switch: silence now and stay silent until resume()."""
        self.muted = True
        self.out.panic()
        self.pad.reset()
        self.pulse.reset()
        self.clock.stop()

    def resume(self) -> None:
        self.muted = False

    def set_tempo(self, bpm: float, beat_t: Optional[float] = None) -> None:
        """Force the tempo (tap tempo). If beat_t is given, it was a beat: align the pulse to it."""
        self.tempo.set_bpm(bpm)
        self.clock.set_period(self.tempo.period)
        if beat_t is not None and self.clock.running:
            self.clock.next_beat = beat_t + self.tempo.period

    # ---- introspection ---------------------------------------------------
    def state(self, now: float) -> dict:
        idle = None if self.last_onset_t is None else now - self.last_onset_t
        root = self.pad.current.root_pc if self.pad.current else (
            self.proposal.root_pc if self.proposal else None)
        return {
            "time": now,
            "bpm": self.tempo.bpm,
            "confidence": self.tempo.confidence,
            "heard": None if self.last_note is None else note_name(self.last_note),
            "heard_ago_s": idle,
            "root": None if root is None else NOTE_NAMES[root],
            "pad": self.pad.current.label() if self.pad.current else None,
            "pad_notes": list(self.pad.current.notes) if self.pad.current else [],
            "pulse": self.clock.running,
            "muted": self.muted,
            "harmony_model": self.cfg.harmony.model,
        }
