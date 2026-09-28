"""Record what you actually play, replay it offline as many times as you like.

A take is a JSON Lines file: one header line, then one line per note-on with its
time (seconds from your first note), pitch and velocity. Flushed line by line,
so a crash mid-take loses nothing.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

FORMAT = "accompanist-take/1"


class TakeError(ValueError):
    pass


class Recorder:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "w", encoding="utf-8")
        self._t0: float | None = None
        self._f.write(json.dumps({"format": FORMAT, "created": datetime.now().isoformat(timespec="seconds")}) + "\n")
        self._f.flush()

    def note_on(self, t: float, note: int, velocity: int, source: str = "") -> None:
        if self._t0 is None:
            self._t0 = t
        row = {"t": round(t - self._t0, 4), "note": note, "vel": velocity, "src": source}
        self._f.write(json.dumps(row) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


def auto_path(directory: str | Path = "takes") -> Path:
    return Path(directory) / f"take-{datetime.now():%Y%m%d-%H%M%S}.jsonl"


def load_take(path: str | Path) -> list[tuple[float, int, int]]:
    """Returns [(t, note, velocity)] sorted by time, starting at t = 0."""
    p = Path(path)
    if not p.exists():
        raise TakeError(f"take not found: {p}")
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        raise TakeError(f"{p} is empty")
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as e:
        raise TakeError(f"{p}: first line is not valid JSON") from e
    if header.get("format") != FORMAT:
        raise TakeError(f"{p}: not an accompanist take (expected format '{FORMAT}')")
    onsets = []
    for i, ln in enumerate(lines[1:], start=2):
        try:
            r = json.loads(ln)
            onsets.append((float(r["t"]), int(r["note"]), int(r["vel"])))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise TakeError(f"{p}: bad note on line {i}") from e
    if not onsets:
        raise TakeError(f"{p} contains no notes")
    return sorted(onsets)
