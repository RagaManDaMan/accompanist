"""The parameter registry: every tunable, declared once.

Config validation (config.py), `accompanist params` (CLI help and --json schema),
live changes (Controller.set_param) and any future UI are all generated from this
list. To add a tunable, add a Param here; the config section gets the field
automatically. Keys are "section.name", matching the [section] in config.toml.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

NOTE_CHOICES = ("auto", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


@dataclass(frozen=True)
class Param:
    key: str                         # "section.name"
    type: type                       # bool, int, float or str
    default: Any
    label: str
    help: str
    group: str
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    choices: Optional[tuple] = None  # allowed values (for str params: what a UI should offer)
    live: bool = True                # may change while running (applies at the next tick)
    nullable: bool = False           # None ("not set") is allowed
    deprecated: bool = False         # accepted in old config files, ignored
    bool_as_number: bool = False     # true/false accepted for a 0-1 number (was a switch)
    primary: bool = False            # one of the few controls a simple GUI shows
    check: Optional[Callable[[Any], None]] = None  # extra validation; raises ValueError

    @property
    def section(self) -> str:
        return self.key.split(".", 1)[0]

    @property
    def name(self) -> str:
        return self.key.split(".", 1)[1]

    def schema(self) -> dict:
        return {
            "key": self.key, "type": self.type.__name__, "default": self.default,
            "min": self.min, "max": self.max, "step": self.step,
            "choices": list(self.choices) if self.choices else None,
            "group": self.group, "label": self.label, "help": self.help,
            "live": self.live, "nullable": self.nullable, "deprecated": self.deprecated,
            "primary": self.primary,
        }


def _check_pattern(v: Any) -> None:
    from .config import ConfigError
    from .patterns import load

    try:
        load(v)
    except ConfigError as e:
        raise ValueError(str(e)) from e


def _multiple_of(v: Any, n: int) -> None:
    if v % n:
        raise ValueError(f"must be a multiple of {n}, got {v}")


def _check_root(v: Any) -> None:
    from .config import parse_root, ConfigError

    try:
        parse_root(v)
    except ConfigError as e:
        raise ValueError(str(e).split(": ", 1)[-1]) from e


P = Param
PARAMS: list[Param] = [
    # ---- output ---------------------------------------------------------------
    P("output.virtual_name", str, "Accompanist", "Virtual port name",
      "Create a virtual MIDI source with this name (Logic sees it automatically).",
      "Output", nullable=True, live=False),
    P("output.port", str, None, "Output port",
      "Send to an existing port instead, matched by part of its name (e.g. 'IAC Driver Bus 1').",
      "Output", nullable=True, live=False),
    P("output.record", bool, False, "Record every run",
      "Save every `run` as a take in takes/ (your notes and key presses: small data files, "
      "not audio), as if --record were given; --no-record skips it once.", "Output", live=False),

    # ---- tempo ----------------------------------------------------------------
    P("tempo.initial_bpm", float, 70.0, "Starting tempo",
      "The reading before there is enough playing (min_onsets notes) to go on.",
      "Tempo", 20, 300, 1, live=False),
    P("tempo.max_count_bpm", float, 320.0, "Fastest count-off",
      "A count-off, tap tempo or song tempo may go up to this, beyond max_bpm (which only "
      "limits listening): fast 5/4 and 7/4 counts.", "Tempo", 20, 400, 1),
    P("tempo.prior_bpm", float, 80.0, "Tempo prior",
      "Chooses the beat 'octave' when playing is ambiguous: 80 reads a 120 melody as 60. "
      "Set near your piece's tempo (e.g. 110 for fast pieces).", "Tempo", 20, 300, 1),
    P("tempo.min_bpm", float, 40.0, "Slowest tempo", "Never read a tempo below this.",
      "Tempo", 20, 300, 1),
    P("tempo.max_bpm", float, 180.0, "Fastest tempo", "Never read a tempo above this.",
      "Tempo", 20, 300, 1),
    P("tempo.window_s", float, 30.0, "Listening window",
      "Seconds of recent playing the tempo estimate looks at.", "Tempo", 5, 120, 1),
    P("tempo.halflife_s", float, 12.0, "Memory half-life",
      "Older notes count less: their weight halves every this many seconds.", "Tempo", 1, 120, 1),
    P("tempo.alpha", float, 0.25, "Follow speed",
      "How fast the estimate follows you (0-1). Higher = quicker but jumpier.", "Tempo", 0.01, 1, 0.01),
    P("tempo.tap_sigma_oct", float, 0.3, "Prior width after tapping",
      "Tapping the tempo also narrows the prior to this (octaves), so the tempo you tapped "
      "wins over look-alike tempi (3:2, 4:3) in busy playing.", "Tempo", 0.1, 2, 0.05),
    P("tempo.prior_sigma_oct", float, 0.5, "Prior width",
      "How strongly prior_bpm pulls, as a width in octaves (smaller = stronger).",
      "Tempo (advanced)", 0.1, 2, 0.05),
    P("tempo.switch_margin", float, 0.1, "Switch margin",
      "A different tempo peak must score this fraction better to take over.",
      "Tempo (advanced)", 0, 1, 0.01),
    P("tempo.switch_hold_s", float, 4.0, "Switch hold",
      "...and keep scoring better for this many seconds.", "Tempo (advanced)", 0, 30, 0.5),
    P("tempo.score_memory", float, 0.5, "Score memory",
      "Blend each second's tempo scores with the previous ones (0 = none).",
      "Tempo (advanced)", 0, 0.95, 0.05),
    P("tempo.peak_width", float, 0.06, "Peak width",
      "Tempi within this fraction of the estimate count as the same peak.",
      "Tempo (advanced)", 0.01, 0.2, 0.01),
    P("tempo.min_onsets", int, 8, "Minimum notes",
      "Fewer notes than this in the window: keep the previous estimate.", "Tempo (advanced)", 3, 64, 1),
    P("tempo.update_s", float, 1.0, "Update interval",
      "Recompute the tempo this often (seconds).", "Tempo (advanced)", 0.2, 5, 0.1),
    P("tempo.confidence_scale", float, 0.3, "Confidence scale",
      "How far the winning score must stand above the median to count as full confidence.",
      "Tempo (advanced)", 0.05, 1, 0.05),
    P("tempo.bin_s", float, 0.01, "Onset resolution",
      "Time resolution of the onset signal (seconds).", "Tempo (advanced)", 0.005, 0.05, 0.005),
    P("tempo.smooth_s", float, 0.06, "Timing slop",
      "Gaussian smoothing of onsets (seconds). Larger tolerates looser timing.",
      "Tempo (advanced)", 0.005, 0.2, 0.005),
    P("tempo.bpm_step", float, 0.5, "Search step", "Resolution of the bpm search.",
      "Tempo (advanced)", 0.1, 2, 0.1),
    P("tempo.min_ioi_s", float, 0.06, "Chord window",
      "Onsets closer than this count as one (chords, grace notes).", "Tempo (advanced)", 0, 0.2, 0.01),
    P("tempo.tolerance", float, 0.3, "(unused)", "Used by the old tempo estimator; ignored.",
      "Tempo (advanced)", deprecated=True),
    P("tempo.max_gap_beats", float, 6.0, "(unused)", "Used by the old tempo estimator; ignored.",
      "Tempo (advanced)", deprecated=True),

    # ---- harmony --------------------------------------------------------------
    P("harmony.model", str, "drone", "Harmony model",
      "Which harmony model drives the pad: 'drone' (root + fifth, third if played), "
      "'modal' (chords from a key and mode, following what you play) or 'chart' (a chord chart).",
      "Harmony", choices=("drone", "modal", "chart"), live=False),
    P("harmony.chart", str, None, "Chart",
      "chart model: a MusicXML chord chart (e.g. exported from iReal Pro); or `--chart FILE`.",
      "Harmony", nullable=True, live=False),
    P("harmony.chart_bpm", float, None, "Chart tempo",
      "chart model: the count-in tempo (or `--tempo N`). Unset: a typical tempo for the "
      "chart's style (Ballad 60, Medium Swing 120, ...), else the tempo you last played.",
      "Harmony", 20, 300, 1, nullable=True),
    P("harmony.melody_colors", bool, True, "Colour from your melody",
      "chart model: add the colour tones you play (9, #11, 13, b9 ...) to the chart's chord, "
      "from the next beat until the chord changes.", "Harmony"),
    P("harmony.melody_min_notes", int, 1, "Colour after",
      "chart model: a colour tone is added once you have played it this many times on the chord.",
      "Harmony", 1, 8, 1),
    P("harmony.melody_max_tensions", int, 2, "Most colour tones",
      "chart model: at most this many colour tones added to one chord (0 = none).",
      "Harmony", 0, 4, 1),
    P("harmony.transpose", int, 0, "Transpose",
      "chart model: play the chart this many semitones up (+) or down (-).", "Harmony", -11, 11, 1),
    P("harmony.mode", str, "auto", "Mode",
      "modal model: 'major', 'minor' (with raised 7th), 'chromatic' (any chord), or 'auto' "
      "(major or minor, detected from your playing).", "Harmony",
      choices=("auto", "major", "minor", "chromatic")),
    P("harmony.color", float, 0.5, "Colour",
      "modal model: how readily it reaches past plain triads for 7ths, add9, sus, dim "
      "(0 = plain, 1 = adventurous).", "Harmony", 0, 1, 0.05),
    P("harmony.chord_memory_s", float, 4.0, "Chord memory",
      "modal model: the chord follows roughly this many seconds of your playing (half-life).",
      "Harmony", 0.5, 60, 0.5),
    P("harmony.chord_stickiness", float, 0.05, "Chord stickiness",
      "modal model: the current chord wins ties by this much (higher = changes less).",
      "Harmony", 0, 0.5, 0.01),
    P("harmony.wander", float, 0.3, "Wander",
      "modal model: how often it prefers a close runner-up chord that also fits, for variety "
      "(0 = always the best fit).", "Harmony", 0, 1, 0.05),
    P("harmony.wander_every_s", float, 8.0, "Wander every",
      "modal model: how long each wandering choice lasts (seconds).", "Harmony", 1, 60, 1),
    P("harmony.seed", int, 0, "Random seed",
      "Seeds the random choices (wander, voicings), so a replay sounds exactly like the take.",
      "Harmony", 0, 999999, 1),
    P("harmony.key_margin", float, 0.1, "Key stickiness",
      "modal model, auto: a new key must fit this much better to take over.", "Harmony", 0, 1, 0.01),
    P("harmony.root", str, "auto", "Root",
      "'auto' follows what you play; or fix the tonic, e.g. D, F#, Bb. (modal: the key's tonic)", "Harmony",
      choices=NOTE_CHOICES, check=_check_root),
    P("harmony.half_life_s", float, 12.0, "Pitch memory",
      "How long it remembers what you played (half-life, seconds). modal: decides the key.",
      "Harmony", 1, 300, 1),
    P("harmony.third_threshold", float, 0.35, "Third threshold",
      "drone model: the third is added once it reaches this share of the root's weight.",
      "Harmony", 0, 1, 0.05),
    P("harmony.switch_margin", float, 1.25, "Root stickiness",
      "drone model: a new root must outweigh the old one by this factor.", "Harmony", 1, 4, 0.05),

    # ---- pad ------------------------------------------------------------------
    P("pad.enabled", bool, True, "Pad on", "Play the sustained pad.", "Pad", primary=True),
    P("pad.feel", float, 0.5, "Pad feel",
      "Algorithmic (0: changes on bar lines, one steady voicing, plain chords, fixed level) to humanize (1: changes on any beat, varied voicings, colour and wandering chords, breathes with you, a slight strum).",
      "Pad", 0, 1, 0.01, primary=True),
    P("pad.channel", int, 1, "Pad channel", "MIDI channel (1-16).", "Pad", 1, 16, 1, live=False),
    P("pad.octave", int, 3, "Pad octave", "Octave of the pad's root (3 = C3 upward).", "Pad", 0, 7, 1),
    P("pad.velocity", int, 55, "Pad velocity", "Loudness of pad notes.", "Pad", 1, 127, 1),
    P("pad.lag_beats", float, 4.0, "Pad lag",
      "A new harmony must persist this many beats before the pad follows.", "Pad", 0, 64, 1),
    P("pad.min_change_beats", float, 8.0, "Pad change limit",
      "Never change chords faster than this many beats.", "Pad", 0, 128, 1),
    P("pad.change_on", str, "beat", "Pad changes on",
      "When the beat is known, a chord change waits for the next 'beat' or 'bar' ('now' = "
      "as soon as its lag is over).", "Pad", choices=("now", "beat", "bar")),
    P("pad.variation", float, 0.4, "Voicing variation",
      "0 = always the smoothest voice-leading; higher = more varied inversions and spacings.",
      "Pad", 0, 1, 0.05),
    P("pad.revoice_bars", int, 4, "Re-voice after",
      "Re-voice a chord that has stood still for this many bars (0 = never).", "Pad", 0, 64, 1),
    P("pad.expression_cc", int, 11, "Pad level controller",
      "Controller that shapes the pad's level continuously (11 = Expression, 7 = Volume; "
      "unset = fixed level).", "Pad", 0, 127, 1, nullable=True),
    P("pad.strum_ms", float, 0.0, "Pad strum",
      "The notes of a new chord start up to this many ms apart (0 = together).", "Pad", 0, 120, 1),
    P("pad.velocity_spread", int, 0, "Pad velocity spread",
      "Each pad note up to this much softer or louder (humanize).", "Pad", 0, 40, 1),
    P("pad.overlap_s", float, 0.25, "Pad overlap",
      "Old notes ring this long under a new chord.", "Pad", 0, 5, 0.05),
    P("pad.idle_release_s", float, 20.0, "Pad release after",
      "Release the pad after this much silence (seconds).", "Pad", 1, 36000, 1),

    # ---- pulse ----------------------------------------------------------------
    P("pulse.enabled", bool, True, "Pulse on", "Play a soft note on every beat.", "Pulse", primary=True),
    P("pulse.feel", float, 0.3, "Bass feel",
      "Algorithmic (0: the root on every beat, even, on the grid) to humanize (1: bass shapes, leans toward your timing, follows your phrasing and dynamics, slightly varied and laid back).",
      "Pulse", 0, 1, 0.01, primary=True),
    P("pulse.channel", int, 2, "Pulse channel", "MIDI channel (1-16).", "Pulse", 1, 16, 1, live=False),
    P("pulse.octave", int, 2, "Pulse octave", "Octave of the pulse note.", "Pulse", 0, 7, 1),
    P("pulse.velocity", int, 45, "Pulse velocity", "Loudness of the pulse.", "Pulse", 1, 127, 1),
    P("pulse.accent", int, 25, "Bar accent", "Extra velocity on the first beat of each bar.",
      "Pulse", 0, 127, 1),
    P("pulse.beats_per_bar", int, 4, "Beats per bar", "Accent every this many beats.", "Pulse", 1, 16, 1),
    P("pulse.min_confidence", float, 0.5, "Pulse start confidence",
      "Start pulsing once the tempo confidence reaches this.", "Pulse", 0, 1, 0.05),
    P("pulse.stop_confidence", float, 0.15, "Pulse stop confidence",
      "Once pulsing, stop only if the tempo confidence falls below this.", "Pulse", 0, 1, 0.05),
    P("pulse.stop_after_s", float, 8.0, "Low-confidence patience",
      "Unlocked, the pulse stops only if confidence stays below stop_confidence this long "
      "(a dip, or pressing l to unlock, never stops it).", "Pulse", 0, 60, 0.5),
    P("pulse.idle_stop_s", float, 6.0, "Pulse stop after",
      "Stop pulsing after this much silence (seconds).", "Pulse", 0.5, 36000, 0.5),
    P("pulse.phase_gain", float, 0.3, "Phase pull",
      "How hard the pulse leans toward your on-beat notes (0-1).", "Pulse", 0, 1, 0.05),
    P("pulse.max_nudge", float, 0.04, "Steadiness",
      "The pulse moves toward your beat by at most this share of a beat per beat, so it "
      "follows without lurching (0 = at once).", "Pulse", 0, 0.5, 0.01),
    P("pulse.max_tempo_step", float, 0.02, "Tempo easing",
      "Its beat spacing changes by at most this share per beat (0 = at once).", "Pulse", 0, 0.5, 0.01),
    P("pulse.timing_ms", float, 0.0, "Bass lay-back",
      "Each bass note up to this many ms behind the beat (humanize).", "Pulse", 0, 60, 1),
    P("pulse.velocity_spread", int, 0, "Bass velocity spread",
      "Each bass note up to this much softer or louder.", "Pulse", 0, 40, 1),
    P("pulse.movement", float, 0.8, "Bass movement",
      "0 = the root on every beat; higher = bass shapes more often: fifths, thirds, octaves "
      "and a step into the next bar.", "Pulse", 0, 1, 0.05),
    P("pulse.rhythm", float, 0.5, "Bass rhythm variety",
      "0 = a note on every beat; higher = some stretches in half time (the strong beats only), "
      "some in double time (eighths) and now and then a triplet into the next bar. Each "
      "lasts as long as a bass shape.", "Pulse", 0, 1, 0.05),
    P("pulse.fast_bpm", float, 170.0, "Fast tempo",
      "Above this tempo the bass leans to half time and plays no double time or triplets.",
      "Pulse", 60, 320, 5),
    P("pulse.shape_bars", int, 2, "Bass shape length",
      "Repeat each bass shape for this many bars before choosing another.", "Pulse", 1, 16, 1),
    P("pulse.hint_window", float, 0.15, "On-beat window",
      "Only notes within this fraction of a beat from the pulse nudge it.", "Pulse", 0, 0.5, 0.01),
    P("pulse.note_length_s", float, 0.2, "Pulse note length", "Seconds.", "Pulse", 0.01, 2, 0.01),

    # ---- drums -----------------------------------------------------------------
    P("drums.enabled", bool, False, "Drums on", "Play a drum pattern on the beat (General MIDI).",
      "Drums", primary=True),
    P("drums.feel", float, 0.4, "Drums feel",
      "Algorithmic (0: the pattern exactly, straight, even) to humanize (1: follows your swing and dynamics, more ghost notes, small timing and velocity variation).",
      "Drums", 0, 1, 0.01, primary=True),
    P("drums.channel", int, 10, "Drums channel", "MIDI channel (1-16); GM drums are on 10.",
      "Drums", 1, 16, 1, live=False),
    P("drums.pattern", str, "basic", "Pattern",
      "Which pattern (see `accompanist params`: patterns/NAME.toml; add your own in ./patterns).",
      "Drums", choices=("basic", "halftime", "soft", "sparse", "swing", "march", "waltz",
                        "five", "six-eight", "seven-322"),
      check=_check_pattern),
    P("drums.velocity", int, 70, "Drums velocity", "Velocity of a normal hit (x).", "Drums", 1, 127, 1),
    P("drums.accent", int, 25, "Drums accent", "Extra velocity for an accent (X).", "Drums", 0, 127, 1),
    P("drums.ghost", float, 0.45, "Ghost level", "A ghost note (g) at this share of a hit.",
      "Drums", 0.05, 1, 0.05),
    P("drums.swing", float, 0.0, "Swing",
      "Delay every second step by this share of a step (0 = straight, 0.33 = triplet swing).",
      "Drums", 0, 0.5, 0.01),
    P("drums.note_length_s", float, 0.1, "Drum note length", "Seconds.", "Drums", 0.01, 1, 0.01),
    P("drums.timing_ms", float, 0.0, "Drums timing spread",
      "Each hit up to this many ms early or late (humanize).", "Drums", 0, 40, 1),
    P("drums.velocity_spread", int, 0, "Drums velocity spread",
      "Each hit up to this much softer or louder.", "Drums", 0, 40, 1),
    P("drums.dynamics", float, 0.5, "Drum dynamics",
      "0 = even; higher = the drums follow your loudness more, lift when you play busily and "
      "drop back when you rest, soften the off-beats, and mark each phrase (a crescendo, "
      "a fill, a crash).", "Drums", 0, 1, 0.05),
    P("drums.phrase_bars", int, 8, "Phrase length",
      "Bars per phrase: the drums build over it, may fill at its end and crash on the next 1 "
      "(0 = no phrases).", "Drums", 0, 32, 1),
    P("drums.count_in_note", int, 37, "Count-in click",
      "Drum note for the chart count-in clicks (37 = side stick, 75 = claves), on the drums "
      "channel.", "Drums", 0, 127, 1),

    # ---- percussion (a second player: latin hand percussion) ------------------
    P("percussion.enabled", bool, False, "Percussion on",
      "A second percussionist (congas, clave, shaker, bell) with the drums (General MIDI "
      "percussion notes, on its own channel).", "Percussion", primary=True),
    P("percussion.feel", float, 0.4, "Percussion feel",
      "Algorithmic (0: the pattern exactly, even) to humanize (1: follows your dynamics, more "
      "ghost notes, small timing and velocity variation).", "Percussion", 0, 1, 0.01,
      primary=True),
    P("percussion.channel", int, 11, "Percussion channel",
      "MIDI channel (1-16): a second drum or percussion kit.", "Percussion", 1, 16, 1, live=False),
    P("percussion.pattern", str, "latin", "Percussion pattern",
      "Its pattern in 4 (in 3, 5, 6 and 7 it plays latin-waltz, latin-five, bembe and "
      "latin-seven).", "Percussion",
      choices=("latin", "latin-waltz", "latin-five", "bembe", "latin-seven"), check=_check_pattern),
    P("percussion.velocity", int, 55, "Percussion velocity", "Velocity of a normal hit (x).",
      "Percussion", 1, 127, 1),
    P("percussion.accent", int, 20, "Percussion accent", "Extra velocity for an accent (X).",
      "Percussion", 0, 127, 1),
    P("percussion.ghost", float, 0.4, "Percussion ghost level",
      "A ghost note (g) at this share of a hit.", "Percussion", 0.05, 1, 0.05),
    P("percussion.swing", float, 0.0, "Percussion swing",
      "Delay every second step by this share of a step (the swing heard overrides it).",
      "Percussion", 0, 0.5, 0.01),
    P("percussion.note_length_s", float, 0.1, "Percussion note length", "Seconds.",
      "Percussion", 0.01, 1, 0.01),
    P("percussion.timing_ms", float, 0.0, "Percussion timing spread",
      "Each hit up to this many ms early or late (humanize).", "Percussion", 0, 40, 1),
    P("percussion.velocity_spread", int, 0, "Percussion velocity spread",
      "Each hit up to this much softer or louder.", "Percussion", 0, 40, 1),
    P("percussion.presence", float, 0.3, "Percussion: how often",
      "The percussion plays in spells of percussion.spell_bars bars: this share of them "
      "(more likely when it's quiet all round). 1 = all the time.", "Percussion", 0, 1, 0.05),
    P("percussion.spell_bars", int, 4, "Percussion spell", "Bars per spell (on or off).",
      "Percussion", 1, 32, 1),
    P("percussion.spotlight", float, 0.3, "Percussion in the gaps",
      "How far the percussion steps forward when it's quiet all round (you and the answer "
      "resting), like the pad's swells: 0 = never; 1 = up to 80% louder.", "Percussion",
      0, 1, 0.05),
    P("percussion.dynamics", float, 0.5, "Percussion dynamics",
      "0 = even; higher = follows your loudness more, lifts when you play busily, softer "
      "off-beats.", "Percussion", 0, 1, 0.05),

    # ---- piano (answers you with arpeggios, on the beat) -----------------------
    P("piano.enabled", bool, False, "Piano on",
      "After your phrase (and the guitar's answer), the piano plays an arpeggio of the chord, "
      "strictly on the beat, following your phrase's direction.", "Piano", primary=True),
    P("piano.feel", float, 0.4, "Piano feel",
      "Algorithmic (0: answers every pause, straight eighths, even) to humanize (1: not every "
      "pause, triplets now and then, small timing and velocity variation).", "Piano", 0, 1,
      0.01, primary=True),
    P("piano.channel", int, 5, "Piano channel", "MIDI channel (1-16).", "Piano", 1, 16, 1,
      live=False),
    P("piano.octave", int, 4, "Piano octave", "Where its arpeggios start (4 = middle C up).",
      "Piano", 2, 6, 1),
    P("piano.range_octaves", int, 2, "Piano range", "How many octaves its arpeggios span.",
      "Piano", 1, 4, 1),
    P("piano.velocity", int, 70, "Piano velocity", "Its normal velocity (follows your loudness).",
      "Piano", 1, 127, 1),
    P("piano.share", float, 0.35, "Piano: turns from the guitar",
      "Share of your phrase endings the piano answers instead of the guitar.", "Piano",
      0, 1, 0.05),
    P("piano.chance", float, 0.5, "Piano: how often in the gaps",
      "Share of the gaps (quiet all round, after the guitar) the piano fills with an "
      "arpeggio; in the others the pad swells.", "Piano", 0, 1, 0.05),
    P("piano.subdivision", str, "auto", "Piano rhythm",
      "Notes per beat: eighths, triplets, quarters, or auto (eighths, triplets now and then, "
      "quarters above piano.fast_bpm).", "Piano",
      choices=("auto", "eighths", "triplets", "quarters")),
    P("piano.fast_bpm", float, 170.0, "Piano: fast tempo",
      "Above this tempo, auto plays quarter notes.", "Piano", 60, 320, 5),
    P("piano.max_beats", int, 4, "Piano: longest answer", "In beats.", "Piano", 1, 16, 1),
    P("piano.variety", float, 0.3, "Piano variety",
      "How often auto plays triplets instead of eighths (0 = never).", "Piano", 0, 1, 0.05),
    P("piano.timing_ms", float, 0.0, "Piano timing spread",
      "Each note up to this many ms early or late (humanize; the first stays on the beat).",
      "Piano", 0, 30, 1),
    P("piano.velocity_spread", int, 0, "Piano velocity spread",
      "Each note up to this much softer or louder.", "Piano", 0, 40, 1),

    # ---- groove (meter, downbeat, feel) ----------------------------------------
    P("groove.auto", bool, True, "Hear the groove",
      "Hear the meter (in 3 or in 4), where 1 is, and straight vs swing from your playing; "
      "bass, drums and pad follow it (not with a chart).", "Groove"),
    P("groove.auto_drums", bool, True, "Drums follow the groove",
      "Switch the drum pattern if yours does not fit the meter heard (e.g. waltz in 3), and "
      "swing the drums as you swing.", "Groove"),
    P("groove.downbeat_accent", int, 15, "Downbeat accent",
      "Extra velocity on 1 (bass and drums) once the groove is heard clearly.", "Groove", 0, 60, 1),
    P("groove.confident_at", float, 0.5, "Sure at",
      "The groove counts as heard clearly from this confidence (0-1).", "Groove", 0, 1, 0.05),
    P("groove.count_wait", float, 0.15, "Count-off wait",
      "A count-off is over when the next tap is this share of a beat late; the band then comes "
      "in on 1 (that late).", "Groove", 0.05, 1, 0.05),
    P("groove.window_beats", int, 24, "Groove memory",
      "Beats of your playing the groove is judged on: a multiple of 12, so it holds whole bars "
      "of both 3 and 4 (24 = 6 bars of 4 or 8 of 3).", "Groove", 12, 96, 12,
      check=lambda v: _multiple_of(v, 12)),
    P("groove.min_notes", int, 12, "Groove needs",
      "Notes needed before a groove is named.", "Groove", 4, 100, 1),
    P("groove.switch_margin", float, 0.15, "Groove stickiness",
      "A different meter or downbeat must fit this much better...", "Groove", 0, 1, 0.01),
    P("groove.hold_s", float, 3.0, "Groove switch after",
      "...for this long before the band changes to it.", "Groove", 0, 30, 0.5),
    P("groove.swing_threshold", float, 0.12, "Swing from",
      "Swing amount (0 = straight, 0.33 = triplet) from which the feel counts as swing.",
      "Groove", 0, 0.5, 0.01),

    # ---- response (call and response) -----------------------------------------
    P("response.enabled", bool, False, "Answer your phrases",
      "When you pause after a phrase, answer it with a short line on its own channel.",
      "Response", primary=True),
    P("response.feel", float, 0.6, "Response feel",
      "Algorithmic (0: an exact, on-the-grid echo of your last phrase, every pause) to humanize (1: curated earlier phrases, sometimes varied, in your own timing, not every pause, gives way partly).",
      "Response", 0, 1, 0.01, primary=True),
    P("response.channel", int, 4, "Response channel", "MIDI channel (1-16), e.g. a guitar track.",
      "Response", 1, 16, 1, live=False),
    P("response.octave", int, None, "Response octave",
      "Unset: the answer is in your register. A number (1-7): centred around that octave.",
      "Response", 1, 7, 1, nullable=True),
    P("response.velocity", int, 90, "Response loudness",
      "The answer's normal velocity; your phrase's accents and the dynamics shape it.",
      "Response", 1, 127, 1),
    P("response.yield_to_you", float, 1.0, "Give way to you",
      "When you play during an answer: 1 = it stops at once, 0 = it finishes over you; in "
      "between it keeps that share of its remaining notes, softer.", "Response", 0, 1, 0.05,
      bool_as_number=True),
    P("response.gap_beats", float, 0.75, "Answer after",
      "Answer once you have paused this many beats...", "Response", 0.25, 8, 0.25),
    P("response.min_gap_s", float, 0.4, "...but at least",
      "...and at least this many seconds.", "Response", 0.1, 3, 0.05),
    P("response.min_notes", int, 2, "Phrase at least",
      "Only answer phrases of at least this many notes.", "Response", 1, 16, 1),
    P("response.max_notes", int, 6, "Answer at most",
      "The answer uses at most this many notes (the end of your phrase).", "Response", 1, 16, 1),
    P("response.memory", int, 16, "Phrases remembered",
      "The answer is one of your last this-many phrases.", "Response", 1, 128, 1),
    P("response.fit", float, 0.75, "Fit to the harmony",
      "A remembered phrase is played only if at least this share of its notes belong to the "
      "chord and key of the moment; else your last phrase is echoed.", "Response", 0, 1, 0.05),
    P("response.curate", float, 1.0, "Curate",
      "How often the answer is one of your earlier phrases rather than an echo of the one you "
      "just played (0 = always an echo).", "Response", 0, 1, 0.05),
    P("response.quantize", float, 1.0, "Quantize",
      "With the beat running: 1 = the answer's rhythm on eighth notes, 0 = in your own timing.",
      "Response", 0, 1, 0.05),
    P("response.variety", float, 0.0, "Variety",
      "How often to play a variation of your last phrase (moved a scale step, inverted or "
      "reversed) instead of one of your own phrases (0 = never).", "Response", 0, 1, 0.05),
    P("response.chance", float, 0.9, "How often",
      "Share of your pauses that get an answer (0-1).", "Response", 0, 1, 0.05),

    # ---- dynamics --------------------------------------------------------------
    P("dynamics.follow", float, 0.5, "Follow your loudness",
      "How much pad and bass get louder or softer with you (0 = fixed levels).",
      "Dynamics", 0, 1, 0.05),
    P("dynamics.duck", float, 0.5, "Step back when busy",
      "How far the pad drops while you play busily (0 = never; 1 = down to pad_floor).",
      "Dynamics", 0, 1, 0.05),
    P("dynamics.pad_floor", float, 0.25, "Pad floor",
      "The quietest the pad gets (share of full expression).", "Dynamics", 0, 1, 0.05),
    P("dynamics.pad_space", float, 0.7, "Pad makes room",
      "How far the pad sits back while anyone plays (you or the answer) and swells in the "
      "quiet: 0 = as set by follow and duck; 1 = down to pad_floor while you play, full in "
      "the pauses.", "Dynamics", 0, 1, 0.05),
    P("dynamics.swell_after_s", float, 1.5, "Swell after",
      "Quiet all round (you and the answer) this long before the pad swells (seconds).",
      "Dynamics", 0, 30, 0.1),
    P("dynamics.swell_s", float, 4.0, "Swell time",
      "Seconds for the pad to swell to full in the quiet.", "Dynamics", 0.1, 30, 0.1),
    P("dynamics.recede_s", float, 0.6, "Recede time",
      "Seconds for the pad to sit back when someone plays again.", "Dynamics", 0.05, 10, 0.05),
    P("dynamics.busy_notes_per_s", float, 4.0, "Busy at",
      "Notes per second that count as fully busy.", "Dynamics", 0.5, 20, 0.5),
    P("dynamics.reference_velocity", float, 80.0, "Your normal velocity",
      "Playing at this velocity leaves the levels as set.", "Dynamics", 10, 127, 1),
    P("dynamics.memory_s", float, 3.0, "Dynamics memory",
      "How quickly the levels react to you (half-life, seconds).", "Dynamics", 0.5, 30, 0.5),

    # ---- audio input (hearing notes: sax first) --------------------------------
    P("audio.gate_db", float, -45.0, "Noise gate",
      "Quieter than this (dBFS) counts as silence.", "Audio input", -90, 0, 1),
    P("audio.min_hz", float, 60.0, "Lowest pitch",
      "Lowest fundamental to listen for (Hz): 60 covers baritone sax and low voices.",
      "Audio input", 30, 500, 1),
    P("audio.max_hz", float, 1600.0, "Highest pitch", "Highest fundamental (Hz).",
      "Audio input", 200, 4000, 10),
    P("audio.yin_threshold", float, 0.15, "Pitch clarity",
      "How clear a pitch must be to count (YIN aperiodicity; lower = stricter).",
      "Audio input (advanced)", 0.02, 0.5, 0.01),
    P("audio.cents_tolerance", float, 35.0, "In tune within",
      "A note must hold within this many cents of a semitone to be confirmed.",
      "Audio input", 5, 50, 1),
    P("audio.min_note_ms", float, 45.0, "Shortest note",
      "A pitch must hold this long to count as a note (filters slides and blips).",
      "Audio input", 10, 300, 5),
    P("audio.release_ms", float, 70.0, "Note end after",
      "This much silence (or unclear pitch) ends a note.", "Audio input", 10, 1000, 5),
    P("audio.octave_fix_ms", float, 150.0, "Octave settle",
      "An octave jump this soon after a note starts is the attack settling, not a new note "
      "(0 = off).", "Audio input", 0, 400, 10),
    P("audio.attack_db", float, 8.0, "Re-attack jump",
      "A jump in level this big on the same pitch is a new note (tonguing).",
      "Audio input", 2, 30, 0.5),
    P("audio.attack_window_ms", float, 80.0, "Re-attack window",
      "...measured against the quietest point in this long a stretch.",
      "Audio input (advanced)", 20, 400, 5),
    P("audio.level_ms", float, 10.0, "Level window",
      "Level for attack detection is measured over this many ms.", "Audio input (advanced)",
      2, 50, 1),
    P("audio.velocity_floor_db", float, -50.0, "Softest level",
      "This level (dBFS) and below plays as the softest velocity.", "Audio input", -90, 0, 1),
    P("audio.velocity_ceiling_db", float, -12.0, "Loudest level",
      "This level and above plays as velocity 127.", "Audio input", -60, 0, 1),
    P("audio.window", int, 2048, "Analysis window",
      "Samples per pitch analysis (longer = lower notes, more latency).",
      "Audio input (advanced)", 512, 8192, 256),
    P("audio.hop", int, 256, "Analysis step", "Samples between analyses.",
      "Audio input (advanced)", 64, 2048, 64),

    # ---- lock ------------------------------------------------------------------
    P("lock.auto", bool, True, "Auto lock",
      "Lock the groove by itself once the tempo has been clear for a while.", "Groove lock"),
    P("lock.confidence", float, 0.8, "Lock confidence",
      "Auto lock needs the tempo confidence at or above this...", "Groove lock", 0, 1, 0.05),
    P("lock.after_s", float, 25.0, "Lock after",
      "...for this many seconds in a row.", "Groove lock", 1, 600, 1),
    P("lock.tempo_rate", float, 0.1, "Tempo follow while locked",
      "While locked, the tempo follows you this much as fast as usual (0 = frozen).",
      "Groove lock", 0, 1, 0.05),
    P("lock.phase_rate", float, 0.3, "Phase follow while locked",
      "While locked, the pulse leans toward your notes this much as hard as usual (0 = rigid).",
      "Groove lock", 0, 1, 0.05),

    # ---- breaks (b, or a pedal: the band stops, you play alone) ------------------
    P("breaks.bars", int, 2, "Break length",
      "A break: from the next 1 the rhythm section stops for this many bars (the pad stays, "
      "low), then comes back in on the 1.", "Breaks", 1, 16, 1),
    P("breaks.hit", bool, True, "Hit the 1",
      "The band plays the first 1 of the break (bass, kick, crash), then stops: stop-time.",
      "Breaks"),
    P("breaks.pad_level", float, 0.15, "Pad in a break",
      "The pad's level during a break (share of full expression): recessed, no swells.",
      "Breaks", 0, 1, 0.05),

    # ---- ending (f, or a pedal: finish the song) --------------------------------
    P("ending.ring_s", float, 4.0, "Last chord rings",
      "The last chord (on the 1 after you ask to finish) rings this long, fading out (seconds).",
      "Ending", 0.5, 30, 0.5),
    P("ending.fill", bool, True, "Fill into the end",
      "The drums play a short fill on the last beat before the final 1.", "Ending"),
    P("ending.level", float, 0.8, "Last chord level",
      "The pad's level on the last chord, before it fades (share of full expression).",
      "Ending", 0, 1, 0.05),

    # ---- song (a song file's own settings; see songs/) ---------------------------
    P("song.title", str, None, "Song", "The song's name (shown when it is loaded).", "Song",
      nullable=True, live=False),
    P("song.tempo", float, None, "Song tempo",
      "Starting the song (s, or a pedal) counts the band in at this tempo.", "Song", 20, 300, 1,
      nullable=True),
    P("song.count", int, None, "Song meter",
      "Beats per bar for the count-in and the band: 3 = waltz, 4, 5 = 3+2, 6 = 6/8, 7 = 3+2+2.",
      "Song", 3, 7, 1, nullable=True),

    # ---- pedal (switches with a tap and a hold action) --------------------------
    P("pedal.hold_s", float, 0.6, "Hold time",
      "A switch with a tap and a hold action ([controls] \"cc:80\" = { tap = ..., hold = ... }): "
      "held this long, it does its hold action instead.", "Controls", 0.2, 3, 0.05),

    # ---- panic ----------------------------------------------------------------
    P("panic.cc", int, None, "Panic CC",
      "This controller (value >= 64) on any input = panic. Same as mapping it in [controls].",
      "Controls", 0, 127, 1, nullable=True, live=False),
]

REGISTRY: dict[str, Param] = {p.key: p for p in PARAMS}
SECTIONS: tuple[str, ...] = tuple(dict.fromkeys(p.section for p in PARAMS))


def get(key: str) -> Param:
    try:
        return REGISTRY[key]
    except KeyError:
        raise KeyError(f"unknown parameter '{key}'") from None


def section_params(section: str) -> list[Param]:
    return [p for p in PARAMS if p.section == section]


def coerce(p: Param, value: Any) -> Any:
    """Validate one value against its Param. Returns the value in the right type.

    Raises ValueError with a readable message (no key prefix; the caller adds it).
    """
    if value is None:
        if p.nullable:
            return None
        raise ValueError("a value is required")
    if p.type is bool:
        if not isinstance(value, bool):
            raise ValueError(f"must be true or false, got {value!r}")
    elif p.type is int:
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            else:
                raise ValueError(f"must be a whole number, got {value!r}")
    elif p.type is float:
        if isinstance(value, bool) and p.bool_as_number:
            value = 1.0 if value else 0.0
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"must be a number, got {value!r}")
        value = float(value)
    elif p.type is str:
        if not isinstance(value, str):
            raise ValueError(f"must be text, got {value!r}")
    if p.min is not None and p.max is not None and not (p.min <= value <= p.max):
        lo, hi = (int(p.min), int(p.max)) if p.type is int else (p.min, p.max)
        raise ValueError(f"must be {lo}-{hi}, got {value!r}")
    if p.check is not None:
        p.check(value)
    elif p.choices is not None and value not in p.choices:
        raise ValueError(f"must be one of {list(p.choices)}, got {value!r}")
    return value


def schema() -> list[dict]:
    return [p.schema() for p in PARAMS]
