"""Configuration: everything device- or player-specific lives here, never in code.

The tunables themselves are declared in params.py; the section classes below
(TempoCfg, PadCfg, ...) are generated from that registry, so a parameter exists
in exactly one place.

Layering (later wins): registry defaults < preset (presets/NAME.toml) <
config.toml < live overrides (Controller.set_param).

Channels are 1-16 in the config file (as in Logic) and 0-15 internally (as in MIDI).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, make_dataclass
from pathlib import Path
from typing import Any, Optional

from . import params as registry

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_FLATS = {"DB": 1, "EB": 3, "GB": 6, "AB": 8, "BB": 10}

# Roles an input channel can play. Only note_source exists in V0; the audio
# roles are declared so config files written today stay valid later.
VALID_ROLES = ("note_source", "control", "body")   # control: a pedal: commands only;
                                                   # body: a headband: heartbeat, gestures
PLANNED_ROLES = ("pitch_contour", "voice")

# Things a MIDI controller (or a key, or a UI) can trigger. See Controller.
ACTIONS = ("panic", "resume", "panic_toggle", "lock", "unlock", "lock_toggle", "chord_hold",
           "chord_release",
           "chord_toggle", "song_start", "chart_restart", "tap_tempo", "finish", "break",
           "song_next", "song_prev")

BUILTIN_PRESETS = Path(__file__).parent / "presets"
USER_PRESETS = Path("presets")
BUILTIN_SONGS = Path(__file__).parent / "songs"
USER_SONGS = Path("songs")
# Sections a preset may set. Ports and inputs are per-machine, so presets never touch them.
PRESET_SECTIONS = tuple(s for s in registry.SECTIONS if s not in ("output", "panic"))


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


def _section_class(section: str):
    fields = [(p.name, Any, field(default=p.default)) for p in registry.section_params(section)]
    return make_dataclass(f"{section.capitalize()}Cfg", fields)


OutputCfg = _section_class("output")
TempoCfg = _section_class("tempo")
HarmonyCfg = _section_class("harmony")
PadCfg = _section_class("pad")
PulseCfg = _section_class("pulse")
LockCfg = _section_class("lock")
DynamicsCfg = _section_class("dynamics")
DrumsCfg = _section_class("drums")
PercussionCfg = _section_class("percussion")
PianoCfg = _section_class("piano")
AudioCfg = _section_class("audio")
ResponseCfg = _section_class("response")
GrooveCfg = _section_class("groove")
SongCfg = _section_class("song")
EndingCfg = _section_class("ending")
StartCfg = _section_class("start")
BreaksCfg = _section_class("breaks")
InterludeCfg = _section_class("interlude")
MixCfg = _section_class("mix")
LibraryCfg = _section_class("library")
BodyCfg = _section_class("body")
UiCfg = _section_class("ui")
PanicCfg = _section_class("panic")
PedalCfg = _section_class("pedal")
SECTION_CLASSES = {"output": OutputCfg, "tempo": TempoCfg, "harmony": HarmonyCfg,
                   "pad": PadCfg, "pulse": PulseCfg, "lock": LockCfg, "dynamics": DynamicsCfg,
                   "drums": DrumsCfg, "percussion": PercussionCfg, "piano": PianoCfg, "audio": AudioCfg, "response": ResponseCfg,
                   "groove": GrooveCfg, "song": SongCfg, "ending": EndingCfg, "start": StartCfg, "breaks": BreaksCfg, "interlude": InterludeCfg, "mix": MixCfg, "library": LibraryCfg, "body": BodyCfg, "ui": UiCfg, "pedal": PedalCfg, "panic": PanicCfg}
assert set(SECTION_CLASSES) == set(registry.SECTIONS), "every registry section needs a class"


@dataclass
class InputCfg:
    port: Optional[str] = None      # substring of the MIDI input port name, or...
    role: str = "note_source"
    name: str = ""                  # label for the status line
    channel: Optional[int] = None   # MIDI: 1-16, or omit for all channels
    audio: Optional[str] = None     # ...substring of an audio input device's name
    audio_channel: int = 1          # audio: which input of the interface (1-based)
    muse: Optional[str] = None      # ...or a Muse headband's Bluetooth address (body signals)

    @property
    def is_audio(self) -> bool:
        return self.audio is not None

    @property
    def is_body(self) -> bool:
        return self.muse is not None


@dataclass
class Config:
    output: Any = field(default_factory=OutputCfg)
    inputs: list[InputCfg] = field(default_factory=list)
    tempo: Any = field(default_factory=TempoCfg)
    harmony: Any = field(default_factory=HarmonyCfg)
    pad: Any = field(default_factory=PadCfg)
    pulse: Any = field(default_factory=PulseCfg)
    lock: Any = field(default_factory=LockCfg)
    dynamics: Any = field(default_factory=DynamicsCfg)
    drums: Any = field(default_factory=DrumsCfg)
    percussion: Any = field(default_factory=PercussionCfg)
    piano: Any = field(default_factory=PianoCfg)
    audio: Any = field(default_factory=AudioCfg)
    response: Any = field(default_factory=ResponseCfg)
    groove: Any = field(default_factory=GrooveCfg)
    song: Any = field(default_factory=SongCfg)
    ending: Any = field(default_factory=EndingCfg)
    start: Any = field(default_factory=StartCfg)
    breaks: Any = field(default_factory=BreaksCfg)
    interlude: Any = field(default_factory=InterludeCfg)
    mix: Any = field(default_factory=MixCfg)
    library: Any = field(default_factory=LibraryCfg)
    body: Any = field(default_factory=BodyCfg)
    ui: Any = field(default_factory=UiCfg)
    pedal: Any = field(default_factory=PedalCfg)
    panic: Any = field(default_factory=PanicCfg)
    controls: dict = field(default_factory=dict)   # (kind, number) -> ControlCfg
    program_bank: int = 0           # program changes folded into banks of this size (0 = off)
    preset: Optional[str] = None

    @property
    def root_pc(self) -> Optional[int]:
        return parse_root(self.harmony.root)

    def section(self, name: str):
        return getattr(self, name)


def _section(name: str, data: Any):
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"[{name}] must be a table of settings")
    known = {p.name: p for p in registry.section_params(name)}
    unknown = set(data) - set(known)
    if unknown:
        allowed = sorted(k for k, p in known.items() if not p.deprecated)
        raise ConfigError(f"[{name}] unknown key(s) {sorted(unknown)}; allowed: {allowed}")
    values = {}
    for k, v in data.items():
        try:
            values[k] = registry.coerce(known[k], v)
        except ValueError as e:
            raise ConfigError(f"[{name}] {k}: {e}") from None
    return SECTION_CLASSES[name](**values)


@dataclass(frozen=True)
class ControlCfg:
    target: str                     # an action (ACTIONS) or a live parameter key
    latching: bool = False          # a switch that toggles 127/0 on each press
    hold: Optional[str] = None      # an action for holding the switch (pedal.hold_s); target = tap


CONTROL_KINDS = ("cc", "pc", "note")


def control_key(k: Any) -> tuple[str, int]:
    """'85' or 'cc:85' -> ('cc', 85); 'pc:3' -> ('pc', 3); 'note:36' -> ('note', 36)."""
    s = str(k).strip().lower()
    kind, _, num = s.partition(":") if ":" in s else ("cc", "", s)
    if kind not in CONTROL_KINDS or not num.isdigit():
        raise ConfigError(f"[controls] '{k}' is not a controller: use a number (a CC), or "
                          f"\"cc:N\", \"pc:N\" (program change) or \"note:N\"")
    n = int(num)
    if not 0 <= n <= 127:
        raise ConfigError(f"[controls] '{k}': the number must be 0-127")
    return kind, n


def _controls(data: Any) -> tuple[dict[tuple[str, int], ControlCfg], int]:
    """[controls] -> ({(kind, number): ControlCfg}, program_bank)."""
    if data is None:
        return {}, 0
    if not isinstance(data, dict):
        raise ConfigError("[controls] must map controllers to actions or parameters")
    data = dict(data)
    bank = data.pop("program_bank", 0)
    if isinstance(bank, bool) or not isinstance(bank, int) or not 0 <= bank <= 128:
        raise ConfigError("[controls] program_bank must be a whole number (0 = off, e.g. 4)")
    out = {}
    for k, v in data.items():
        kind, n = control_key(k)
        latching = False
        if isinstance(v, dict) and "tap" in v:      # a tap action and a hold action
            extra = set(v) - {"tap", "hold"}
            bad = [a for a in (v.get("tap"), v.get("hold")) if a is not None and a not in ACTIONS]
            if extra or bad:
                raise ConfigError(f"[controls] {k}: use {{ tap = \"...\", hold = \"...\" }} "
                                  f"with actions from {list(ACTIONS)}")
            if kind not in ("cc", "note"):
                raise ConfigError(f"[controls] {k}: tap and hold needs a switch that also sends "
                                  f"its release: a momentary CC or a note (not a program change)")
            out[(kind, n)] = ControlCfg(v["tap"], False, v.get("hold"))
            continue
        if isinstance(v, dict):
            extra = set(v) - {"action", "param", "latching"}
            if extra or ("action" in v) == ("param" in v):
                raise ConfigError(f"[controls] {k}: use {{ action = \"...\" }} or "
                                  f"{{ param = \"...\" }}, optionally with latching = true")
            target, latching = v.get("action") or v.get("param"), bool(v.get("latching", False))
        else:
            target = v
        if target in ACTIONS:
            out[(kind, n)] = ControlCfg(target, latching)
            continue
        p = registry.REGISTRY.get(target) if isinstance(target, str) else None
        if p is None or p.deprecated:
            raise ConfigError(f"[controls] {k} = {target!r}: not an action {list(ACTIONS)} "
                              f"or a parameter (see `accompanist params`)")
        if kind != "cc":
            raise ConfigError(f"[controls] {k} = '{target}': only a CC (a knob or pedal) can "
                              f"set a parameter; switches trigger actions")
        if not p.live:
            raise ConfigError(f"[controls] {k} = '{target}': this parameter can't change while running")
        if p.type in (int, float) and (p.min is None or p.max is None):
            raise ConfigError(f"[controls] {k} = '{target}': parameter has no range to map a CC onto")
        out[(kind, n)] = ControlCfg(target, latching)
    return out, bank


def _check_channel(ch: Optional[int], where: str) -> None:
    if ch is not None and not (isinstance(ch, int) and not isinstance(ch, bool) and 1 <= ch <= 16):
        raise ConfigError(f"{where}: channel must be 1-16, got {ch!r}")


def merge(base: dict, over: dict) -> dict:
    """Deep-merge two config dicts: tables merge key by key, anything else is replaced."""
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def available_presets() -> list[str]:
    names = {p.stem for d in (BUILTIN_PRESETS, USER_PRESETS) if d.is_dir() for p in d.glob("*.toml")}
    return sorted(names)


def load_preset(name: str) -> dict:
    """A preset is a partial config: only tunable sections. ./presets/ overrides the built-ins."""
    for d in (USER_PRESETS, BUILTIN_PRESETS):
        p = d / f"{name}.toml"
        if p.is_file():
            data = _read_toml(p)
            bad = set(data) - set(PRESET_SECTIONS)
            if bad:
                raise ConfigError(f"preset '{name}' ({p}): may only set {list(PRESET_SECTIONS)}, "
                                  f"not {sorted(bad)}")
            return data
    raise ConfigError(f"unknown preset '{name}'; available: {', '.join(available_presets()) or '(none)'}")


USER_SETS = Path("sets")


def available_sets() -> list[str]:
    return sorted(p.stem for p in USER_SETS.glob("*.toml")) if USER_SETS.is_dir() else []


def load_set(name: str) -> tuple[str, list[str]]:
    """A set list (./sets/NAME.toml): (title, song names in order). Every song must exist."""
    name = resolve_name(name, available_sets(), "set")
    p = USER_SETS / f"{name}.toml"
    if not p.is_file():
        raise ConfigError(f"unknown set '{name}'; available: {', '.join(available_sets()) or '(none)'}"
                          f" (set lists go in ./sets/NAME.toml with songs = [\"...\", ...])")
    data = _read_toml(p)
    songs = data.get("songs")
    if not isinstance(songs, list) or not songs or not all(isinstance(s, str) for s in songs):
        raise ConfigError(f"set '{name}' ({p}): needs songs = [\"song-name\", ...] (at least one)")
    songs = [resolve_name(s, available_songs(), "song") for s in songs]
    missing = [s for s in songs if s not in available_songs()]
    if missing:
        raise ConfigError(f"set '{name}': no song file for {', '.join(missing)} "
                          f"(available: {', '.join(available_songs())})")
    return str(data.get("title", name)), songs


def available_songs() -> list[str]:
    names = {p.stem for d in (BUILTIN_SONGS, USER_SONGS) if d.is_dir() for p in d.glob("*.toml")}
    return sorted(names)


def resolve_name(name: str, names: list[str], what: str) -> str:
    """A song, set or preset by part of its name: 'lady' -> 'lady-sings-the-blues', if only
    one name starts with it (or else, only one contains it). Spaces count as dashes and
    case doesn't matter. Unchanged if exact or not found (the caller says what's available)."""
    if name in names:
        return name
    want = name.strip().lower().replace(" ", "-").replace("_", "-")
    for test in (lambda n: n.lower().startswith(want), lambda n: want in n.lower()):
        found = [n for n in names if test(n)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise ConfigError(f"'{name}' could be the {what} {', '.join(found)}: say a little more")
    return name


def song_path(name: str) -> Path:
    """The song's file (./songs/NAME.toml first, else the built-in example)."""
    name = resolve_name(name, available_songs(), "song")
    for d in (USER_SONGS, BUILTIN_SONGS):
        p = d / f"{name}.toml"
        if p.is_file():
            return p
    raise ConfigError(f"unknown song '{name}'; available: {', '.join(available_songs())}")


