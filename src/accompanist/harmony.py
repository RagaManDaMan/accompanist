"""What has the player been playing lately, and what pad voicing fits it?

Harmony is a plug-in: anything with observe(onset) and propose(now) -> Voicing
can drive the pad (see HarmonyModel). The engine never assumes a style. The
only model so far is DroneModel: a root plus open fifth, with the third added
once you have played it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol


class PitchClassTracker:
    """Exponentially-decaying histogram of pitch classes (C=0 ... B=11)."""

    def __init__(self, half_life_s: float = 12.0) -> None:
        self.half_life = half_life_s
        self._h = [0.0] * 12
        self._t: float | None = None

    def _decay_to(self, t: float) -> None:
        if self._t is None:
            self._t = t
            return
        dt = max(0.0, t - self._t)
        f = 0.5 ** (dt / self.half_life)
        self._h = [x * f for x in self._h]
        self._t = t

    def add(self, t: float, note: int, velocity: int = 100) -> None:
        self._decay_to(t)
        w = 0.3 + 0.7 * min(max(velocity, 1), 127) / 127.0
        self._h[note % 12] += w

    def snapshot(self, t: float) -> list[float]:
        self._decay_to(t)
        return list(self._h)


@dataclass(frozen=True)
class Voicing:
    root_pc: int
    third: int | None          # 3 = minor, 4 = major, None = open (drone-like)
    notes: tuple[int, ...]

    @property
    def key(self) -> tuple[int, int | None]:
        return (self.root_pc, self.third)

    def label(self) -> str:
        from .config import NOTE_NAMES

        q = {None: "5", 3: "m", 4: ""}[self.third]
        return f"{NOTE_NAMES[self.root_pc]}{q}"


def choose_voicing(hist: list[float], root_pc: int, octave: int = 3, third_threshold: float = 0.35) -> Voicing:
    """Open, atmospheric voicing: root, fifth, octave, and the third only if you've been playing it."""
    root_w = hist[root_pc]
    minor, major = hist[(root_pc + 3) % 12], hist[(root_pc + 4) % 12]
    third = None
    if root_w > 0 and max(minor, major) >= third_threshold * root_w:
        third = 3 if minor > major else 4
    base = 12 * (octave + 1) + root_pc
    notes = [base, base + 7, base + 12]
    if third is not None:
        notes.append(base + 12 + third)
    return Voicing(root_pc, third, tuple(notes))


@dataclass(frozen=True)
class Onset:
    t: float
    note: int
    velocity: int


class HarmonyModel(Protocol):
    """A harmony plug-in. It listens to every onset and, when asked, proposes a voicing.

    propose() may return None ("nothing to say yet"). The pad adds its own lag and
    rate limit on top, so a model can propose freely; it need not smooth its output.
    Models read their settings from the live config object they were given, so
    parameter changes apply at the next call.
    """

    def observe(self, onset: Onset) -> None: ...

    def propose(self, now: float) -> Optional[Voicing]: ...


class DroneModel:
    """Decaying pitch-class memory -> dominant root -> open voicing (+ third if played)."""

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg                     # the whole Config: harmony.* and pad.octave
        self.pitch = PitchClassTracker(cfg.harmony.half_life_s)
        self.root_pc: Optional[int] = cfg.root_pc

    def observe(self, onset: Onset) -> None:
        self.pitch.half_life = self.cfg.harmony.half_life_s
        self.pitch.add(onset.t, onset.note, onset.velocity)

    def propose(self, now: float) -> Optional[Voicing]:
        self.pitch.half_life = self.cfg.harmony.half_life_s
        hist = self.pitch.snapshot(now)
        if sum(hist) <= 0:
            return None
        root = self._select_root(hist)
        return choose_voicing(hist, root, self.cfg.pad.octave, self.cfg.harmony.third_threshold)

    def _select_root(self, hist: list[float]) -> int:
        fixed = self.cfg.root_pc
        if fixed is not None:
            self.root_pc = fixed
            return fixed
        dominant = max(range(12), key=lambda pc: hist[pc])
        if self.root_pc is None or hist[dominant] > hist[self.root_pc] * self.cfg.harmony.switch_margin:
            self.root_pc = dominant
        return self.root_pc


MODELS = {"drone": DroneModel}


def make_model(cfg: Any) -> HarmonyModel:
    return MODELS[cfg.harmony.model](cfg)
