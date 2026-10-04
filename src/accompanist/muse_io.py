"""The Muse headband over Bluetooth: the only module that talks to it (like audio_io.py for
audio). It runs in its own thread, decodes the headband's packets with OpenMuse
(github.com/DominiqueMakowski/OpenMuse, MIT), and puts body events on a queue:

    ("beat", t, bpm)       a heartbeat (bpm: the heart rate so far, or None)
    ("gesture", t, name)   'nod', 'tilt_left' or 'tilt_right'
    ("status", t, text)    connected, lost, ...

t is time.monotonic(), the same clock as the MIDI and audio inputs. Optional: needs
`pip install bleak` and OpenMuse; without them `run` warns and carries on.
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .bio import GestureDetector, HeartTracker

ACC_RATE = 52.0
OPTICS_RATE = 64.0
RED = (12, 13)              # the inner left and right red sensors (OPTICS_LI_RED, OPTICS_RI_RED)
PRESET = "p1041"            # OpenMuse's default: EEG, optics, motion
RECONNECT_S = 3.0


class MuseError(RuntimeError):
    pass


def available() -> Optional[str]:
    """None if the headband can be used; else what to install."""
    try:
        import bleak  # noqa: F401
        from OpenMuse.decode import parse_message  # noqa: F401
        from OpenMuse.muse import MuseS  # noqa: F401
    except ImportError:
        return ("the Muse needs Bluetooth support: pip install bleak "
                "https://github.com/DominiqueMakowski/OpenMuse/zipball/main")
    return None


class MuseInput:
    def __init__(self, address: str, out: "queue.Queue", name: str = "muse") -> None:
        problem = available()
        if problem:
            raise MuseError(problem)
        self.address, self.out, self.name = address, out, name
        self.heart, self.gestures = HeartTracker(OPTICS_RATE), GestureDetector()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    # ---- the thread ---------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                asyncio.run(self._session())
            except Exception as e:                       # a dropped link: try again
                self.out.put(("status", time.monotonic(), f"headband: {e}; reconnecting"))
            if not self._stop.wait(RECONNECT_S):
                continue

    async def _session(self) -> None:
        import bleak
        from OpenMuse.muse import MuseS

        async with bleak.BleakClient(self.address, timeout=15.0) as client:
            callbacks = {uuid: self._callback(uuid) for uuid in MuseS.DATA_CHARACTERISTICS}
            await MuseS.connect_and_initialize(client, PRESET, callbacks, False)
            self.out.put(("status", time.monotonic(), "headband connected"))
            while not self._stop.is_set() and client.is_connected:
                await asyncio.sleep(0.1)

    def _callback(self, uuid: str):
        from OpenMuse.decode import parse_message

        def inner(_, data: bytearray) -> None:
            now = time.monotonic()
            stamp = datetime.now(timezone.utc).isoformat()
            parsed = parse_message(f"{stamp}\t{uuid}\t{data.hex()}")
            for sub in parsed.get("ACCGYRO", ()):
                self._motion(now, sub["data"])
            for sub in parsed.get("OPTICS", ()):
                self._optics(now, sub["data"])

        return inner

    def _motion(self, now: float, rows: Any) -> None:
        n = len(rows)
        for i, row in enumerate(rows):
            t = now - (n - 1 - i) / ACC_RATE
            g = self.gestures.process(t, row[0:3], row[3:6])
            if g:
                self.out.put(("gesture", t, g))

    def _optics(self, now: float, rows: Any) -> None:
        if len(rows) == 0 or len(rows[0]) <= max(RED):
            return
        n = len(rows)
        times = [now - (n - 1 - i) / OPTICS_RATE for i in range(n)]
        red = [(row[RED[0]] + row[RED[1]]) / 2 for row in rows]
        for t in self.heart.process(times, red):
            self.out.put(("beat", t, self.heart.bpm))
