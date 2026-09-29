"""accompanist devices | monitor | run [--record] | replay TAKE | simulate | params [--json]"""
from __future__ import annotations

import argparse
import json
import queue
import re
import select
import shutil
import sys
import time
from pathlib import Path

from . import config as cfgmod
from . import params as registry
from .controller import Controller, format_status
from .midi_io import PortError, list_ports, open_inputs, open_output
from .output import SafeOutput
from .recording import Recorder, TakeError, auto_path, load_actions, load_take


class KeyReader:
    """Single-keypress input (no Enter needed) so the kill switch is one tap."""

    def __init__(self) -> None:
        self.enabled = sys.stdin.isatty()
        self._old = None
        if self.enabled:
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)

    def poll(self):
        if not self.enabled:
            return None
        r, _, _ = select.select([sys.stdin], [], [], 0)
        return sys.stdin.read(1) if r else None

    def close(self) -> None:
        if self.enabled and self._old is not None:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)


def cmd_devices(_args) -> int:
    ins, outs = list_ports()
    print("MIDI inputs:")
    for n in ins or ["(none)"]:
        print(f"  {n}")
    print("MIDI outputs:")
    for n in outs or ["(none)"]:
        print(f"  {n}")
    print("\nPut a distinctive part of a name in `port = \"...\"` in your config.")
    return 0


def cmd_monitor(args) -> int:
    cfg = cfgmod.load(args.config)
    q: queue.Queue = queue.Queue()
    ports = open_inputs(cfg, q)
    print("Monitoring configured inputs. Play something; Ctrl-C to stop.")
    try:
        while True:
            try:
                t, icfg, msg = q.get(timeout=0.2)
            except queue.Empty:
                continue
            if msg.type in ("clock", "active_sensing"):
                continue
            print(f"{t:10.3f}  {icfg.name or icfg.port:<12} {msg}")
    except KeyboardInterrupt:
        pass
    finally:
        for p in ports:
            p.close()
    return 0


PRESET_HELP = "layer a preset (presets/NAME.toml) under your config; see `accompanist params`"
KEYS = {" ": "panic", "p": "panic", "r": "resume", "t": "tap_tempo", "l": "lock", "u": "unlock"}


def say(message) -> None:
    """Print an event line above the status line (which is redrawn after it)."""
    if message:
        sys.stdout.write(f"\r\x1b[K{message}\n")
        sys.stdout.flush()


def cmd_run(args) -> int:
    cfg = cfgmod.load(args.config, args.preset)
    q: queue.Queue = queue.Queue()
    in_ports = open_inputs(cfg, q)
    port = open_output(cfg.output)
    out = SafeOutput(port)
    ctl = Controller(cfg, out)
    keys = KeyReader()
    rec = None
    if args.record:
        rec = Recorder(auto_path() if args.record == "auto" else args.record)
    where = cfg.output.port or f"virtual source '{cfg.output.virtual_name}'"
    print(f"Listening on {len(in_ports)} input(s); playing to {where}."
          + (f" Preset: {cfg.preset}." if cfg.preset else ""))
    if rec:
        print(f"Recording your notes to {rec.path}")
    print("Keys: [space]/[p] = PANIC (silence + mute)   [r] = resume   [l] (L) = lock   [u] = unlock"
          "   [t] = tap tempo   [q] = quit\n")
    last_print = 0.0
    try:
        while True:
            now = time.monotonic()
            while True:
                try:
                    t, icfg, msg = q.get_nowait()
                except queue.Empty:
                    break
                if msg.type == "note_on" and msg.velocity > 0 and icfg.role == "note_source":
                    ctl.on_note(t, msg.note, msg.velocity)
                    if rec:
                        rec.note_on(t, msg.note, msg.velocity, icfg.name or icfg.port)
                elif msg.type == "control_change":
                    target = cfg.controls.get(msg.control)
                    try:
                        if target in cfgmod.ACTIONS:
                            if msg.value >= 64:        # a switch pressed
                                say(ctl.do(target, t))
                                if rec:
                                    rec.action(t, target)
                        elif target is not None:
                            say(ctl.on_cc(t, msg.control, msg.value))
                    except cfgmod.ConfigError as e:
                        say(str(e))
                    last_print = 0.0
            key = keys.poll()
            if key == "q":
                break
            if key in KEYS:
                say(ctl.do(KEYS[key], now))
                if rec:
                    rec.action(now, KEYS[key])
                last_print = 0.0               # show the new state at once
            ctl.tick(now)
            if now - last_print >= 0.25:
                width = shutil.get_terminal_size((100, 20)).columns - 1
                sys.stdout.write("\r\x1b[K" + format_status(ctl.get_state(now))[:width])
                sys.stdout.flush()
                last_print = now
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        out.panic()  # never leave notes hanging, however we exit
        if rec:
            rec.close()
        keys.close()
        for p in in_ports:
            p.close()
        port.close()
        print("\nStopped. All notes off.")
    return 0


