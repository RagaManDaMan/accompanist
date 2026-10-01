"""Drum patterns: plain data, one TOML file each, so new grooves need no code.

A pattern is a cycle of `beats` beats, each split into `steps_per_beat` steps, and one
grid string per instrument, one character per step:

    X = accent    x = hit    g = ghost (soft)    . = rest    (spaces and | are ignored)

Instruments are General MIDI drum names (kick, snare, hat, ride, shaker, ...) or note
numbers. The cycle can be any length (3, 4, 7, 10, 16 beats...), which is what lets
non-4/4 rhythmic cycles be added later as more files.

Built-in patterns live in accompanist/patterns/; ./patterns/NAME.toml overrides them.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, _read_toml

BUILTIN = Path(__file__).parent / "patterns"
USER = Path("patterns")

GM_DRUMS = {
    "kick": 36, "rim": 37, "snare": 38, "clap": 39, "snare2": 40, "tom_low": 45, "hat": 42,
    "pedal_hat": 44, "open_hat": 46, "tom_mid": 47, "tom_high": 50, "crash": 49, "ride": 51,
    "ride_bell": 53, "tambourine": 54, "cowbell": 56, "bongo_high": 60, "bongo_low": 61,
    "conga_mute": 62, "conga_high": 63, "conga_low": 64, "shaker": 70, "claves": 75,
    "woodblock_high": 76, "woodblock_low": 77, "triangle": 81, "triangle_mute": 80,
    "timbale_high": 65, "timbale_low": 66, "agogo_high": 67, "agogo_low": 68, "cabasa": 69,
    "guiro_short": 73, "guiro_long": 74,
}
LEVELS = {"X": "accent", "x": "hit", "g": "ghost"}


@dataclass(frozen=True)
class Pattern:
    name: str
    beats: int
    steps_per_beat: int
    hits: tuple[tuple[int, int, str], ...]      # (step, GM note, 'accent'|'hit'|'ghost')
    description: str = ""


def available() -> list[str]:
    return sorted({p.stem for d in (BUILTIN, USER) if d.is_dir() for p in d.glob("*.toml")})


def load(name: str) -> Pattern:
    for d in (USER, BUILTIN):
        p = d / f"{name}.toml"
        if p.is_file():
            return parse(name, _read_toml(p), str(p))
    raise ConfigError(f"unknown drum pattern '{name}'; available: {', '.join(available())}")


def parse(name: str, data: dict, where: str = "pattern") -> Pattern:
    try:
        beats, spb = int(data["beats"]), int(data["steps_per_beat"])
        grids = data["hits"]
    except (KeyError, TypeError, ValueError):
        raise ConfigError(f"{where}: needs beats, steps_per_beat and a [hits] table") from None
    if not (1 <= beats <= 64 and 1 <= spb <= 8) or not isinstance(grids, dict):
        raise ConfigError(f"{where}: beats must be 1-64, steps_per_beat 1-8")
    steps = beats * spb
    hits = []
    for inst, grid in grids.items():
        note = GM_DRUMS.get(inst) if not str(inst).isdigit() else int(inst)
        if note is None or not 0 <= note <= 127:
            raise ConfigError(f"{where}: unknown instrument '{inst}'; use a number or one of "
                              f"{', '.join(sorted(GM_DRUMS))}")
        cells = [ch for ch in str(grid) if ch not in " |"]
        if len(cells) != steps:
            raise ConfigError(f"{where}: '{inst}' has {len(cells)} steps, needs {steps} "
                              f"({beats} beats x {spb})")
        for i, ch in enumerate(cells):
            if ch in LEVELS:
                hits.append((i, note, LEVELS[ch]))
            elif ch != ".":
                raise ConfigError(f"{where}: '{inst}' step {i + 1}: '{ch}' is not X, x, g or .")
    return Pattern(name, beats, spb, tuple(sorted(hits)), str(data.get("description", "")))
