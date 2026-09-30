"""One knob per voice, from algorithmic (0) to humanize (1).

Each knob ("feel") sets a group of detailed settings together, along a curve through three
points: fully algorithmic, the setting's default (at the knob's default position), and fully
humanized. So the defaults reproduce the detailed defaults exactly, and turning a knob moves
smoothly away from them. A simple interface only needs these four knobs; everything they set
stays available underneath for anyone who wants it.

At start-up a detailed setting given explicitly (config.toml or a preset) wins over its knob;
turning a knob live (Controller.set_param) sets all of its settings.
"""
from __future__ import annotations

from typing import Any

from . import params as registry

# knob -> [(setting, (at 0 = algorithmic, at the knob's default, at 1 = humanize))]
MACROS: dict[str, list[tuple[str, tuple]]] = {
    "pad.feel": [
        ("pad.change_on", ("bar", "beat", "beat")),      # bar lines only .. any beat
        ("pad.variation", (0.0, 0.4, 0.9)),              # one steady voicing .. varied
        ("pad.revoice_bars", (0, 4, 2)),                 # never .. often
        ("harmony.color", (0.1, 0.5, 0.8)),              # plain chords .. colour tones
        ("harmony.wander", (0.0, 0.3, 0.6)),             # best fit .. wandering
        ("dynamics.duck", (0.0, 0.5, 0.8)),              # fixed level .. breathes with you
        ("dynamics.pad_space", (0.0, 0.7, 0.9)),         # fixed level .. swells in the quiet
        ("pad.strum_ms", (0.0, 0.0, 40.0)),              # together .. a slight strum
        ("pad.velocity_spread", (0, 0, 10)),
    ],
    "pulse.feel": [
        ("pulse.phase_gain", (0.0, 0.3, 0.6)),           # rigid grid .. leans toward you
        ("dynamics.follow", (0.0, 0.5, 0.9)),            # even .. follows your dynamics
        ("pulse.timing_ms", (0.0, 0.0, 25.0)),           # on the beat .. laid back
        ("pulse.velocity_spread", (0, 0, 14)),
        ("pulse.movement", (0.0, 0.7, 0.9)),             # root on every beat .. bass shapes
    ],
    "drums.feel": [
        ("groove.auto_drums", (False, True, True)),      # the pattern as set .. follows your swing
        ("drums.ghost", (0.25, 0.45, 0.7)),              # quiet ghosts .. more ghost notes
        ("drums.timing_ms", (0.0, 0.0, 15.0)),
        ("drums.velocity_spread", (0, 0, 14)),
        ("drums.dynamics", (0.0, 0.5, 0.9)),             # even .. follows you, marks phrases
    ],
    "response.feel": [
        ("response.chance", (1.0, 0.9, 0.6)),            # every pause .. not every pause
        ("response.curate", (0.0, 1.0, 1.0)),            # echo of your last phrase .. curated
        ("response.variety", (0.0, 0.0, 0.35)),          # exact .. sometimes varied
        ("response.quantize", (1.0, 1.0, 0.3)),          # on the grid .. your own timing
        ("response.yield_to_you", (1.0, 1.0, 0.5)),      # stops for you .. gives way partly
    ],
}


def value_at(points: tuple, x: float, x_default: float) -> Any:
    """The setting at knob position x: piecewise-linear through (0, a), (x_default, d), (1, b)
    for numbers; for switches and choices, whichever of a, d, b is nearest in knob travel."""
    a, d, b = points
    if isinstance(a, (bool, str)):
        if x < x_default / 2:
            return a
        return d if x <= (x_default + 1) / 2 else b
    if x <= x_default:
        v = a + (d - a) * (x / x_default if x_default else 1.0)
    else:
        v = d + (b - d) * ((x - x_default) / (1 - x_default) if x_default < 1 else 1.0)
    return round(v) if isinstance(a, int) and isinstance(b, int) else v


def settings(knob: str, x: float) -> list[tuple[str, Any]]:
    """(setting, value) pairs for knob position x, each validated by the registry."""
    x_default = registry.get(knob).default
    out = []
    for key, points in MACROS[knob]:
        out.append((key, registry.coerce(registry.get(key), value_at(points, x, x_default))))
    return out


def apply(cfg: Any, knob: str, skip: frozenset = frozenset()) -> None:
    """Set the knob's settings on cfg from its current position (except those in `skip`)."""
    section, name = knob.split(".")
    x = getattr(cfg.section(section), name)
    for key, value in settings(knob, x):
        if key not in skip:
            sec, attr = key.split(".")
            setattr(cfg.section(sec), attr, value)
