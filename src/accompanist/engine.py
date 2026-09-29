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
from .tempo import REALIGN_ADVANTAGE, TempoEstimator


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
        self.locked = False
        self.frozen: Optional[Voicing] = None     # the harmony held while locked
        self._confident_since: Optional[float] = None
        self._auto_armed = True

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
        # While locked the groove is trusted: the tempo follows only slowly (fitting the beat
        # grid rather than re-searching all tempi) and never jumps, and the pulse is never
        # realigned wholesale. Phase hints (small nudges toward your on-beat notes) continue,
        # scaled by lock.phase_rate, so the pulse stays with you without chasing every note.
        rate = self.cfg.lock.tempo_rate if self.locked else 1.0
        if self.tempo.update(now, rate=rate, locked=self.locked):
            self.clock.set_period(self.tempo.period)
            if self.clock.running:
                self._realign(now)
        self.clock.phase_gain = self.cfg.pulse.phase_gain * (self.cfg.lock.phase_rate if self.locked else 1.0)
        self.proposal = self.harmony.propose(now)
        self._auto_lock(now)
        if self.muted:
            return
        idle = float("inf") if self.last_onset_t is None else now - self.last_onset_t
        period = self.tempo.period
        voicing = self.frozen if self.locked else self.proposal

        if not self.cfg.pad.enabled:
            self.pad.release_all()
        elif idle > self.cfg.pad.idle_release_s and not self.locked:
            self.pad.release_all()
        elif voicing is not None:
            self.pad.update(now, voicing, period)

        if not self.cfg.pulse.enabled:
            self.clock.stop()
            return
        p = self.cfg.pulse
        if self.locked:
            active = voicing is not None                      # silence never stops a locked groove
        elif self.clock.running:                              # hysteresis: stop only well below start
            active = self.tempo.confidence >= p.stop_confidence and idle <= p.idle_stop_s
        else:
            active = self.tempo.confidence >= p.min_confidence and idle <= p.idle_stop_s
        active = active and voicing is not None
        if active and not self.clock.running and self.last_onset_t is not None:
            self.pulse.reset()
            fit = self.tempo.beat_reference(now)
            self.clock.start(fit[0] if fit else self.last_onset_t, period, now)
        elif not active and self.clock.running:
            self.clock.stop()
        if self.clock.running:
            # The pulse follows the harmony that is actually sounding, so pad and
            # pulse never disagree while the pad is still catching up.
            pulse_root = self.pad.current.root_pc if self.pad.current else voicing.root_pc
            for _ in self.clock.due(now):
                self.pulse.on_beat(now, pulse_root)

    def _realign(self, now: float) -> None:
        """Compare the pulse with the beat your last few notes imply. Clearly off (e.g. it
        started on an off-beat) and clearly better elsewhere: move it there, unless locked.
        Close: nudge it."""
        fit = self.tempo.beat_reference(now, self.clock.period, current=self.clock.next_beat)
        if fit is None:
            return
        ref, advantage = fit
        err = self.clock.phase_error(ref)
        far = abs(err) > self.cfg.pulse.hint_window * self.clock.period
        if far and not self.locked and advantage >= REALIGN_ADVANTAGE:
            self.clock.align(ref)
        elif not far:
            self.clock.correct(-err)

    def _auto_lock(self, now: float) -> None:
        lk = self.cfg.lock
        confident = self.tempo.confidence >= lk.confidence
        if not confident:
            self._auto_armed = True               # after an unlock, re-arm once confidence dips
        if self.locked or self.muted or not lk.auto or not confident or not self._auto_armed:
            self._confident_since = None
            return
        if self._confident_since is None:
            self._confident_since = now
        elif now - self._confident_since >= lk.after_s:
            self.lock(now)

    # ---- control (called by the Controller) --------------------------------
    def panic(self) -> None:
        """Kill switch: silence now and stay silent until resume(). Also ends a lock."""
        self.unlock()
        self.muted = True
        self.out.panic()
        self.pad.reset()
        self.pulse.reset()
        self.clock.stop()

    def resume(self) -> None:
        self.muted = False

    def lock(self, now: float) -> bool:
        """Hold the groove: freeze the harmony, keep pad and pulse going through silence,
        follow tempo only slowly. Needs something heard first. Returns True if locked."""
        if self.muted or self.last_onset_t is None:
            return False
        voicing = self.pad.current or self.proposal
        if voicing is None:
            return False
        self.locked, self.frozen, self._confident_since = True, voicing, None
        # Lock onto the best beat we can hear (tempo, then phase), then hold it.
        if len(self.tempo.onsets) >= self.cfg.tempo.min_onsets:
            period, beat = self.tempo.refine(now)
            self.tempo.set_bpm(60.0 / period)
            self.clock.set_period(self.tempo.period)
            if self.clock.running:
                self.clock.align(beat)
        return True

    def unlock(self) -> None:
        """Only a key, a controller or panic ends a lock; silence never does."""
        if self.locked:
            self._auto_armed = False
        self.locked, self.frozen = False, None

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
            "locked": self.locked,
            "lock_in_s": None if self.locked or self._confident_since is None else
                         max(0.0, self.cfg.lock.after_s - (now - self._confident_since)),
            "harmony_model": self.cfg.harmony.model,
            "key": getattr(self.harmony, "key_label", None),
        }
