"""The engine: listen (tempo + harmony model), then respond (pad + pulse) with lag.

It is deliberately clock-agnostic: callers pass `now` in seconds, so the same
code runs live (time.monotonic) and in tests/simulation (virtual time).

Every component holds a reference to the one live Config, so a parameter changed
through the Controller applies at the next tick. Control (panic, tap tempo, ...)
comes in through controller.Controller, never directly from the CLI.
"""
from __future__ import annotations

import math
import random
from typing import Optional

from .beatclock import BeatClock
from .dynamics import Dynamics
from .groove import GROUPS, Groove
from .config import Config, note_name, NOTE_NAMES
from .harmony import Onset, Voicing, make_model
from .output import SafeOutput
from .piano import PianoResponder
from .responders import DrumResponder, PadResponder, PulseResponder
from .response import ResponseResponder
from .tempo import REALIGN_ADVANTAGE, TempoEstimator

# Drum patterns for a meter heard, when yours does not fit it: (meter, feel) or meter.
GROOVE_PATTERNS = {3: "waltz", 4: "basic", (4, "swing"): "swing", 5: "five", 6: "six-eight",
                   7: "seven-322"}
# Heartbeat before the music: how long after the last beat it still counts, how the pad
# sinks between beats (time constant) and the share of the swell it keeps.
HEART_HOLD_S = 3.0
HEART_DECAY_S = 0.4
# The filler between songs: scale degrees (0 = the home chord) its chords sit on, in turn.
FILLER_DEGREES = (0, 5, 3, 4, 0, 3, 5, 1)
# Ending shapes; a piano tag's notes (beats of the bar) and softness; a ritardando's last
# chord rings this much longer (a fermata).
ENDING_SHAPES = ("chord", "button", "tag", "ritardando", "piano-tag")
PIANO_TAG_BEATS = {3: (0, 1), 4: (0, 2), 6: (0, 3)}
PIANO_TAG_SOFT = 0.6
FERMATA = 1.5
# How much louder the percussion gets in the quiet at percussion.spotlight = 1.
SPOTLIGHT_LIFT = 0.8
# Percussion triplet figures: notes per beat (3 over 2 beats; 6 over 2 when doubled), and the
# share of percussion.triplet_build_s of soloing past which they double.
TRIPLET_SINGLE = 1.5
TRIPLET_DOUBLE = 3.0
TRIPLET_DOUBLE_AT = 0.7
PERCUSSION_PATTERNS = {3: "latin-waltz", 4: "latin", 5: "latin-five", 6: "bembe", 7: "latin-seven"}

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
        self.pulse = PulseResponder(cfg.pulse, out, cfg.harmony.seed)
        self.drums = DrumResponder(cfg.drums, out, cfg.harmony.seed)
        self.percussion = DrumResponder(cfg.percussion, out, cfg.harmony.seed + 7)
        self._percussion_on = False               # in a spell when the percussion plays
        self._spell_rng = random.Random(cfg.harmony.seed + 31)
        self.response = ResponseResponder(cfg.response, out, cfg.harmony.seed)
        self.piano = PianoResponder(cfg.piano, out, cfg.harmony.seed)
        self.response.partner = self.piano          # they take turns answering you
        if cfg.drums.style:                           # real drummers' grooves
            from .drumbook import DrumBook

            self.drums.book = DrumBook.open(cfg.library.path)
        if cfg.library.enabled:                       # your phrases, from every run
            from .phrasebook import Phrasebook

            self.response.book = Phrasebook.open(cfg.library.path)
        self.beat_count = 0                       # beats since the clock started (bar position)
        # A chart is a song: silent until started (count-in), then it plays until panic.
        self.is_chart = hasattr(self.harmony, "restart")
        self.song_playing = False
        self._count_in_left = 0
        self._count_total = 0
        self._count_meter: Optional[int] = None
        self._low_conf_since: Optional[float] = None   # unlocked pulse: when confidence dropped
        if self.is_chart and getattr(self.harmony, "default_bpm", None):
            self.tempo.set_bpm(self.harmony.default_bpm)
        self.dynamics = Dynamics(cfg.dynamics)
        self.groove = Groove(cfg.groove)
        self._groove_t = float("-inf")
        self._expression_sent: Optional[tuple[int, int]] = None   # (cc, value) last sent to the pad
        self._expression_t = float("-inf")
        self.muted = False
        self.finished = False                     # muted by an ending (s or t starts again)
        self._finish_requested = False            # the ending comes on the next 1
        self._break_requested = False             # a break starts on the next 1
        self._break_left = 0                      # beats of the break still to come
        self._break_return = False                # the band comes back in on this 1
        self._interlude_bar0: Optional[int] = None  # the bar an interlude began (you resting)
        self._heart_t: Optional[float] = None     # the last heartbeat played (before the music)
        self._heart_count = 0                     # heartbeats played (the filler's progress)
        self._solo_since: Optional[float] = None  # when you started playing after a rest
        self._figure_until = 0                    # beat_count when a triplet figure is over
        self._figure_rng = random.Random(cfg.harmony.seed + 41)
        self._ending: Optional[tuple[float, float]] = None   # (start, end) of the last chord
        self._ending_shape = "chord"              # how this ending goes (ending.shape)
        self._final_bars: Optional[int] = None    # bars still to play before the last chord
        self._rit_step = 1.0                      # a ritardando: period growth per beat
        self._ending_rng = random.Random(cfg.harmony.seed + 51)
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
        rest = self.cfg.interlude.after_beats * self.tempo.period
        if self.last_onset_t is None or t - self.last_onset_t > rest:
            self._solo_since = t                      # soloing again after a rest
        self.last_onset_t, self.last_note = t, note
        self.clock.hint(t, self.cfg.pulse.hint_window)
        self.response.hear(t, note, velocity, self.tempo.period)   # stops any answer at once
        self.piano.hear(t)
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
        if self.tempo.update(now, rate=rate, locked=self.locked) and self._rit_step == 1.0:
            self.clock.set_period(self.tempo.period)
            if self.clock.running:
                self._realign(now)
        self.clock.phase_gain = self.cfg.pulse.phase_gain * (self.cfg.lock.phase_rate if self.locked else 1.0)
        self.clock.max_nudge = self.cfg.pulse.max_nudge
        self.clock.max_tempo_step = self.cfg.pulse.max_tempo_step
        self.proposal = self.harmony.propose(now)
        self._auto_lock(now)
        if self.finished:                            # between songs, after the set
            if self._heart_active(now):
                self._heart_decay(now)               # ...your heartbeat and the filler pad
            elif self._heart_t is not None:          # the headband stopped: let the pad go
                self.pad.release_all()
                self._heart_t = None
            return
        if self.muted:
            return
        if self._ending is not None:
            self._ring_out(now)
            return
        idle = float("inf") if self.last_onset_t is None else now - self.last_onset_t
        period = self.tempo.period
        voicing = self.frozen if self.chord_held else self.proposal
        waiting = self.is_chart and not self.song_playing   # before the chart starts: no band

        self.response.key = getattr(self.harmony, "key", None)
        if self.response.playing(now) or self.piano.playing(now):
            self.dynamics.heard(now)                 # an answer is playing: not quiet
        if self._heart_active(now):                  # before the music: breathing with you
            self._heart_decay(now)
        else:
            self._shape_pad(now)
        if self._heart_active(now):
            pass                                     # the home chord holds
        elif not self.cfg.pad.enabled or waiting:
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
            self.piano.tick(now, self.clock.period, chord, self._scale_pcs(),
                            self.dynamics.follow_gain(),
                            self.clock.next_beat if self.clock.running else None, self.response,
                            0.0 if self.in_interlude else self.dynamics.quiet(now))
            if self.in_break:                         # a break: you alone, no answers
                self.response.cancel()
                self.piano.stop()

        # The beat clock runs while bass (pulse) or drums need it, or a chart is counting in.
        if not (self.cfg.pulse.enabled or self.cfg.drums.enabled or self.cfg.percussion.enabled
                or self.is_chart):
            self.clock.stop()
            self._reset_drums()
            return
        p = self.cfg.pulse
        if self.is_chart:
            active = self.song_playing or self._count_in_left > 0   # a song never stops by itself
        elif self.locked:
            active = True                                     # silence never stops a locked groove
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
        if not self.is_chart and not self.locked:
            active = active and voicing is not None
        if active and not self.clock.running and self.last_onset_t is not None and not self.is_chart:
            self.restart_form()
            fit = self.tempo.beat_reference(now)
            self.clock.start(fit[0] if fit else self.last_onset_t, period, now)
        elif not active and self.clock.running:
            self.clock.stop()
            self._reset_drums()
        if self.clock.running:
            # The pulse follows the harmony that is actually sounding, so pad and
            # pulse never disagree while the pad is still catching up.
            pulse_root = (self.pad.current.root_pc if self.pad.current
                          else voicing.root_pc if voicing is not None else None)
            gain = self.dynamics.follow_gain()
            if ((self.cfg.groove.auto or self.groove.pinned) and not self.is_chart
                    and now - self._groove_t >= self.cfg.tempo.update_s):
                self._groove_t = now
                self.groove.update(now, self.clock.period)
                self._choose_drums()
            for beat_t in self.clock.due(now):
                bpb, bar_pos, form_beat, sure = self._bar(self.beat_count)
                if self._count_in_left > 0:                   # count-in click: 1, 2, 3, 4
                    self._click(beat_t, first=self._count_in_left == self._count_total)
                    self._count_in_left -= 1
                    if self._count_in_left == 0:
                        self.restart_form()                   # the next beat is bar 1
                        self.song_playing = True
                        if self._count_meter:                 # a song's meter, like a count-off
                            self.groove.pin(self._count_meter)
                    continue
                if self._finish_requested and bar_pos == 0 and self._ending_bar(bpb, now):
                    self._play_ending(beat_t, now)            # the end: one last chord
                    break
                if self._rit_step != 1.0:                     # a ritardando: each beat longer
                    self.clock.period = self.clock.target_period = self.clock.period * self._rit_step
                if self._ending_shape == "piano-tag" and self._final_bars is not None:
                    self._piano_tag_beat(now, bar_pos, bpb)
                    self.beat_count += 1
                    continue
                resting = self._break_beat(bar_pos, bpb, now, pulse_root)
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
                if resting:                                   # a break: only the pad plays
                    self.beat_count += 1
                    continue
                if p.enabled and pulse_root is not None:     # no harmony heard yet: no bass
                    group = bar_pos in GROUPS.get(bpb, (0,))
                    bass_chord = next((v for v in (self.pad.current, self.proposal, voicing)
                                       if v is not None and v.root_pc == pulse_root), None)
                    self.pulse.on_beat(now, pulse_root, gain, bar_pos, boost, group,
                                       bass_chord, bpb, self._scale_pcs(), self.clock.period)
                if self.cfg.drums.enabled:
                    swing = (self.groove.swing if self.cfg.groove.auto and self.cfg.groove.auto_drums
                             and self.groove.meter and not self.is_chart else None)
                    if self.drums.pattern_name is None and self.groove.pinned:
                        self._choose_drums()
                    self.drums.on_beat(beat_t, self.clock.period, gain, form_beat, swing, boost,
                                       bpb, self.dynamics.busyness(now))
                if self.cfg.percussion.enabled and bar_pos == 0:
                    self._percussion_spell(form_beat // max(1, bpb), now)
                if self.cfg.percussion.enabled:
                    self.percussion.pitches = self._tuned_pitches()
                    self._triplet_figure(beat_t, now, form_beat, bar_pos, bpb, gain)
                if (self.cfg.percussion.enabled and self._percussion_on
                        and self.beat_count >= self._figure_until):
                    swing = (self.groove.swing if self.cfg.groove.auto and self.cfg.groove.auto_drums
                             and self.groove.meter and not self.is_chart else None)
                    if self.percussion.pattern_name is None and self.groove.pinned:
                        self._choose_drums()
                    lift = 1 + SPOTLIGHT_LIFT * self.cfg.percussion.spotlight * self.dynamics.quiet(now)
                    self.percussion.on_beat(beat_t, self.clock.period, gain * lift, form_beat,
                                            swing, boost, bpb, self.dynamics.busyness(now))
                self._interlude_beat(now, form_beat, bar_pos, bpb)
                self._comp_under_solo(now, form_beat, bar_pos, bpb)
                self.beat_count += 1
            self.drums.tick(now)
            self.percussion.tick(now)

    def _bar(self, beat: int) -> tuple[int, int, int, bool]:
        """(beats per bar, position in the bar, beats since a downbeat, groove heard clearly)
        for beat number `beat`: from the chart, else the groove heard, else pulse.beats_per_bar."""
        g = self.groove
        if self.is_chart:
            bpb = max(1, self.harmony.beats_per_bar)
            return bpb, beat % bpb, beat, True
        if (self.cfg.groove.auto or g.pinned) and g.meter:
            form = beat - g.downbeat
            sure = g.pinned or g.confidence >= self.cfg.groove.confident_at
            return g.meter, form % g.meter, form, sure
        bpb = max(1, self.cfg.pulse.beats_per_bar)
        return bpb, beat % bpb, beat, False

    # ---- your heartbeat, before the music ------------------------------------
    def heartbeat(self, now: float, bpm: Optional[float] = None) -> bool:
        """A heartbeat from a headband. Until the band starts: a soft kick, and the pad's
        home chord swelling with it (body.heart). True if it played."""
        b = self.cfg.body
        if (not b.heart or (self.muted and not self.finished) or self.clock.running
                or self._count_in_left > 0 or self._ending is not None):
            return False
        from .patterns import GM_DRUMS

        if self.cfg.drums.enabled:
            ch = self.cfg.drums.channel - 1
            self.out.note_on(ch, GM_DRUMS["kick"], b.heart_kick_velocity)
            self.out.note_off_at(now + self.cfg.drums.note_length_s, ch, GM_DRUMS["kick"])
        chord = self._filler_chord(self._heart_count // max(1, b.filler_beats)) if b.filler \
            else self._home_chord()
        self._heart_count += 1
        if self.cfg.pad.enabled and chord is not None:
            if self.pad.current is None or {n % 12 for n in self.pad.current.notes} != {
                    n % 12 for n in chord.notes}:
                self.pad.update(now, chord, self.tempo.period)
            self._heart_t = now
            self._send_expression(now, round(127 * self._heart_level(now)), force=True)
        self._heart_t = now
        return True

    def _filler_chord(self, step: int) -> Optional[Voicing]:
        """A slow, quiet progression in the home key while you talk (between songs): the
        home chord, then chords on the key's other degrees (FILLER_DEGREES), round again."""
        from .modal import SCALES

        home = self._home_chord()
        if home is None:
            return None
        key = self._home_key()
        tonic, mode = key
        scale = sorted(SCALES.get(mode, SCALES["major"]))
        degree = FILLER_DEGREES[step % len(FILLER_DEGREES)] % len(scale)
        pcs = [(tonic + scale[(degree + k) % len(scale)]) % 12 for k in (0, 2, 4)]
        root = pcs[0]
        third = (pcs[1] - root) % 12
        base = 12 * (self.cfg.pad.octave + 1) + root
        notes = (base, base + (pcs[2] - root) % 12, base + 12, base + 12 + third)
        return Voicing(root, third if third in (3, 4) else None, notes, scheduled=True)

    def _home_key(self) -> Optional[tuple[int, str]]:
        from .modal import SCALES, parse_keys

        h = self.cfg.harmony
        if h.keys:
            return parse_keys(h.keys)[0]
        if self.cfg.root_pc is not None:
            return self.cfg.root_pc, h.mode if h.mode in SCALES else "major"
        return getattr(self.harmony, "key", None)

    def _heart_active(self, now: float) -> bool:
        return (self._heart_t is not None and not self.clock.running and not self.muted
                and now - self._heart_t < HEART_HOLD_S)

    def _heart_decay(self, now: float) -> None:
        self._send_expression(now, round(127 * self._heart_level(now)))

    def _heart_level(self, now: float) -> float:
        """The pad's level with your heartbeat: a bed at body.pad_level that ebbs and flows
        slowly (body.ebb_s, body.ebb_depth), plus a swell on each beat that sinks away."""
        b = self.cfg.body
        ebb = 1 - b.ebb_depth * (0.5 + 0.5 * math.cos(2 * math.pi * now / max(1.0, b.ebb_s)))
        beat = math.exp(-(now - self._heart_t) / HEART_DECAY_S) if self._heart_t is not None else 0.0
        return min(1.0, b.pad_level * ebb + b.heart_pad_level * beat)

    def _home_chord(self) -> Optional[Voicing]:
        """The song's home key (its key palette's first, or the key set or heard), open."""
        from .modal import SCALES

        key = self._home_key()
        if not key:
            return None
        tonic, mode = key
        scale = SCALES.get(mode, SCALES["major"])
        third = 4 if 4 in scale else 3 if 3 in scale else None
        base = 12 * (self.cfg.pad.octave + 1) + tonic
        notes = (base, base + 7, base + 12) + ((base + 12 + third,) if third else ())
        return Voicing(tonic, third, notes, scheduled=True)

    # ---- interludes ---------------------------------------------------------
    @property
    def in_interlude(self) -> bool:
        return self._interlude_bar0 is not None

    def _comp_under_solo(self, now: float, form_beat: int, bar_pos: int, bpb: int) -> None:
        """While you play: the piano comps behind you (piano.comp), sparser when you're busy,
        quiet while it is answering you."""
        p = self.cfg.piano
        if (p.comp <= 0 or not p.enabled or self.in_interlude or self.in_break
                or self.piano.answering()):
            return
        chord = self.pad.current or self.proposal
        busy = self.dynamics.busyness(now)
        self.piano.comp_beat(now, self.clock.period, form_beat // max(1, bpb), bar_pos, bpb, chord,
                             self.dynamics.follow_gain(), self._scale_pcs(), p.comp, busy)

    def _interlude_beat(self, now: float, form_beat: int, bar_pos: int, bpb: int) -> None:
        """You have rested a while: the piano comps and the guitar plays your phrases, taking
        turns every interlude.turn_bars bars, until you come back in (on_note ends it)."""
        il = self.cfg.interlude
        resting = (il.enabled and self.last_onset_t is not None and not self.in_break
                   and not self._finish_requested
                   and now - self.last_onset_t >= il.after_beats * self.clock.period)
        if not resting:
            self._interlude_bar0 = None
            return
        bar = form_beat // max(1, bpb)
        if self._interlude_bar0 is None:
            if bar_pos != 0:
                return                                # it starts on a 1
            self._interlude_bar0 = bar
        chord = self.pad.current or self.proposal
        players = [v for v, on in (("piano", self.cfg.piano.enabled),
                                   ("guitar", self.cfg.response.enabled and self.response.memory))
                   if on]
        if not players or chord is None:
            return
        turn = (bar - self._interlude_bar0) // max(1, il.turn_bars)
        who = players[turn % len(players)]
        gain = self.dynamics.follow_gain()
        if who == "piano":
            self.piano.comp_beat(now, self.clock.period, bar, bar_pos, bpb, chord, gain,
                                 self._scale_pcs())
        elif bar_pos == 0 and (bar - self._interlude_bar0) % max(1, il.guitar_every_bars) == 0 \
                and not self.response.playing(now):
            left = il.turn_bars - (bar - self._interlude_bar0) % max(1, il.turn_bars)
            self.response.play_from_memory(now, self.clock.period,      # a solo for its turn
                                           {n % 12 for n in chord.notes}, self._scale_pcs(),
                                           gain, now, beats=left * bpb - 1)

    # ---- breaks -------------------------------------------------------------
    @property
    def in_break(self) -> bool:
        """From the break's 1 until the band is back on the next 1."""
        return self._break_left > 0 or self._break_return

    def request_break(self) -> str:
        """A break on the next 1 (or, during one, end it at the next 1). Returns what happens."""
        if self.muted or not self.clock.running or self._count_in_left > 0:
            return "nothing playing to break"
        if self._break_left > 0:
            bpb = self._bar(self.beat_count)[0]
            self._break_left = (self._break_left - 1) % bpb + 1     # to the end of this bar
            return "break ends on the next 1"
        self._break_requested = True
        return f"break: the band stops for {self.cfg.breaks.bars} bar(s) on the next 1"

    def _break_beat(self, bar_pos: int, bpb: int, now: float, root_pc: Optional[int]) -> bool:
        """Before each beat: start or end a break; True while the rhythm section rests."""
        from .patterns import GM_DRUMS

        if bar_pos == 0 and self._break_return:          # back in on the 1, with a crash
            self._break_return = False
            if self.cfg.drums.enabled:
                d = self.cfg.drums
                self.out.note_on(d.channel - 1, GM_DRUMS["crash"], min(127, d.velocity + d.accent))
                self.out.note_off_at(now + d.note_length_s, d.channel - 1, GM_DRUMS["crash"])
        if bar_pos == 0 and self._break_requested:
            self._break_requested = False
            self._break_left = max(1, self.cfg.breaks.bars) * bpb
            self.response.cancel()
            self.piano.stop()
            self._reset_drums()
            if self.cfg.breaks.hit:
                self._hit(now, root_pc)
        if self._break_left <= 0:
            return False
        self._break_left -= 1
        if self._break_left == 0:
            self._break_return = True
        return True

    def _hit(self, now: float, root_pc: Optional[int]) -> None:
        """One short band hit: the bass's root, a kick and a crash."""
        from .patterns import GM_DRUMS

        p, d = self.cfg.pulse, self.cfg.drums
        if p.enabled and root_pc is not None:
            root = 12 * (p.octave + 1) + root_pc
            self.out.note_on(p.channel - 1, root, min(127, p.velocity + p.accent))
            self.out.note_off_at(now + p.note_length_s, p.channel - 1, root)
        if d.enabled:
            for name in ("kick", "crash"):
                self.out.note_on(d.channel - 1, GM_DRUMS[name], min(127, d.velocity + d.accent))
                self.out.note_off_at(now + d.note_length_s, d.channel - 1, GM_DRUMS[name])

    def _tuned_pitches(self) -> list[int]:
        """Tuned percussion: the sounding chord's notes, two octaves from percussion.octave
        (empty: drum notes as written)."""
        p = self.cfg.percussion
        chord = self.pad.current or self.proposal
        if not p.tuned or chord is None:
            return []
        low = 12 * (p.octave + 1)
        pcs = {n % 12 for n in chord.notes}
        return [n for n in range(low, low + 24) if n % 12 in pcs]

    def soloing_s(self, now: float) -> float:
        """How long you've been playing without a rest (0 if you're resting)."""
        rest = self.cfg.interlude.after_beats * self.clock.period
        if self._solo_since is None or self.last_onset_t is None or now - self.last_onset_t > rest:
            return 0.0
        return now - self._solo_since

    def _triplet_figure(self, beat_t: float, now: float, form_beat: int, bar_pos: int,
                        bpb: int, gain: float) -> None:
        """At the last two beats of a phrase, maybe a percussion triplet figure into the next
        1: likelier the longer you've soloed, doubled up past 70% of triplet_build_s."""
        p = self.cfg.percussion
        if self.beat_count < self._figure_until:
            return
        bar = form_beat // max(1, bpb)
        every = max(1, p.triplet_every_bars)
        if (bpb < 2 or bar_pos != bpb - 2 or (bar + 1) % every != 0 or p.triplets <= 0
                or self.in_break or self.in_interlude):
            return
        build = min(self.soloing_s(now) / p.triplet_build_s, 1.0)
        if build <= 0 or self._figure_rng.random() >= p.triplets * build:
            return
        per_beat = TRIPLET_DOUBLE if build >= TRIPLET_DOUBLE_AT else TRIPLET_SINGLE
        self.percussion.triplet_figure(beat_t, self.clock.period, 2, per_beat, gain)
        self._figure_until = self.beat_count + 2          # this beat and the next: the figure

    def _percussion_spell(self, bar: int, now: float) -> None:
        """At the start of each spell: does the percussion play the next spell_bars bars?
        percussion.presence of them, likelier when it's quiet all round."""
        p = self.cfg.percussion
        if bar % max(1, p.spell_bars) != 0:
            return
        chance = p.presence + (1 - p.presence) * p.spotlight * self.dynamics.quiet(now)
        self._percussion_on = self._spell_rng.random() < chance

    def _reset_drums(self) -> None:
        self.drums.reset()
        self.percussion.reset()

    def _choose_drums(self) -> None:
        """Keep your drum pattern if its cycle fits the meter heard; else one that does."""
        g = self.groove
        self.drums.pattern_name = self.percussion.pattern_name = None
        if not ((self.cfg.groove.auto_drums or g.pinned) and g.meter):
            return
        from .patterns import load

        if load(self.cfg.drums.pattern).beats % g.meter != 0:
            self.drums.pattern_name = GROOVE_PATTERNS.get((g.meter, g.feel),
                                                          GROOVE_PATTERNS.get(g.meter))
        if load(self.cfg.percussion.pattern).beats % g.meter != 0:
            self.percussion.pattern_name = PERCUSSION_PATTERNS.get(g.meter)

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
        if self.cfg.pad.expression_cc is None or now - self._expression_t < EXPRESSION_INTERVAL_S:
            return
        level = self.dynamics.pad_level(now)
        if self.in_break:                             # a break: the pad stays back
            level = min(level, self.cfg.breaks.pad_level)
        self._send_expression(now, round(127 * level))

    def _send_expression(self, now: float, value: int, force: bool = False) -> None:
        cc = self.cfg.pad.expression_cc
        if cc is None or (not force and now - self._expression_t < EXPRESSION_INTERVAL_S):
            return
        last = self._expression_sent
        if force or last is None or last[0] != cc or abs(last[1] - value) >= EXPRESSION_STEP:
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
        self._finish_requested, self._ending, self.finished = False, None, False
        self._final_bars, self._rit_step = None, 1.0
        self._break_requested, self._break_left, self._break_return = False, 0, False
        self._interlude_bar0 = None
        self.unlock()
        self.release_chord()
        self.muted = True
        self.out.panic()
        self._expression_sent = None           # re-send the pad level after resume
        self.pad.reset()
        self.pulse.reset()
        self._reset_drums()
        self.response.reset()
        self.piano.reset()
        self.clock.stop()

    def resume(self) -> None:
        self.muted = self.finished = False

    # ---- the ending ---------------------------------------------------------
    def finish(self) -> bool:
        """End the song: the drums fill into the next 1, where the band plays one last chord
        (the tonic of the key you're playing in, else the chord of the moment) that rings for ending.ring_s and fades,
        then everything stops (finished: s or a count-off starts again). False if nothing
        is playing."""
        if self.muted or not self.clock.running or self._count_in_left > 0:
            return False
        self._finish_requested = True
        shape = self.cfg.ending.shape
        if shape == "random":
            shape = self._ending_rng.choice(ENDING_SHAPES)
        self._ending_shape = shape
        if self.cfg.ending.fill and shape in ("chord", "button"):
            self.drums.fill_requested = True
        return True

    def _ending_bar(self, bpb: int, now: float) -> bool:
        """At a 1 after f: is this the last chord's 1? A tag, a ritardando or a piano tag
        first plays its bars (and the drums fill into the last chord)."""
        e = self.cfg.ending
        if self._ending_shape in ("chord", "button"):
            return True
        if self._final_bars is None:                  # the shape's bars start now
            self._final_bars = {"tag": e.tag_bars, "ritardando": e.rit_bars,
                                "piano-tag": 1}.get(self._ending_shape, 0)
            if self._ending_shape == "ritardando":
                self._rit_step = (1 / e.rit_to) ** (1 / max(1, e.rit_bars * bpb))
        elif self._final_bars > 0:
            self._final_bars -= 1
        if self._final_bars == 1 and self._ending_shape == "tag" and e.fill:
            self.drums.fill_requested = True          # the tag's last bar: a fill into the 1
        return self._final_bars <= 0

    def _piano_tag_beat(self, now: float, bar_pos: int, bpb: int) -> None:
        """A piano tag's bar: the band drops out; soft piano notes on the tonic chord ("plink,
        plink..."), then the full band on the next 1 ("...PLUNK")."""
        if bar_pos == 0:
            self.response.cancel()
            self._reset_drums()
            if self.cfg.pad.enabled:
                self._send_expression(now, round(127 * self.cfg.breaks.pad_level), force=True)
        chord = self._ending_chord(now)
        if chord is None or not self.cfg.piano.enabled or bar_pos not in PIANO_TAG_BEATS.get(
                bpb, (0, bpb // 2)):
            return
        c = self.cfg.piano
        low = 12 * (c.octave + 2)                     # an octave above the piano's usual place
        tones = sorted(n for n in range(low, low + 12) if n % 12 in {x % 12 for x in chord.notes})
        note = tones[-1] if bar_pos == 0 else tones[len(tones) // 2]
        self.out.note_on(c.channel - 1, note, max(1, round(c.velocity * PIANO_TAG_SOFT)))
        self.out.note_off_at(now + self.clock.period, c.channel - 1, note)

    def _ending_chord(self, now: float) -> Optional[Voicing]:
        """The tonic of the key you're playing in: the key the harmony has been following (or,
        if a key is set, the one heard from your playing), turned to its relative minor or
        major if that's the note you last played (you land on the tonic); else the chord of
        the moment."""
        from .modal import SCALES, parse_keys

        h = self.cfg.harmony
        key = getattr(self.harmony, "key", None)
        palette = parse_keys(h.keys) if h.keys else []
        if not palette and h.mode != "auto" and self.cfg.root_pc is not None:
            heard = getattr(self.harmony, "heard_key", None)      # a key set: trust your ears
            key = (heard(now) if heard else None) or key
        if key and self.last_note is not None:
            tonic, mode = key
            if palette:                                # you landed on another palette key's tonic
                landed = [k for k in palette if k[0] == self.last_note % 12]
                key = landed[0] if landed and key not in landed else key
            else:
                relative = {"major": ((tonic + 9) % 12, "minor"),
                            "minor": ((tonic + 3) % 12, "major")}.get(mode)
                if relative and self.last_note % 12 == relative[0]:
                    key = relative
        if key and not self.is_chart:
            tonic, mode = key
            scale = SCALES.get(mode, frozenset())
            third = 4 if 4 in scale and mode != "chromatic" else 3 if 3 in scale and mode != "chromatic" else None
            if third is None and self.pad.current is not None and self.pad.current.root_pc == tonic:
                third = self.pad.current.third
            base = 12 * (self.cfg.pad.octave + 1) + tonic
            notes = (base, base + 7, base + 12 + 2) + ((base + 12 + third,) if third else ())
            return Voicing(tonic, third, notes, scheduled=True)
        chord = self.pad.current or self.proposal
        if chord is None:
            return None
        return Voicing(chord.root_pc, chord.third, chord.notes, chord.name, scheduled=True)

    def _play_ending(self, beat_t: float, now: float) -> None:
        """The final 1: the last chord on the pad, the bass's root, a kick and a crash."""
        from .patterns import GM_DRUMS

        ring = self.cfg.ending.ring_s
        if self._ending_shape == "button":
            ring = self.cfg.ending.button_beats * self.clock.period
        elif self._ending_shape == "ritardando":
            ring *= FERMATA                            # held: a fermata
        chord = self._ending_chord(now)
        self.response.cancel()
        self.piano.final_chord(now, chord, self.clock.period)
        self._reset_drums()
        if chord is not None:
            if self.cfg.pad.enabled:
                self.pad.update(now, chord, self.clock.period)
                self._send_expression(now, round(127 * self.cfg.ending.level), force=True)
            if self.cfg.pulse.enabled:
                p = self.cfg.pulse
                root = 12 * (p.octave + 1) + chord.root_pc
                self.out.note_on(p.channel - 1, root, min(127, p.velocity + p.accent))
                self.out.note_off_at(now + ring, p.channel - 1, root)
        for cfg, notes in ((self.cfg.drums, ("kick", "crash")),
                           (self.cfg.percussion, ("conga_low",))):
            if cfg.enabled:
                for name in notes:
                    self.out.note_on(cfg.channel - 1, GM_DRUMS[name], min(127, cfg.velocity + cfg.accent))
                    self.out.note_off_at(now + cfg.note_length_s, cfg.channel - 1, GM_DRUMS[name])
        self.clock.stop()
        self._finish_requested = False
        self._final_bars, self._rit_step = None, 1.0
        self._ending = (now, now + ring)

    def _ring_out(self, now: float) -> None:
        """While the last chord rings: fade the pad out, then stop everything."""
        start, end = self._ending
        self.piano.tick(now, self.clock.period, None, None, 1.0, None, self.response)  # its roll
        if now >= end:
            self.panic()
            self.finished = True
            return
        level = self.cfg.ending.level * (1 - (now - start) / (end - start))
        self._send_expression(now, round(127 * level))

    def lock(self, now: float, settle: bool = True) -> bool:
        """Lock the tempo: pad and pulse keep going through silence, the tempo follows only
        slowly and never jumps. Chords still follow you (see hold_chord). Needs something
        heard first. Returns True if locked.

        The beat is never moved: the lock is usually pressed on a computer key, whose timing
        has nothing to do with the music. Only the beat spacing settles on your notes
        (settle=False: not even that, for a tempo just counted or set by the song: notes
        played before it were at another tempo)."""
        if self.muted or (self.last_onset_t is None and not self.clock.running):
            return False
        self.locked, self._confident_since = True, None
        # Settle the tempo on the beat grid your recent notes fit best (a small, inaudible
        # change in beat spacing), but never move where the next beat falls.
        if settle and len(self.tempo.onsets) >= self.cfg.tempo.min_onsets:
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
        self._reset_drums()
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
        self.lock(last_tap, settle=False)

    def count_off(self, beats: int, bpm: float, downbeat_t: float, now: float) -> bool:
        """A count-off of `beats` taps: tempo, and that meter with 1 at downbeat_t (the beat
        after the last tap); the band comes in there, tempo locked. Returns False if muted."""
        if self.muted:
            return False
        self.set_tempo(bpm)
        self.clock.start(downbeat_t - self.tempo.period, self.tempo.period)   # next beat: 1
        self.restart_form()
        self.groove.pin(beats)
        self.lock(now, settle=False)
        return True

    def start_song(self, now: float) -> Optional[float]:
        """Count in one bar of clicks, then play, tempo locked (like pressing play in iReal Pro).

        With a chart: from bar 1, in the chart's meter. Without: in song.count's meter (pinned,
        like a count-off), drums and pulse first, the pad once it has heard you.
        The tempo: song.tempo, else harmony.chart_bpm / --tempo, else the tempo you have been
        playing if it is clear, else (a chart) a typical tempo for its style.
        Returns the count-in tempo, or None if muted or there is no tempo to count in at."""
        if self.muted:
            return None
        bpm = self.cfg.song.tempo or self.cfg.harmony.chart_bpm
        if bpm is None and self.tempo.confidence < self.cfg.pulse.min_confidence:
            bpm = getattr(self.harmony, "default_bpm", None)
            if bpm is None and not self.is_chart:
                return None                         # nothing to go on: count off with taps
        if bpm is not None:
            self.tempo.set_bpm(bpm)
        self.clock.start(now, self.tempo.period)     # first click one beat from now
        self.song_playing = False
        self.pad.release_all()
        if self.is_chart:
            self._count_meter = None
            beats = self.harmony.beats_per_bar
        else:
            self._count_meter = self.cfg.song.count
            beats = self.cfg.song.count or self.cfg.pulse.beats_per_bar
        self._count_in_left = self._count_total = max(1, beats)
        self.lock(now, settle=False)
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
            "finished": self.finished,
            "break": self.in_break or self._break_requested,
            "interlude": self.in_interlude,
            "ending": self._finish_requested or self._ending is not None,
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
