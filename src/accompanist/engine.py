"""The engine: listen (tempo + harmony model), then respond (pad + pulse) with lag.

It is deliberately clock-agnostic: callers pass `now` in seconds, so the same
code runs live (time.monotonic) and in tests/simulation (virtual time).

Every component holds a reference to the one live Config, so a parameter changed
through the Controller applies at the next tick. Control (panic, tap tempo, ...)
comes in through controller.Controller, never directly from the CLI.
"""
from __future__ import annotations

import math
from typing import Optional

from .beatclock import BeatClock
from .dynamics import Dynamics
from .groove import Groove
from .config import Config, note_name, NOTE_NAMES
from .harmony import Onset, Voicing, make_model
from .output import SafeOutput
from .responders import DrumResponder, PadResponder, PulseResponder
from .response import ResponseResponder
from .tempo import REALIGN_ADVANTAGE, TempoEstimator

# Drum patterns for a meter heard, when yours does not fit it: (meter, feel) or meter.
GROOVE_PATTERNS = {3: "waltz", 4: "basic", (4, "swing"): "swing"}

# Pad expression: resend when it moves this many steps (of 127), at most this often.
EXPRESSION_STEP = 2
EXPRESSION_INTERVAL_S = 0.05


class Engine:
    def __init__(self, cfg: Config, out: SafeOutput) -> None:
        self.cfg, self.out = cfg, out
        self.tempo = TempoEstimator(cfg.tempo)
        self.harmony = make_model(cfg)
        self.clock = BeatClock(cfg.pulse.phase_gain)
        self.pad = PadResponder(cfg.pad, out, cfg.harmony.seed)
        self.pulse = PulseResponder(cfg.pulse, out)
        self.drums = DrumResponder(cfg.drums, out)
        self.response = ResponseResponder(cfg.response, out, cfg.harmony.seed)
        self.beat_count = 0                       # beats since the clock started (bar position)
        # A chart is a song: silent until started (count-in), then it plays until panic.
        self.is_chart = hasattr(self.harmony, "restart")
        self.song_playing = False
        self._count_in_left = 0
        self._low_conf_since: Optional[float] = None   # unlocked pulse: when confidence dropped
        if self.is_chart and getattr(self.harmony, "default_bpm", None):
            self.tempo.set_bpm(self.harmony.default_bpm)
        self.dynamics = Dynamics(cfg.dynamics)
        self.groove = Groove(cfg.groove)
        self._groove_t = float("-inf")
        self._expression_sent: Optional[tuple[int, int]] = None   # (cc, value) last sent to the pad
        self._expression_t = float("-inf")
        self.muted = False
        self.last_onset_t: Optional[float] = None
        self.last_note: Optional[int] = None
        self.proposal: Optional[Voicing] = None   # what the harmony model last suggested
        self.locked = False                       # tempo lock (the groove)
        self.chord_held = False                   # chord lock, separate from the tempo lock
        self.frozen: Optional[Voicing] = None     # the chord held while chord_held
        self._confident_since: Optional[float] = None
        self._auto_armed = True

    # ---- listening -------------------------------------------------------
    def on_note(self, t: float, note: int, velocity: int) -> None:
        """A note-on from a note_source input. Listening continues even while muted."""
        self.tempo.on_onset(t)
        self.harmony.observe(Onset(t, note, velocity))
        self.dynamics.observe(t, velocity)
        self.last_onset_t, self.last_note = t, note
        self.clock.hint(t, self.cfg.pulse.hint_window)
        self.response.hear(t, note, velocity, self.tempo.period)   # stops any answer at once
        if self.clock.running and not self.is_chart:           # where it fell against the beat
            x = (t - self.clock.next_beat) / self.clock.period
            k = math.floor(x)
            self.groove.observe(t, self.beat_count + k, x - k, velocity)

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
        voicing = self.frozen if self.chord_held else self.proposal
        waiting = self.is_chart and not self.song_playing   # before the chart starts: no band

        self._shape_pad(now)
        if not self.cfg.pad.enabled or waiting:
            self.pad.release_all()
        elif idle > self.cfg.pad.idle_release_s and not (self.locked or self.chord_held):
            self.pad.release_all()
        elif voicing is not None:
            self.pad.update(now, voicing, period, beat_known=self.clock.running)

        if not waiting:
            chord = self.pad.current or voicing
            self.response.tick(now, period, None if chord is None else {n % 12 for n in chord.notes},
                               self._scale_pcs(), self.dynamics.follow_gain(),
                               self.clock.next_beat if self.clock.running else None)

        # The beat clock runs while bass (pulse) or drums need it, or a chart is counting in.
        if not (self.cfg.pulse.enabled or self.cfg.drums.enabled or self.is_chart):
            self.clock.stop()
            self.drums.reset()
            return
        p = self.cfg.pulse
        if self.is_chart:
            active = self.song_playing or self._count_in_left > 0   # a song never stops by itself
        elif self.locked:
            active = voicing is not None                      # silence never stops a locked groove
        elif self.clock.running:                              # hysteresis: stop only well below start,
            if self.tempo.confidence >= p.stop_confidence:    # and only if it stays there
                self._low_conf_since = None
            elif self._low_conf_since is None:
                self._low_conf_since = now
            unsure = (self._low_conf_since is not None
                      and now - self._low_conf_since >= p.stop_after_s)
            active = not unsure and idle <= p.idle_stop_s
        else:
            active = self.tempo.confidence >= p.min_confidence and idle <= p.idle_stop_s
        if not self.is_chart:
            active = active and voicing is not None
        if active and not self.clock.running and self.last_onset_t is not None and not self.is_chart:
            self.restart_form()
            fit = self.tempo.beat_reference(now)
            self.clock.start(fit[0] if fit else self.last_onset_t, period, now)
        elif not active and self.clock.running:
            self.clock.stop()
            self.drums.reset()
        if self.clock.running:
            # The pulse follows the harmony that is actually sounding, so pad and
            # pulse never disagree while the pad is still catching up.
            pulse_root = (self.pad.current.root_pc if self.pad.current
                          else voicing.root_pc if voicing is not None else 0)
            gain = self.dynamics.follow_gain()
            if (self.cfg.groove.auto and not self.is_chart
                    and now - self._groove_t >= self.cfg.tempo.update_s):
                self._groove_t = now
                self.groove.update(now, self.clock.period)
                self._choose_drums()
            for beat_t in self.clock.due(now):
                bpb, bar_pos, form_beat, sure = self._bar(self.beat_count)
                if self._count_in_left > 0:                   # count-in click: 1, 2, 3, 4
                    self._click(beat_t, first=self._count_in_left == bpb)
                    self._count_in_left -= 1
                    if self._count_in_left == 0:
                        self.restart_form()                   # the next beat is bar 1
                        self.song_playing = True
                    continue
                if hasattr(self.harmony, "on_beat"):          # a chart: its chord for this beat

                    self.harmony.on_beat(beat_t)
                    self.proposal = self.harmony.propose(now)
                    chord = self.frozen if self.chord_held else self.proposal
                    if self.cfg.pad.enabled and chord is not None:
                        self.pad.update(now, chord, period, beat_known=True)
                    if chord is not None:
                        pulse_root = chord.root_pc
                boost = self.cfg.groove.downbeat_accent if (sure and bar_pos == 0) else 0
                if self.cfg.pad.enabled:
                    self.pad.on_beat(now, bar_pos)
                    if self.pad.current and not self.chord_held:
                        pulse_root = self.pad.current.root_pc
                if p.enabled:
                    self.pulse.on_beat(now, pulse_root, gain, bar_pos, boost)
                if self.cfg.drums.enabled:
                    swing = (self.groove.swing if self.cfg.groove.auto and self.cfg.groove.auto_drums
                             and self.groove.meter and not self.is_chart else None)
                    self.drums.on_beat(beat_t, self.clock.period, gain, form_beat, swing, boost)
                self.beat_count += 1
            self.drums.tick(now)

    def _bar(self, beat: int) -> tuple[int, int, int, bool]:
        """(beats per bar, position in the bar, beats since a downbeat, groove heard clearly)
        for beat number `beat`: from the chart, else the groove heard, else pulse.beats_per_bar."""
        g = self.groove
        if self.is_chart:
            bpb = max(1, self.harmony.beats_per_bar)
            return bpb, beat % bpb, beat, True
        if self.cfg.groove.auto and g.meter:
            form = beat - g.downbeat
            return g.meter, form % g.meter, form, g.confidence >= self.cfg.groove.confident_at
        bpb = max(1, self.cfg.pulse.beats_per_bar)
        return bpb, beat % bpb, beat, False

    def _choose_drums(self) -> None:
        """Keep your drum pattern if its cycle fits the meter heard; else one that does."""
        g = self.groove
        self.drums.pattern_name = None
        if not (self.cfg.groove.auto_drums and g.meter):
            return
        from .patterns import load

        if load(self.cfg.drums.pattern).beats % g.meter != 0:
            self.drums.pattern_name = GROOVE_PATTERNS.get((g.meter, g.feel), GROOVE_PATTERNS[g.meter])

    def _scale_pcs(self) -> Optional[set[int]]:
        """The key's scale, when the harmony model knows one (modal); else None."""
        key = getattr(self.harmony, "key", None)
        if not key:
            return None
        from .modal import SCALES

        tonic, mode = key
        return {(tonic + i) % 12 for i in SCALES[mode]}

    def _shape_pad(self, now: float) -> None:
        """Ride the pad's level on its expression controller: follow your loudness, step back
        while you're busy. Sent only when it moves by EXPRESSION_STEP, at most every
        EXPRESSION_INTERVAL_S."""
        cc = self.cfg.pad.expression_cc
        if cc is None or now - self._expression_t < EXPRESSION_INTERVAL_S:
            return
        value = round(127 * self.dynamics.pad_level(now))
        last = self._expression_sent
        if last is None or last[0] != cc or abs(last[1] - value) >= EXPRESSION_STEP:
            self.out.control_change(self.cfg.pad.channel - 1, cc, value)
            self._expression_sent, self._expression_t = (cc, value), now

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
        """Kill switch: silence now and stay silent until resume(). Also ends both locks, and
        stops a chart (start it again with a count-in)."""
        self.song_playing, self._count_in_left = False, 0
        self.unlock()
        self.release_chord()
        self.muted = True
        self.out.panic()
        self._expression_sent = None           # re-send the pad level after resume
        self.pad.reset()
        self.pulse.reset()
        self.drums.reset()
        self.response.reset()
        self.clock.stop()

    def resume(self) -> None:
        self.muted = False

    def lock(self, now: float) -> bool:
        """Lock the tempo: pad and pulse keep going through silence, the tempo follows only
        slowly and never jumps. Chords still follow you (see hold_chord). Needs something
        heard first. Returns True if locked.

        The beat is never moved: the lock is usually pressed on a computer key, whose timing
        has nothing to do with the music. Only the beat spacing settles on your notes."""
        if self.muted or (self.last_onset_t is None and not self.clock.running):
            return False
        self.locked, self._confident_since = True, None
        # Settle the tempo on the beat grid your recent notes fit best (a small, inaudible
        # change in beat spacing), but never move where the next beat falls.
        if len(self.tempo.onsets) >= self.cfg.tempo.min_onsets:
            period, _ = self.tempo.refine(now)
            self.tempo.set_bpm(60.0 / period)
            self.clock.set_period(self.tempo.period)
        return True

    def unlock(self) -> None:
        """Only a key, a controller or panic ends a lock; silence never does."""
        if self.locked:
            self._auto_armed = False
        self.locked = False
        self._low_conf_since = None           # unlocking never stops the band by itself

    def hold_chord(self) -> bool:
        """Hold the chord that is sounding (or about to): the pad and pulse stay on it,
        whatever you play, until release_chord() or panic. Returns True if held."""
        voicing = self.pad.current or self.proposal
        if self.muted or voicing is None:
            return False
        self.chord_held, self.frozen = True, voicing
        return True

    def release_chord(self) -> None:
        self.chord_held, self.frozen = False, None

    def restart_form(self) -> None:
        """The next beat is beat 1 of bar 1: for the bar count, the drums and a chart."""
        self.pulse.reset()
        self.drums.reset()
        self.groove.reset()                    # beat numbers start again
        self.beat_count = 0
        if hasattr(self.harmony, "restart"):
            self.harmony.restart()

    def count_in(self, last_tap: float) -> None:
        """After tapping the tempo: the taps were the count-in, the next beat is bar 1, and
        the tempo locks so the band keeps playing before you do."""
        if self.muted:
            return
        if self.clock.running:
            self.clock.next_beat = last_tap + self.tempo.period
        else:
            self.clock.start(last_tap, self.tempo.period)
        self._count_in_left = 0
        self.restart_form()
        self.song_playing = True
        self.lock(last_tap)

    def start_song(self, now: float) -> Optional[float]:
        """Chart: count in one bar of clicks, then play from bar 1, tempo locked (like pressing
        play in iReal Pro). The tempo: harmony.chart_bpm / --tempo, else the tempo you have
        been playing if it is clear, else a typical tempo for the chart's style.
        Returns the count-in tempo, or None if muted."""
        if self.muted:
            return None
        bpm = self.cfg.harmony.chart_bpm
        if bpm is None and self.tempo.confidence < self.cfg.pulse.min_confidence:
            bpm = getattr(self.harmony, "default_bpm", None)
        if bpm is not None:
            self.tempo.set_bpm(bpm)
        self.clock.start(now, self.tempo.period)     # first click one beat from now
        self.song_playing = False
        self.pad.release_all()
        self._count_in_left = max(1, getattr(self.harmony, "beats_per_bar", None)
                                  or self.cfg.pulse.beats_per_bar)
        self.lock(now)
        return self.tempo.bpm

    def _click(self, t: float, first: bool) -> None:
        d = self.cfg.drums
        vel = min(d.velocity + (d.accent if first else 0), 127)
        self.out.note_on(d.channel - 1, d.count_in_note, vel)
        self.out.note_off_at(t + d.note_length_s, d.channel - 1, d.count_in_note)

    def set_tempo(self, bpm: float, beat_t: Optional[float] = None) -> None:
        """Force the tempo (tap tempo). If beat_t is given, it was a beat: align the pulse to it
        (only for a count-in; otherwise key presses must not move the beat)."""
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
            "chord_held": self.chord_held,
            "your_velocity": self.dynamics.loudness(),
            "busy": self.dynamics.busyness(now),
            "pad_level": None if self._expression_sent is None else self._expression_sent[1] / 127,
            "lock_in_s": None if self.locked or self._confident_since is None else
                         max(0.0, self.cfg.lock.after_s - (now - self._confident_since)),
            "harmony_model": self.cfg.harmony.model,
            "key": getattr(self.harmony, "key_label", None),
            "groove": None if self.is_chart else self.groove.label(),
            "groove_confidence": self.groove.confidence,
            "chart": getattr(self.harmony, "position", None),
            "song": None if not self.is_chart else
                    "playing" if self.song_playing else
                    f"count-in {self._count_in_left}" if self._count_in_left else "waiting",
        }
