"""What has the player been playing lately, and what pad voicing fits it?"""
from __future__ import annotations

from dataclasses import dataclass


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
