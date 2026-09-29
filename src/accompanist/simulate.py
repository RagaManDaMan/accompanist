"""Run the engine offline in virtual time: against a scripted performance, or a recorded take.

The scripted performance: a D-major phrase that accelerates 70 -> 95 bpm, then a
G-minor phrase that relaxes 95 -> 75 bpm, then silence. It exercises tempo
following, snapping of eighth-note pairs, lagged harmonic response, pulse
start/stop and pad release. A recorded take (see recording.py) does the same job
with your real playing.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

from .config import Config
from .controller import Controller, format_status
from .engine import Engine
from .output import RecordingPort, SafeOutput

PHRASE_D = [62, 64, 66, 62, 67, 69, 66, 62]   # D major-ish, D is home
PHRASE_G = [67, 70, 69, 67, 72, 74, 70, 67]   # G minor-ish, G is home

Onsets = list[tuple[float, int, int]]


def scripted_performance() -> tuple[Onsets, list[tuple[float, float]]]:
    """Returns (onsets, truth) where onsets = [(t, note, vel)], truth = [(t, true_bpm)]."""
    onsets, truth = [], []
    t = 1.0
    for b in range(72):
        if b < 32:
            bpm, note = 70 + 25 * b / 32, PHRASE_D[b % 8]
        else:
            bpm, note = 95 - 20 * (b - 32) / 40, PHRASE_G[b % 8]
        period = 60.0 / bpm
        onsets.append((t, note, 90))
        if b % 4 == 3:                       # an eighth-note pair on every 4th beat
            onsets.append((t + period / 2, note, 70))
        truth.append((t, bpm))
        t += period
    return onsets, truth


# A melody-like rhythm, in beats: quarters, eighth pairs, a dotted figure, held notes.
MELODY_RHYTHM = [1, 1, 0.5, 0.5, 1, 2, 1, 0.5, 0.5, 1, 0.5, 0.5, 1, 1, 1.5, 0.5, 2]
MELODY_NOTES = [62, 64, 66, 67, 69, 67, 66, 64, 62, 69, 71, 69, 67, 66, 64, 62, 62]


def synthetic_melody(
    bpm: Union[float, Callable[[float], float]],
    duration: float,
    jitter: float = 0.0,
    seed: int = 0,
    start: float = 0.0,
    drift: float = 0.0,
) -> tuple[Onsets, list[tuple[float, float]]]:
    """A melody at `bpm` (a number, or a function of time for accelerando/rubato).

    jitter: each note lands off its grid position by up to +-jitter of its own interval
        (loose timing around a steady beat).
    drift: each interval is stretched by a random factor in [1-drift, 1+drift] and the
        error accumulates, so the beat itself wanders (free, rubato-like time).
    Returns (onsets, truth) like scripted_performance().
    """
    rng = random.Random(seed)
    bpm_at = bpm if callable(bpm) else (lambda _t, b=bpm: b)
    onsets, truth = [], []
    t, i = start, 0
    while t < start + duration:
        b = bpm_at(t - start)
        ioi = MELODY_RHYTHM[i % len(MELODY_RHYTHM)] * 60.0 / b
        played = max(start, t + rng.uniform(-jitter, jitter) * ioi)
        onsets.append((played, MELODY_NOTES[i % len(MELODY_NOTES)], 70 + (i * 7) % 30))
        truth.append((t, b))
        t += ioi * (1 + rng.uniform(-drift, drift))
        i += 1
    return sorted(onsets), truth


@dataclass
class SimResult:
    engine: Engine
    port: RecordingPort
    log: list[tuple[float, str]]
    end_time: float
    tempo_trace: list[tuple[float, float, float]] = field(default_factory=list)  # (t, bpm, confidence) each second
    controller: Optional[Controller] = None
    timeline: list[tuple[float, object]] = field(default_factory=list)  # (t, MIDI message) as sent

    def notes_on(self, channel_1_16: int) -> list[float]:
        """Times of the note-ons sent on a channel (1-16, as in the config)."""
        return [t for t, m in self.timeline if m.type == "note_on" and m.channel == channel_1_16 - 1]


def run(
    cfg: Config,
    verbose: bool = False,
    dt: float = 0.005,
    total: Optional[float] = None,
    onsets: Optional[Onsets] = None,
    actions: Optional[list[tuple[float, str]]] = None,
) -> SimResult:
    """actions: [(t, action)] performed through the Controller, e.g. [(30.0, "lock")]."""
    truth: list[tuple[float, float]] = []
    if onsets is None:
        onsets, truth = scripted_performance()
    onsets = sorted(onsets)
    if total is None:  # long enough to see the pad release after the last note
        last = onsets[-1][0] if onsets else 0.0
        total = last + max(cfg.pad.idle_release_s, cfg.pulse.idle_stop_s) + 5.0

    port = RecordingPort()
    ctl = Controller(cfg, SafeOutput(port))
    eng = ctl.engine
    log: list[tuple[float, str]] = []
    trace: list[tuple[float, float, float]] = []
    pending = sorted(actions or [])
    timeline: list[tuple[float, object]] = []
    i, now, last_print, last_trace, last_chord, last_locked, last_held = 0, 0.0, -1.0, -1.0, None, False, False
    while now <= total:
        while i < len(onsets) and onsets[i][0] <= now:
            ctl.on_note(onsets[i][0], onsets[i][1], onsets[i][2])
            i += 1
        while pending and pending[0][0] <= now:
            ctl.do(pending.pop(0)[1], now)
        sent = len(port.sent)
        ctl.tick(now)
        timeline.extend((now, m) for m in port.sent[sent:])
        chord = eng.pad.current.label() if eng.pad.current else None
        if chord != last_chord:
            log.append((now, f"pad -> {chord}"))
            last_chord = chord
        if eng.locked != last_locked:
            log.append((now, "LOCKED" if eng.locked else "unlocked"))
            last_locked = eng.locked
        if eng.chord_held != last_held:
            log.append((now, f"chord HELD ({eng.frozen.label()})" if eng.chord_held else "chord released"))
            last_held = eng.chord_held
        if now - last_trace >= 1.0:
            trace.append((now, eng.tempo.bpm, eng.tempo.confidence))
            last_trace = now
        if verbose and now - last_print >= 2.0:
            true = next((b for (tt, b) in reversed(truth) if tt <= now), None)
            true_s = f"(you: {true:5.1f})" if truth and true and now < truth[-1][0] + 1 else ""
            print(f"{format_status(ctl.get_state(now))}  {true_s}")
            last_print = now
        now += dt
    return SimResult(eng, port, log, now, trace, ctl, timeline)
