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
        }


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

    # ---- tempo ----------------------------------------------------------------
    P("tempo.initial_bpm", float, 70.0, "Starting tempo",
      "The reading before there is enough playing (min_onsets notes) to go on.",
      "Tempo", 20, 300, 1, live=False),
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
      "Which harmony model drives the pad: 'drone' (root + fifth, third if played) or "
      "'modal' (chords from a key and mode, following what you play).",
      "Harmony", choices=("drone", "modal"), live=False),
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
    P("pad.enabled", bool, True, "Pad on", "Play the sustained pad.", "Pad"),
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
    P("pad.overlap_s", float, 0.25, "Pad overlap",
      "Old notes ring this long under a new chord.", "Pad", 0, 5, 0.05),
    P("pad.idle_release_s", float, 20.0, "Pad release after",
      "Release the pad after this much silence (seconds).", "Pad", 1, 36000, 1),

    # ---- pulse ----------------------------------------------------------------
    P("pulse.enabled", bool, True, "Pulse on", "Play a soft note on every beat.", "Pulse"),
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
    P("pulse.idle_stop_s", float, 6.0, "Pulse stop after",
      "Stop pulsing after this much silence (seconds).", "Pulse", 0.5, 36000, 0.5),
    P("pulse.phase_gain", float, 0.3, "Phase pull",
      "How hard the pulse leans toward your on-beat notes (0-1).", "Pulse", 0, 1, 0.05),
    P("pulse.hint_window", float, 0.15, "On-beat window",
      "Only notes within this fraction of a beat from the pulse nudge it.", "Pulse", 0, 0.5, 0.01),
    P("pulse.note_length_s", float, 0.2, "Pulse note length", "Seconds.", "Pulse", 0.01, 2, 0.01),

    # ---- dynamics --------------------------------------------------------------
    P("dynamics.follow", float, 0.5, "Follow your loudness",
      "How much pad and bass get louder or softer with you (0 = fixed levels).",
      "Dynamics", 0, 1, 0.05),
    P("dynamics.duck", float, 0.5, "Step back when busy",
      "How far the pad drops while you play busily (0 = never; 1 = down to pad_floor).",
      "Dynamics", 0, 1, 0.05),
    P("dynamics.pad_floor", float, 0.25, "Pad floor",
      "The quietest the pad gets (share of full expression).", "Dynamics", 0, 1, 0.05),
    P("dynamics.busy_notes_per_s", float, 4.0, "Busy at",
      "Notes per second that count as fully busy.", "Dynamics", 0.5, 20, 0.5),
    P("dynamics.reference_velocity", float, 80.0, "Your normal velocity",
      "Playing at this velocity leaves the levels as set.", "Dynamics", 10, 127, 1),
    P("dynamics.memory_s", float, 3.0, "Dynamics memory",
      "How quickly the levels react to you (half-life, seconds).", "Dynamics", 0.5, 30, 0.5),

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
