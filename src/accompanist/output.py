"""All MIDI leaves through SafeOutput so we always know what is sounding.

That knowledge is what makes the panic button trustworthy: it can turn off
exactly the notes we started, plus broadcast All Notes Off / All Sound Off.
"""
from __future__ import annotations

import heapq
import itertools
from typing import Optional

import mido


class RecordingPort:
    """Stand-in for a real MIDI port (tests and `simulate`)."""

    def __init__(self) -> None:
        self.sent: list[mido.Message] = []

    def send(self, msg: mido.Message) -> None:
        self.sent.append(msg)


class SafeOutput:
    def __init__(self, port) -> None:
        self._port = port
        self._sounding: set[tuple[int, int]] = set()
        self._sched: list[tuple[float, int, int, int, int]] = []
        self._counter = itertools.count()
        self._gen: dict[tuple[int, int], int] = {}
        # Scheduled note-ons (humanised timing): (t, seq, channel, note, velocity, off_at or None)
        self._ons: list[tuple[float, int, int, int, int, Optional[float]]] = []

    @property
    def sounding(self) -> set[tuple[int, int]]:
        return set(self._sounding)

    def _send(self, msg: mido.Message) -> None:
        self._port.send(msg)

    def note_on(self, channel: int, note: int, velocity: int) -> None:
        key = (channel, note)
        if key in self._sounding:
            self._send(mido.Message("note_off", channel=channel, note=note, velocity=0))
        self._gen[key] = self._gen.get(key, 0) + 1  # invalidates any scheduled off for the old note
        self._send(mido.Message("note_on", channel=channel, note=note, velocity=velocity))
        self._sounding.add(key)

    def note_off(self, channel: int, note: int) -> None:
        key = (channel, note)
        if key in self._sounding:
            self._send(mido.Message("note_off", channel=channel, note=note, velocity=0))
            self._sounding.discard(key)

    def control_change(self, channel: int, control: int, value: int) -> None:
        self._send(mido.Message("control_change", channel=channel, control=control,
                                value=min(max(int(value), 0), 127)))

    def note_off_at(self, t: float, channel: int, note: int) -> None:
        gen = self._gen.get((channel, note), 0)
        heapq.heappush(self._sched, (t, next(self._counter), channel, note, gen))

    def note_on_at(self, t: float, channel: int, note: int, velocity: int,
                   off_at: Optional[float] = None) -> None:
        """Start a note at time t (a little later than now: humanised timing); with off_at,
        also end it then. Panic, or cancel_ons(channel), drops it if it has not started."""
        heapq.heappush(self._ons, (t, next(self._counter), channel, note, velocity, off_at))

    def cancel_ons(self, channel: int) -> None:
        """Drop the scheduled note-ons of a channel (e.g. the pad was released)."""
        self._ons = [e for e in self._ons if e[2] != channel]
        heapq.heapify(self._ons)

    def flush(self, now: float) -> None:
        while self._ons and self._ons[0][0] <= now:
            _, _, ch, n, vel, off_at = heapq.heappop(self._ons)
            self.note_on(ch, n, vel)
            if off_at is not None:
                self.note_off_at(off_at, ch, n)
        while self._sched and self._sched[0][0] <= now:
            _, _, ch, n, gen = heapq.heappop(self._sched)
            if self._gen.get((ch, n), 0) == gen:
                self.note_off(ch, n)

    def panic(self) -> None:
        """Silence everything, immediately, however we got here."""
        for ch, n in sorted(self._sounding):
            self._send(mido.Message("note_off", channel=ch, note=n, velocity=0))
        self._sounding.clear()
        self._sched.clear()
        self._ons.clear()
        for ch in range(16):
            self._send(mido.Message("control_change", channel=ch, control=123, value=0))  # all notes off
            self._send(mido.Message("control_change", channel=ch, control=120, value=0))  # all sound off