def load_song(name: str) -> dict:
    """A song file (./songs/NAME.toml, else a built-in example): a partial config like a preset,
    plus [song] title/tempo/count. A chart path in it may be relative to the song file."""
    for d in (USER_SONGS, BUILTIN_SONGS):
        p = d / f"{name}.toml"
        if p.is_file():
            data = _read_toml(p)
            bad = set(data) - set(PRESET_SECTIONS)
            if bad:
                raise ConfigError(f"song '{name}' ({p}): may only set {list(PRESET_SECTIONS)}, "
                                  f"not {sorted(bad)} (ports, inputs and controls belong in "
                                  f"config.toml)")
            song = data.setdefault("song", {})
            if isinstance(song, dict):
                song.setdefault("title", name)
            chart = (data.get("harmony") or {}).get("chart")
            if isinstance(chart, str) and not Path(chart).exists() and (p.parent / chart).exists():
                data["harmony"]["chart"] = str(p.parent / chart)
            return data
    raise ConfigError(f"unknown song '{name}'; available: "
                      f"{', '.join(available_songs()) or '(none)'} (song files go in ./songs/NAME.toml)")


def build(path: Optional[str | Path] = None, preset: Optional[str] = None,
          overrides: Optional[dict] = None, song: Optional[str] = None) -> Config:
    """The whole layering: defaults < preset < config.toml (if a path is given) < song <
    command-line overrides."""
    d: dict = {}
    if path is not None:
        if not Path(path).exists():
            raise ConfigError(f"config file not found: {path} (copy config.example.toml to start)")
        d = _read_toml(Path(path))
    if song:
        d = merge(d, load_song(song))
    return from_dict(merge(d, overrides or {}), preset)