def cmd_simulate(args) -> int:
    from . import simulate

    cfg = cfgmod.load(args.config, args.preset) if args.config else cfgmod.from_dict({}, args.preset)
    res = simulate.run(cfg, verbose=True)
    print("\nPad changes:")
    for t, what in res.log:
        print(f"  t={t:6.1f}s  {what}")
    print(f"\nMIDI messages emitted: {len(res.port.sent)}; notes still sounding at end: {len(res.engine.out.sounding)}")
    return 0


def cmd_replay(args) -> int:
    from . import simulate

    cfg_path = args.config or ("config.toml" if Path("config.toml").exists() else None)
    cfg = cfgmod.load(cfg_path, args.preset) if cfg_path else cfgmod.from_dict({}, args.preset)
    onsets = [(t + 1.0, n, v) for t, n, v in load_take(args.take)]
    actions = [(t + 1.0, a) for t, a in load_actions(args.take)]
    print(f"Replaying {args.take}: {len(onsets)} notes over {onsets[-1][0] - 1.0:.1f}s "
          f"(config: {cfg_path or 'defaults'}{', preset: ' + cfg.preset if cfg.preset else ''})\n")
    res = simulate.run(cfg, verbose=True, onsets=onsets, actions=actions)
    active = [b for (t, b, c) in res.tempo_trace if onsets[0][0] <= t <= onsets[-1][0]]
    print("\nPad changes and groove lock:")
    for t, what in res.log:
        print(f"  t={t:6.1f}s  {what}")
    if actions:
        print("Your key presses / controller actions (replayed): "
              + ", ".join(f"{a} at {t:.1f}s" for t, a in actions))
    if active:
        print(f"\nTempo while you played: min {min(active):.1f}, max {max(active):.1f}, "
              f"final {active[-1]:.1f} bpm")
    m = re.search(r"(\d+(?:\.\d+)?)bpm", Path(args.take).stem)
    if m and "to" not in Path(args.take).stem:  # e.g. melody-90bpm.jsonl: the true tempo is known
        expected = float(m.group(1))
        start = onsets[0][0]
        late = [(t, b) for (t, b, _) in res.tempo_trace if start + 30 <= t <= onsets[-1][0]]
        if late:
            worst = max(abs(b / expected - 1) for _, b in late)
            print(f"Expected {expected:g} bpm (from the file name). From 30 s of playing on: "
                  f"worst error {worst:.1%} {'(OK, within 4%)' if worst <= 0.04 else '(outside 4%)'}")
    if res.engine.locked:
        print("Still LOCKED at the end: live, pad and pulse would keep playing until you "
              "unlock ([u], a controller, or panic).")
    print(f"Notes still sounding at end: {len(res.engine.out.sounding)}")
    return 0


def cmd_params(args) -> int:
    if args.json:
        print(json.dumps({"params": registry.schema(), "actions": list(cfgmod.ACTIONS),
                          "presets": cfgmod.available_presets()}, indent=2))
        return 0
    group = None
    for p in registry.PARAMS:
        if p.deprecated:
            continue
        if p.group != group:
            group = p.group
            print(f"\n{group}")
        rng = f"{p.min:g}-{p.max:g}" if p.min is not None else (
            "|".join(map(str, p.choices)) if p.choices else p.type.__name__)
        flag = "" if p.live else "  (restart to change)"
        print(f"  {p.key:<26} {str(p.default):<12} {rng:<14} {p.help}{flag}")
    print(f"\nActions (keys, [controls] CCs): {', '.join(cfgmod.ACTIONS)}")
    print(f"Presets (--preset NAME): {', '.join(cfgmod.available_presets()) or '(none)'}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="accompanist", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices", help="list MIDI ports")
    for name, helptext in (("monitor", "print incoming MIDI from configured inputs"),
                           ("run", "listen and accompany, live")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("-c", "--config", default="config.toml")
        if name == "run":
            sp.add_argument("--preset", default=None, help=PRESET_HELP)
            sp.add_argument("--record", nargs="?", const="auto", default=None, metavar="FILE",
                            help="save your notes as a take (default: takes/take-<time>.jsonl)")
    sp = sub.add_parser("replay", help="run a recorded take through the engine offline (no hardware)")
    sp.add_argument("take", help="a take file made with `run --record`")
    sp.add_argument("-c", "--config", default=None, help="default: ./config.toml if present, else defaults")
    sp.add_argument("--preset", default=None, help=PRESET_HELP)
    sp = sub.add_parser("simulate", help="dry-run against a scripted performance (no hardware)")
    sp.add_argument("-c", "--config", default=None)
    sp.add_argument("--preset", default=None, help=PRESET_HELP)
    sp = sub.add_parser("params", help="list every setting (with --json: the schema a UI is built from)")
    sp.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    try:
        return {"devices": cmd_devices, "monitor": cmd_monitor, "run": cmd_run,
                "replay": cmd_replay, "simulate": cmd_simulate, "params": cmd_params}[args.cmd](args)
    except (cfgmod.ConfigError, PortError, TakeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
