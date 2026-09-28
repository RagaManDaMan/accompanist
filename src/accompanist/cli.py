"""accompanist devices | monitor | run [--record] | replay TAKE | simulate"""
from __future__ import annotations

import argparse
import queue
import select
import sys
import time
from pathlib import Path

from . import config as cfgmod
from .engine import Engine
from .midi_io import PortError, list_ports, open_inputs, open_output
from .output import SafeOutput
from .recording import Recorder, TakeError, auto_path, load_take


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


def cmd_run(args) -> int:
    cfg = cfgmod.load(args.config)
    q: queue.Queue = queue.Queue()
    in_ports = open_inputs(cfg, q)
    port = open_output(cfg.output)
    out = SafeOutput(port)
    eng = Engine(cfg, out)
    keys = KeyReader()
    rec = None
    if args.record:
        rec = Recorder(auto_path() if args.record == "auto" else args.record)
    where = cfg.output.port or f"virtual source '{cfg.output.virtual_name}'"
    print(f"Listening on {len(in_ports)} input(s); playing to {where}.")
    if rec:
        print(f"Recording your notes to {rec.path}")
    print("Keys: [space] or [p] = PANIC (silence + mute)   [r] = resume   [q] = quit\n")
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
                    eng.on_note(t, msg.note, msg.velocity)
                    if rec:
                        rec.note_on(t, msg.note, msg.velocity, icfg.name or icfg.port)
                elif (msg.type == "control_change" and cfg.panic.cc is not None
                      and msg.control == cfg.panic.cc and msg.value >= 64):
                    eng.panic()
            key = keys.poll()
            if key in (" ", "p"):
                eng.panic()
            elif key == "r":
                eng.resume()
            elif key == "q":
                break
            eng.tick(now)
            if now - last_print >= 0.25:
                sys.stdout.write("\r\x1b[K" + eng.status(now))
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

    cfg = cfgmod.load(args.config) if args.config else cfgmod.from_dict({})
    res = simulate.run(cfg, verbose=True)
    print("\nPad changes:")
    for t, what in res.log:
        print(f"  t={t:6.1f}s  {what}")
    print(f"\nMIDI messages emitted: {len(res.port.sent)}; notes still sounding at end: {len(res.engine.out.sounding)}")
    return 0


def cmd_replay(args) -> int:
    from . import simulate

    cfg_path = args.config or ("config.toml" if Path("config.toml").exists() else None)
    cfg = cfgmod.load(cfg_path) if cfg_path else cfgmod.from_dict({})
    onsets = [(t + 1.0, n, v) for t, n, v in load_take(args.take)]
    print(f"Replaying {args.take}: {len(onsets)} notes over {onsets[-1][0] - 1.0:.1f}s "
          f"(config: {cfg_path or 'defaults'})\n")
    res = simulate.run(cfg, verbose=True, onsets=onsets)
    active = [b for (t, b, c) in res.tempo_trace if onsets[0][0] <= t <= onsets[-1][0]]
    print("\nPad changes:")
    for t, what in res.log:
        print(f"  t={t:6.1f}s  {what}")
    if active:
        print(f"\nTempo while you played: min {min(active):.1f}, max {max(active):.1f}, "
              f"final {active[-1]:.1f} bpm")
    print(f"Notes still sounding at end: {len(res.engine.out.sounding)}")
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
            sp.add_argument("--record", nargs="?", const="auto", default=None, metavar="FILE",
                            help="save your notes as a take (default: takes/take-<time>.jsonl)")
    sp = sub.add_parser("replay", help="run a recorded take through the engine offline (no hardware)")
    sp.add_argument("take", help="a take file made with `run --record`")
    sp.add_argument("-c", "--config", default=None, help="default: ./config.toml if present, else defaults")
    sp = sub.add_parser("simulate", help="dry-run against a scripted performance (no hardware)")
    sp.add_argument("-c", "--config", default=None)
    args = p.parse_args(argv)
    try:
        return {"devices": cmd_devices, "monitor": cmd_monitor, "run": cmd_run,
                "replay": cmd_replay, "simulate": cmd_simulate}[args.cmd](args)
    except (cfgmod.ConfigError, PortError, TakeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
