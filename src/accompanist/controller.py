"""The one place control comes in: keys, MIDI CCs and any future UI all call this.

    ctl = Controller(cfg, SafeOutput(port))
    ctl.on_note(t, note, vel); ctl.on_cc(t, cc, value); ctl.tick(now)
    ctl.set_param("pad.velocity", 70)      # validated; applies at the next tick
    ctl.get_state(now)                     # a plain dict; the status line formats it
    ctl.panic(); ctl.resume(); ctl.lock(now); ctl.unlock(); ctl.tap_tempo(now)

Like the engine, it is clock-agnostic: callers pass the time.
"""
from __future__ import annotations

from statistics import median
from typing import Any, Optional

from . import params as registry
from .config import ACTIONS, Config, ConfigError, check
from .engine import Engine
from .output import SafeOutput

TAP_COUNT = 4           # taps needed to set the tempo
TAP_RESET_S = 2.5       # a longer gap between taps starts a new count


class Controller:
    def __init__(self, cfg: Config, out: SafeOutput) -> None:
        self.cfg = cfg
        self.engine = Engine(cfg, out)
        self.overrides: dict[str, Any] = {}     # live changes on top of the config file
        self._taps: list[float] = []

    # ---- input --------------------------------------------------------------
    def on_note(self, t: float, note: int, velocity: int) -> None:
        self.engine.on_note(t, note, velocity)

    def on_cc(self, t: float, control: int, value: int) -> Optional[str]:
        """A controller message from any input. Returns what it did (for display), if anything."""
        target = self.cfg.controls.get(control)
        if target is None:
            return None
        if target in ACTIONS:
            if value >= 64:            # a switch pressed (foot switches send 127, then 0)
                self.do(target, t)
                return target
            return None
        p = registry.get(target)
        self.set_param(target, cc_to_value(p, value))
        return f"{target} = {self.get_param(target)}"

    def tick(self, now: float) -> None:
        self.engine.tick(now)

    # ---- parameters -----------------------------------------------------------
    def set_param(self, key: str, value: Any) -> None:
        """Validated live change. Raises ConfigError with a readable message."""
        try:
            p = registry.get(key)
        except KeyError as e:
            raise ConfigError(str(e.args[0])) from None
        if p.deprecated:
            raise ConfigError(f"{key} is no longer used")
        if not p.live:
            raise ConfigError(f"{key} can't change while running; set it in config.toml and restart")
        try:
            value = registry.coerce(p, value)
        except ValueError as e:
            raise ConfigError(f"{key}: {e}") from None
        section = self.cfg.section(p.section)
        old = getattr(section, p.name)
        setattr(section, p.name, value)
        try:
            check(self.cfg)
        except ConfigError:
            setattr(section, p.name, old)
            raise
        self.overrides[key] = value

    def get_param(self, key: str) -> Any:
        p = registry.get(key)
        return getattr(self.cfg.section(p.section), p.name)

    def get_params(self) -> dict[str, Any]:
        return {p.key: self.get_param(p.key) for p in registry.PARAMS if not p.deprecated}

    # ---- actions ----------------------------------------------------------------
    def do(self, action: str, now: float) -> None:
        if action not in ACTIONS:
            raise ConfigError(f"unknown action '{action}'; use one of {list(ACTIONS)}")
        if action in ("tap_tempo", "lock"):
            getattr(self, action)(now)
        else:
            getattr(self, action)()

    def panic(self) -> None:
        self.engine.panic()

    def resume(self) -> None:
        self.engine.resume()

    def lock(self, now: float) -> bool:
        return self.engine.lock(now)

    def unlock(self) -> None:
        self.engine.unlock()

    def tap_tempo(self, now: float) -> Optional[float]:
        """Tap the beat. After TAP_COUNT taps, sets the tempo, and its octave (prior_bpm), to
        what you tapped, and lines the pulse up with your last tap. Returns the bpm once set."""
        if self._taps and now - self._taps[-1] > TAP_RESET_S:
            self._taps = []
        self._taps.append(now)
        if len(self._taps) < TAP_COUNT:
            return None
        taps = self._taps[-TAP_COUNT:]
        period = median(b - a for a, b in zip(taps, taps[1:]))
        t = self.cfg.tempo
        bpm = min(max(60.0 / period, t.min_bpm), t.max_bpm)
        self.set_param("tempo.prior_bpm", round(bpm, 1))
        self.engine.set_tempo(bpm, beat_t=now)
        self._taps = []
        return bpm

    # ---- state --------------------------------------------------------------------
    def get_state(self, now: float) -> dict:
        s = self.engine.state(now)
        s["preset"] = self.cfg.preset
        s["overrides"] = dict(self.overrides)
        s["taps"] = len(self._taps)
        return s


def cc_to_value(p: registry.Param, value: int) -> Any:
    """Map a 0-127 controller value onto a parameter's range."""
    x = min(max(value, 0), 127) / 127.0
    if p.type is bool:
        return value >= 64
    if p.choices:
        return p.choices[min(int(x * len(p.choices)), len(p.choices) - 1)]
    v = p.min + x * (p.max - p.min)
    if p.step:
        v = p.min + round((v - p.min) / p.step) * p.step
    return int(round(v)) if p.type is int else round(v, 6)


def format_status(s: dict) -> str:
    """The one-line status display. Only a formatter of get_state()."""
    heard = "--" if s["heard"] is None else f"{s['heard']} ({s['heard_ago_s']:0.1f}s ago)"
    flags = ""
    if s.get("locked"):
        flags += "  LOCKED"
    if s["muted"]:
        flags += "  ** MUTED **"
    return (f"{s['bpm']:5.1f} bpm  conf {s['confidence']:4.0%}  heard {heard:<16} "
            f"root {s['root'] or '--':<2}  pad {s['pad'] or '--':<4} "
            f"pulse {'on ' if s['pulse'] else 'off'}{flags}")