def from_dict(d: Optional[dict], preset: Optional[str] = None) -> Config:
    """Build a Config from a config.toml-shaped dict, layered over a preset if one is named
    (argument first, else a top-level `preset = "..."` in the dict)."""
    d = dict(d or {})
    name = preset or d.pop("preset", None)
    d.pop("preset", None)
    if name is not None:
        if not isinstance(name, str):
            raise ConfigError("preset must be a name, e.g. preset = \"ambient\"")
        d = merge(load_preset(name), d)

    allowed = set(registry.SECTIONS) | {"inputs", "controls", "preset"}
    unknown = set(d) - allowed
    if unknown:
        raise ConfigError(f"unknown top-level key(s) {sorted(unknown)}; allowed: {sorted(allowed)}")

    out_data = dict(d.get("output") or {})
    if "port" in out_data and "virtual_name" not in out_data:
        out_data["virtual_name"] = None  # naming a real port means: don't create a virtual one
    output = _section("output", out_data)
    if not output.port and not output.virtual_name:
        raise ConfigError("[output] needs either virtual_name or port")

    inputs = []
    for i, item in enumerate(d.get("inputs") or []):
        if not isinstance(item, dict):
            raise ConfigError(f"inputs[{i}] must be a table ([[inputs]])")
        allowed_in = {"port", "role", "name", "channel", "audio", "audio_channel", "muse"}
        if set(item) - allowed_in:
            raise ConfigError(f"inputs[{i}] unknown key(s) {sorted(set(item) - allowed_in)}; "
                              f"allowed: {sorted(allowed_in)}")
        if sum(k in item for k in ("port", "audio", "muse")) != 1:
            raise ConfigError(f"inputs[{i}] needs one of port = \"...\" (a MIDI input), "
                              f"audio = \"...\" (an audio interface) by part of its name, or "
                              f"muse = \"...\" (a Muse headband's address)")
        if "muse" in item:
            item = {**item, "role": item.get("role", "body")}
        inp = InputCfg(**item)
        if not isinstance(inp.audio_channel, int) or isinstance(inp.audio_channel, bool) or inp.audio_channel < 1:
            raise ConfigError(f"inputs[{i}]: audio_channel must be 1 or more (the input number)")
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
        tempo=_section("tempo", d.get("tempo")),
        harmony=_section("harmony", d.get("harmony")),
        pad=_section("pad", d.get("pad")),
        pulse=_section("pulse", d.get("pulse")),
        lock=_section("lock", d.get("lock")),
        dynamics=_section("dynamics", d.get("dynamics")),
        drums=_section("drums", d.get("drums")),
        percussion=_section("percussion", d.get("percussion")),
        piano=_section("piano", d.get("piano")),
        audio=_section("audio", d.get("audio")),
        response=_section("response", d.get("response")),
        groove=_section("groove", d.get("groove")),
        song=_section("song", d.get("song")),
        ending=_section("ending", d.get("ending")),
        start=_section("start", d.get("start")),
        breaks=_section("breaks", d.get("breaks")),
        interlude=_section("interlude", d.get("interlude")),
        mix=_section("mix", d.get("mix")),
        library=_section("library", d.get("library")),
        body=_section("body", d.get("body")),
        ui=_section("ui", d.get("ui")),
        pedal=_section("pedal", d.get("pedal")),
        panic=_section("panic", d.get("panic")),
        controls=_controls(d.get("controls"))[0],
        program_bank=_controls(d.get("controls"))[1],
        preset=name,
    )
    if cfg.panic.cc is not None:
        cfg.controls.setdefault(("cc", cfg.panic.cc), ControlCfg("panic"))
    # The feel knobs set their detailed settings, except those given explicitly.
    from .feel import MACROS, apply

    explicit = frozenset(f"{sec}.{k}" for sec, v in d.items() if isinstance(v, dict) for k in v)
    for knob in MACROS:
        apply(cfg, knob, skip=explicit)
    check(cfg)
    return cfg


def check(cfg: Config) -> None:
    """Rules that involve more than one parameter. Also run after every live change."""
    t = cfg.tempo
    if not (t.min_bpm < t.max_bpm):
        raise ConfigError("[tempo] need min_bpm < max_bpm")
    for k in ("initial_bpm", "prior_bpm"):
        if not (t.min_bpm <= getattr(t, k) <= t.max_bpm):
            raise ConfigError(f"[tempo] {k} must lie between min_bpm and max_bpm")
    if cfg.pulse.stop_confidence > cfg.pulse.min_confidence:
        raise ConfigError("[pulse] stop_confidence must not be above min_confidence")
    cfg.root_pc  # validates harmony.root


def _read_toml(p: Path) -> dict:
    try:
        with open(p, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{p}: not valid TOML ({e})") from None


def load(path: str | Path, preset: Optional[str] = None, overrides: Optional[dict] = None,
         song: Optional[str] = None) -> Config:
    """config.toml, layered over a preset, under a song; `overrides` (e.g. command-line flags)
    win."""
    return build(path, preset, overrides, song)
