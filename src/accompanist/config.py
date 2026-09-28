"""Configuration: everything device- or player-specific lives here, never in code.

Channels are 1-16 in the config file (as in Logic) and 0-15 internally (as in MIDI).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_FLATS = {"DB": 1, "EB": 3, "GB": 6, "AB": 8, "BB": 10}

# Roles an input channel can play. Only note_source exists in V0; the audio
# roles are declared so config files written today stay valid later.
VALID_ROLES = ("note_source",)
PLANNED_ROLES = ("pitch_contour", "voice")


class ConfigError(ValueError):
    pass


def parse_root(value: Any) -> Optional[int]:
    """'auto' -> None (track it); 'D', 'F#', 'Bb' -> pitch class 0-11."""
    v = str(value).strip().upper()
    if v in ("AUTO", ""):
        return None
    if v in _FLATS:
        return _FLATS[v]
    if v in NOTE_NAMES:
        return NOTE_NAMES.index(v)
    raise ConfigError(f"harmony.root: '{value}' is not 'auto' or a note name like D, F#, Bb")


def note_name(note: int) -> str:
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


@dataclass
class InputCfg:
    port: str                       # substring of the MIDI input port name
    role: str = "note_source"
    name: str = ""                  # label for the status line
    channel: Optional[int] = None   # 1-16, or omit for all channels


@dataclass
class OutputCfg:
    # Default: create a virtual MIDI source that Logic sees automatically.
    virtual_name: Optional[str] = "Accompanist"
    # Or send to an existing port (e.g. "IAC Driver Bus 1"); this overrides virtual_name.
    port: Optional[str] = None


@dataclass
class TempoCfg:
    initial_bpm: float = 70.0   # the estimate before there is enough playing to go on
    min_bpm: float = 40.0
    max_bpm: float = 180.0
    prior_bpm: float = 80.0     # picks the beat "octave" when the playing is ambiguous (see README)
    prior_sigma_oct: float = 0.5  # how strongly: width of the prior, in octaves
    window_s: float = 30.0      # how much recent playing the estimate looks at
    halflife_s: float = 12.0    # older onsets count less: halve their weight every this many s
    alpha: float = 0.25         # how fast the estimate follows you (0-1); higher = jumpier
    switch_margin: float = 0.1  # a different tempo peak must score this much better to take over
    switch_hold_s: float = 4.0  # ... and keep scoring better for this long
    score_memory: float = 0.5   # 0-1: blend each tempo score curve with the previous one
    peak_width: float = 0.06    # tempi within this fraction of the estimate count as the same peak
    min_onsets: int = 8         # fewer onsets in the window: keep the previous estimate
    update_s: float = 1.0       # recompute this often
    confidence_scale: float = 0.3  # peak-above-median score that counts as full confidence
    bin_s: float = 0.01         # onset signal resolution
    smooth_s: float = 0.06      # Gaussian smoothing of the onset signal (timing slop)
    bpm_step: float = 0.5       # resolution of the bpm search
    min_ioi_s: float = 0.06     # closer onsets count as one (chords, grace notes)
    # Accepted so older config files still load; unused by the windowed estimator.
    tolerance: float = 0.3
    max_gap_beats: float = 6.0


@dataclass
class HarmonyCfg:
    root: str = "auto"          # "auto", or lock the tonic: "D", "F#", "Bb"
    half_life_s: float = 12.0   # how long the ear remembers what you played
    third_threshold: float = 0.35  # third must reach this share of the root's weight
    switch_margin: float = 1.25    # a new root must beat the old by this factor


@dataclass
class PadCfg:
    enabled: bool = True
    channel: int = 1
    octave: int = 3
    velocity: int = 55
    lag_beats: float = 4.0         # a new harmony must persist this long before we follow
    min_change_beats: float = 8.0  # never change chords faster than this
    overlap_s: float = 0.25        # old notes ring this long under the new chord
    idle_release_s: float = 20.0   # release the pad after this much silence


@dataclass
class PulseCfg:
    enabled: bool = True
    channel: int = 2
    octave: int = 2
    velocity: int = 45
    accent: int = 25
    beats_per_bar: int = 4
    min_confidence: float = 0.5    # start pulsing once the tempo estimate is this sure
    idle_stop_s: float = 6.0       # stop pulsing after this much silence
    phase_gain: float = 0.3        # how hard the pulse nudges toward your onsets
    hint_window: float = 0.15      # only onsets this close to a beat (fraction of it) nudge it
    note_length_s: float = 0.2


@dataclass
class PanicCfg:
    cc: Optional[int] = None  # optional: this controller (value >= 64) on any input = panic


@dataclass
class Config:
    output: OutputCfg = field(default_factory=OutputCfg)
    inputs: list[InputCfg] = field(default_factory=list)
    tempo: TempoCfg = field(default_factory=TempoCfg)
    harmony: HarmonyCfg = field(default_factory=HarmonyCfg)
    pad: PadCfg = field(default_factory=PadCfg)
    pulse: PulseCfg = field(default_factory=PulseCfg)
    panic: PanicCfg = field(default_factory=PanicCfg)

    @property
    def root_pc(self) -> Optional[int]:
        return parse_root(self.harmony.root)


def _section(cls, data: Optional[dict], name: str):
    data = dict(data or {})
    allowed = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"[{name}] unknown key(s) {sorted(unknown)}; allowed: {sorted(allowed)}")
    try:
        return cls(**data)
    except TypeError as e:
        raise ConfigError(f"[{name}] {e}") from e


def _check_channel(ch: Optional[int], where: str) -> None:
    if ch is not None and not (isinstance(ch, int) and 1 <= ch <= 16):
        raise ConfigError(f"{where}: channel must be 1-16, got {ch!r}")


def from_dict(d: Optional[dict]) -> Config:
    d = dict(d or {})
    allowed = {"output", "inputs", "tempo", "harmony", "pad", "pulse", "panic"}
    unknown = set(d) - allowed
    if unknown:
        raise ConfigError(f"unknown top-level key(s) {sorted(unknown)}; allowed: {sorted(allowed)}")

    out_data = dict(d.get("output") or {})
    if "port" in out_data and "virtual_name" not in out_data:
        out_data["virtual_name"] = None  # naming a real port means: don't create a virtual one
    output = _section(OutputCfg, out_data, "output")
    if not output.port and not output.virtual_name:
        raise ConfigError("[output] needs either virtual_name or port")

    inputs = []
    for i, item in enumerate(d.get("inputs") or []):
        inp = _section(InputCfg, item, f"inputs[{i}]")
        if inp.role in PLANNED_ROLES:
            raise ConfigError(
                f"inputs[{i}]: role '{inp.role}' is planned but not implemented yet "
                f"(available now: {', '.join(VALID_ROLES)})"
            )
        if inp.role not in VALID_ROLES:
            raise ConfigError(f"inputs[{i}]: unknown role '{inp.role}'; use one of {VALID_ROLES}")
        _check_channel(inp.channel, f"inputs[{i}]")
        inputs.append(inp)

    cfg = Config(
        output=output,
        inputs=inputs,
        tempo=_section(TempoCfg, d.get("tempo"), "tempo"),
        harmony=_section(HarmonyCfg, d.get("harmony"), "harmony"),
        pad=_section(PadCfg, d.get("pad"), "pad"),
        pulse=_section(PulseCfg, d.get("pulse"), "pulse"),
        panic=_section(PanicCfg, d.get("panic"), "panic"),
    )
    _check_channel(cfg.pad.channel, "[pad]")
    _check_channel(cfg.pulse.channel, "[pulse]")
    t = cfg.tempo
    if not (0 < t.min_bpm < t.max_bpm):
        raise ConfigError("[tempo] need 0 < min_bpm < max_bpm")
    if not (t.min_bpm <= t.initial_bpm <= t.max_bpm):
        raise ConfigError("[tempo] initial_bpm must lie between min_bpm and max_bpm")
    if not (t.min_bpm <= t.prior_bpm <= t.max_bpm):
        raise ConfigError("[tempo] prior_bpm must lie between min_bpm and max_bpm")
    cfg.root_pc  # validates harmony.root
    return cfg


def load(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p} (copy config.example.toml to start)")
    with open(p, "rb") as f:
        return from_dict(tomllib.load(f))
