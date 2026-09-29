"""The one place control comes in: keys, MIDI CCs and any future UI all call this.

    ctl = Controller(cfg, SafeOutput(port))
    ctl.on_note(t, note, vel); ctl.on_cc(t, cc, value); ctl.tick(now)
    ctl.set_param("pad.velocity", 70)      # validated; applies at the next tick
    ctl.get_state(now)                     # a plain dict; the status line formats it
    ctl.panic(); ctl.resume(); ctl.lock(now); ctl.unlock(); ctl.tap_tempo(now)
    ctl.hold_chord(); ctl.release_chord()      # or ctl.do("chord_toggle", now)

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
        self.t0: Optional[float] = None         # first note or action: the take's clock starts here

    # ---- input --------------------------------------------------------------
    def _started(self, t: float) -> None:
        if self.t0 is None:
            self.t0 = t

    def on_note(self, t: float, note: int, velocity: int) -> None:
        self._started(t)
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
    def do(self, action: str, now: float) -> str:
        """Perform an action. Returns a short message saying what happened (or why not),
        for the status display: a key press should never be silently ignored."""
        if action not in ACTIONS:
            raise ConfigError(f"unknown action '{action}'; use one of {list(ACTIONS)}")
        self._started(now)
        eng = self.engine
        if action == "panic":
            self.panic()
            return "PANIC: silenced and muted (r = resume)"
        if action == "resume":
            was = eng.muted
            self.resume()
            return "resumed" if was else "not muted"
        if action == "lock_toggle":
            action = "unlock" if eng.locked else "lock"
        if action == "lock":
            if eng.locked:
                return "already LOCKED"
            if eng.muted:
                return "can't lock while muted (r = resume first)"
            if not self.lock(now):
                return "can't lock yet: nothing heard"
            return f"tempo LOCKED at {eng.tempo.bpm:.1f} bpm"
        if action == "unlock":
            was = eng.locked
            self.unlock()
            return "tempo unlocked" if was else "tempo not locked"
        if action == "chord_toggle":
            action = "chord_release" if eng.chord_held else "chord_hold"
        if action == "chord_hold":
            if eng.chord_held:
                return f"already holding {eng.frozen.label()}"
            if eng.muted:
                return "can't hold a chord while muted (r = resume first)"
            if not self.hold_chord():
                return "can't hold a chord yet: nothing heard"
            return f"chord HELD: {eng.frozen.label()}"
        if action == "chord_release":
            was = eng.chord_held
            self.release_chord()
            return "chords follow you again" if was else "no chord held"
        bpm = self.tap_tempo(now)
        return f"tap {len(self._taps)}/{TAP_COUNT}" if bpm is None else f"tempo set to {bpm:.1f} bpm by tapping"

    def panic(self) -> None:
        self.engine.panic()

    def resume(self) -> None:
        self.engine.resume()

    def lock(self, now: float) -> bool:
        return self.engine.lock(now)

    def unlock(self) -> None:
        self.engine.unlock()

    def hold_chord(self) -> bool:
        return self.engine.hold_chord()

    def release_chord(self) -> None:
        self.engine.release_chord()

    def tap_tempo(self, now: float) -> Optional[float]:
        """Tap the beat. After TAP_COUNT taps, sets the tempo, and its octave (prior_bpm), to
        what you tapped, narrows the prior (tap_sigma_oct) so look-alike tempi don't take over,
        and lines the pulse up with your last tap. Returns the bpm once set."""
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
        if t.prior_sigma_oct > t.tap_sigma_oct:
            self.set_param("tempo.prior_sigma_oct", t.tap_sigma_oct)
        self.engine.set_tempo(bpm, beat_t=now)
        self._taps = []
        return bpm

    # ---- state --------------------------------------------------------------------
    def get_state(self, now: float) -> dict:
        s = self.engine.state(now)
        s["preset"] = self.cfg.preset
        s["overrides"] = dict(self.overrides)
        s["taps"] = len(self._taps)
        s["elapsed_s"] = None if self.t0 is None else now - self.t0   # same clock as a recorded take
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


def clock(seconds: Optional[float]) -> str:
    """Playing time as m:ss.s (the clock recorded takes and `replay` use)."""
    if seconds is None:
        return "-:--.-"
    m, sec = divmod(max(seconds, 0.0), 60)
    return f"{int(m)}:{sec:04.1f}"


def format_status(s: dict) -> str:
    """The one-line status display. Only a formatter of get_state().

    State flags come first, so they stay visible when a narrow window cuts the line."""
    heard = "--" if s["heard"] is None else f"{s['heard']} ({s['heard_ago_s']:0.1f}s ago)"
    flags = (("MUTED " if s["muted"] else "") + ("LOCKED " if s.get("locked") else "")
             + ("CHORD HELD " if s.get("chord_held") else ""))
    return (f"{clock(s.get('elapsed_s'))}  {flags}{s['bpm']:5.1f} bpm  conf {s['confidence']:4.0%}  "
            f"pad {s['pad'] or '--':<7} pulse {'on ' if s['pulse'] else 'off'}  "
            f"{'key ' + s['key'] + '  ' if s.get('key') else ''}"
            f"root {s['root'] or '--':<2}  heard {heard}")
