"""Setting the band's levels like a line check: one voice at a time, measured, then trimmed.

`accompanist levels` plays each voice alone for a few seconds at its normal performing level,
then asks you to play; your DAW records it all (the recorder switch). This module is the pure
part: what to play (plan), measuring each part of the recording (measure), the trims that
bring each voice to its target below your instrument (trims), MIDI volume for a trim
(cc7_value), and writing the trims into a song file (update_song).

A trim is sent as MIDI volume (CC7) on the voice's channel when a song loads. CC7 follows
the General MIDI curve, dB = 40 log10(value / 127), with 0 dB trim at CC7_UNITY, which
leaves a little headroom above (up to +4 dB).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

VOICES = ("pad", "bass", "drums", "percussion", "piano", "guitar")
CC7_UNITY = 100            # CC7 for a 0 dB trim
LEAD_IN_S = 2.0            # silence before the first voice (the room's noise floor)
PART_S = 3.0               # each voice plays this long...
GAP_S = 1.0                # ...then this much silence
YOU_S = 6.0                # your turn
SKIP_S = 0.4               # measure from this far into a part (past its attack)
ACTIVE_PERCENTILE = 80     # your part: the level of your louder moments, not the gaps
FLOOR_MARGIN_DB = 6.0      # a part this close to the noise floor made no sound


@dataclass(frozen=True)
class Part:
    voice: str             # one of VOICES, or "you"
    channel: int           # 1-16 (0 for you)
    start: float           # seconds from the first part's start
    notes: tuple           # ((at, note, velocity, length), ...) within the part


def cc7_value(db: float) -> int:
    return min(max(round(CC7_UNITY * 10 ** (db / 40)), 0), 127)


def voice_cfg(cfg: Any, voice: str):
    return {"pad": cfg.pad, "bass": cfg.pulse, "drums": cfg.drums, "percussion": cfg.percussion,
            "piano": cfg.piano, "guitar": cfg.response}[voice]


def plan(cfg: Any) -> list[Part]:
    """Each voice that is on, alone, at its normal level; then you."""
    from .patterns import GM_DRUMS

    parts, t = [], 0.0
    for voice in VOICES:
        vc = voice_cfg(cfg, voice)
        if not vc.enabled:
            continue
        vel = getattr(vc, "velocity", 80)
        if voice == "pad":
            base = 12 * (cfg.pad.octave + 1)
            notes = tuple((0.0, base + d, vel, PART_S) for d in (0, 4, 7, 11))
        elif voice == "bass":
            base = 12 * (cfg.pulse.octave + 1)
            notes = tuple((i * 0.5, base + (0, 7, 12, 7)[i % 4], vel + (cfg.pulse.accent if i % 4 == 0 else 0),
                           0.4) for i in range(int(PART_S / 0.5)))
        elif voice in ("drums", "percussion"):
            names = (("kick", "hat", "snare", "hat") if voice == "drums"
                     else ("conga_mute", "conga_high", "conga_low", "shaker"))
            notes = tuple((i * 0.25, GM_DRUMS[names[i % 4]], vel, 0.1)
                          for i in range(int(PART_S / 0.25)))
        else:                                      # piano, guitar: a line
            base = 12 * ((cfg.piano.octave if voice == "piano" else 4) + 1)
            notes = tuple((i * 0.25, base + (0, 4, 7, 12, 7, 4)[i % 6], vel, 0.24)
                          for i in range(int(PART_S / 0.25)))
        parts.append(Part(voice, vc.channel, t, notes))
        t += PART_S + GAP_S
    parts.append(Part("you", 0, t, ()))
    return parts


def frame_db(samples: np.ndarray, rate: float, frame_s: float = 0.1) -> np.ndarray:
    n = max(1, int(rate * frame_s))
    k = len(samples) // n
    if k == 0:
        return np.array([-120.0])
    power = (samples[: k * n].reshape(k, n) ** 2).mean(axis=1)
    return 10 * np.log10(power + 1e-12)


def find_start(samples: np.ndarray, rate: float, floor_db: float) -> Optional[float]:
    """When the first part begins: the first sound well above the noise floor."""
    db = frame_db(samples, rate, 0.01)
    loud = np.nonzero(db > floor_db + FLOOR_MARGIN_DB * 2)[0]
    return None if len(loud) == 0 else float(loud[0]) * 0.01


def measure(samples: np.ndarray, rate: float, parts: list[Part]) -> dict[str, Optional[float]]:
    """{voice: level in dB, or None if it made no sound} for each part of a recording that
    starts with LEAD_IN_S of silence."""
    floor = float(np.median(frame_db(samples[: int(rate * LEAD_IN_S * 0.8)], rate)))
    start = find_start(samples, rate, floor)
    out: dict[str, Optional[float]] = {}
    if start is None:
        return {p.voice: None for p in parts}
    for p in parts:
        length = YOU_S if p.voice == "you" else PART_S
        a = int((start + p.start + SKIP_S) * rate)
        b = int((start + p.start + length) * rate)
        db = frame_db(samples[a:b], rate)
        level = float(np.percentile(db, ACTIVE_PERCENTILE)) if p.voice == "you" else float(
            10 * np.log10(np.mean(10 ** (db / 10)) + 1e-12))
        out[p.voice] = level if level > floor + FLOOR_MARGIN_DB else None
    return out


def trims(levels: dict[str, Optional[float]], cfg: Any) -> dict[str, Optional[float]]:
    """{voice: new trim in dB} bringing each voice to its target below you (None: it made no
    sound, or you weren't heard)."""
    you = levels.get("you")
    out = {}
    for voice, level in levels.items():
        if voice == "you":
            continue
        if level is None or you is None:
            out[voice] = None
            continue
        current = getattr(cfg.mix, f"{voice}_db")
        target = you + getattr(cfg.mix, f"target_{voice}_db")
        out[voice] = round(min(max(current + target - level, -40.0), 4.0) * 2) / 2
    return out


def update_song(text: str, new: dict[str, float]) -> str:
    """The song file's text with its [mix] trims set (other [mix] settings kept)."""
    lines = text.rstrip("\n").split("\n")
    start = next((i for i, l in enumerate(lines) if l.strip() == "[mix]"), None)
    if start is None:
        lines += ["", "[mix]   # levels: written by `accompanist levels`"]
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")),
               len(lines))
    keep = [l for l in lines[start + 1:end]
            if not re.match(r"\s*(" + "|".join(f"{v}_db" for v in new) + r")\s*=", l)]
    while keep and not keep[-1].strip():
        keep.pop()
    block = [f"{v}_db = {db:g}" for v, db in new.items()]
    return "\n".join(lines[:start + 1] + keep + block + lines[end:]) + "\n"


def describe(levels: dict, new: dict, cfg: Any) -> list[str]:
    """Readable lines: each voice's level, target and trim."""
    you = levels.get("you")
    rows = [f"  you: {you:6.1f} dB" if you is not None else "  you: not heard (play louder, "
            "or check the input)"]
    for voice in VOICES:
        if voice not in levels:
            continue
        level, trim = levels[voice], new.get(voice)
        if level is None:
            rows.append(f"  {voice:10s} no sound: check its MainStage strip (soundcheck {voice})")
            continue
        rel = level - you if you is not None else math.nan
        target = getattr(cfg.mix, f"target_{voice}_db")
        rows.append(f"  {voice:10s} {rel:+6.1f} dB under you (target {target:+.0f}) -> trim "
                    f"{trim:+.1f} dB" if trim is not None else f"  {voice:10s} {level:6.1f} dB")
    return rows
