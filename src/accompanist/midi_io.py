"""Live MIDI plumbing. The only module that touches real ports.

Nothing here is specific to any device: ports are found by (case-insensitive)
substring of their name, and everything else comes from the config file.
"""
from __future__ import annotations

import queue
import time
from typing import Optional

from .config import Config, InputCfg, OutputCfg


class PortError(RuntimeError):
    pass


def _mido():
    """Import mido and make sure a MIDI backend exists, with a readable error if not."""
    import mido

    try:
        mido.get_input_names()
    except ImportError as e:
        raise PortError(
            "No MIDI backend available (python-rtmidi is missing or failed to build). "
            "Run: pip install python-rtmidi   (on macOS you may first need: xcode-select --install)"
        ) from e
    return mido


def list_ports() -> tuple[list[str], list[str]]:
    mido = _mido()
    return mido.get_input_names(), mido.get_output_names()


def find_port(names: list[str], wanted: str, kind: str) -> str:
    matches = [n for n in names if wanted.lower() in n.lower()]
    if len(matches) == 1:
        return matches[0]
    listing = "\n  ".join(names) or "(none found)"
    if not matches:
        raise PortError(f"No MIDI {kind} port matching '{wanted}'. Available:\n  {listing}")
    raise PortError(f"'{wanted}' matches several MIDI {kind} ports; be more specific:\n  " + "\n  ".join(matches))


def open_output(cfg: OutputCfg):
    mido = _mido()

    if cfg.port:
        return mido.open_output(find_port(mido.get_output_names(), cfg.port, "output"))
    return mido.open_output(cfg.virtual_name, virtual=True)


def open_inputs(cfg: Config, q: "queue.Queue"):
    """Open every configured input. Messages land on q as (monotonic_time, InputCfg, msg)."""
    mido = _mido()

    if not cfg.inputs:
        raise PortError("No [[inputs]] in the config. Run `accompanist devices`, then add one.")
    names = mido.get_input_names()
    ports = []
    for inp in cfg.inputs:
        name = find_port(names, inp.port, "input")

        def make_cb(icfg: InputCfg):
            def cb(msg):
                if icfg.channel is not None and getattr(msg, "channel", None) not in (None, icfg.channel - 1):
                    return
                q.put((time.monotonic(), icfg, msg))
            return cb

        ports.append(mido.open_input(name, callback=make_cb(inp)))
    return ports
