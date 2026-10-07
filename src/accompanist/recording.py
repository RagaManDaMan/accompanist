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
    def __init__(self, path: str | Path, run: dict | None = None) -> None:
        """run: how the band was started (song, key, mode, time, tempo, preset...), kept in
        the header so a replay can be set up the same way."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "w", encoding="utf-8")
        self._t0: float | None = None
        head = {"format": FORMAT, "created": datetime.now().isoformat(timespec="seconds")}
        if run:
            head["run"] = {k: v for k, v in run.items() if v is not None}
        self._f.write(json.dumps(head) + "\n")
        self._f.flush()

    def note_on(self, t: float, note: int, velocity: int, source: str = "") -> None:
        self._write(t, {"note": note, "vel": velocity, "src": source})

    def action(self, t: float, name: str) -> None:
        """A control action (lock, unlock, panic, ...) so replay can repeat it at the same moment."""
        self._write(t, {"action": name})

    def _write(self, t: float, fields: dict) -> None:
        if self._t0 is None:
            self._t0 = t
        self._f.write(json.dumps({"t": round(t - self._t0, 4), **fields}) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


def auto_path(directory: str | Path = "takes", name: str | None = None) -> Path:
    """takes/take-<date>-<time>.jsonl, or takes/<name>-<date>-<time>.jsonl (e.g. the song)."""
    slug = "".join(ch if ch.isalnum() else "-" for ch in (name or "take").lower()).strip("-")
    return Path(directory) / f"{slug or 'take'}-{datetime.now():%Y%m%d-%H%M%S}.jsonl"


def _rows(path: str | Path) -> list[dict]:
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
    rows = []
    for i, ln in enumerate(lines[1:], start=2):
        try:
            r = json.loads(ln)
            float(r["t"])
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            raise TakeError(f"{p}: bad line {i}") from e
        r["_line"] = i
        rows.append(r)
    return rows


def load_take(path: str | Path) -> list[tuple[float, int, int]]:
    """Returns the notes, [(t, note, velocity)] sorted by time (t = 0 is the first event)."""
    onsets = []
    for r in _rows(path):
        if "action" in r:
            continue
        try:
            onsets.append((float(r["t"]), int(r["note"]), int(r["vel"])))
        except (KeyError, ValueError) as e:
            raise TakeError(f"{path}: bad note on line {r['_line']}") from e
    if not onsets:
        raise TakeError(f"{path} contains no notes")
    return sorted(onsets)


def load_actions(path: str | Path) -> list[tuple[float, str]]:
    """The control actions recorded in a take, [(t, action)], on the same clock as load_take."""
    return sorted((float(r["t"]), str(r["action"])) for r in _rows(path) if "action" in r)


def load_run(path: str | Path) -> dict:
    """How the band was started for this take ({} for older takes, or if unreadable: the
    take's own loading says what is wrong with it)."""
    try:
        with open(path, encoding="utf-8") as f:
            head = json.loads(f.readline())
        return head.get("run", {}) if isinstance(head, dict) else {}
    except (OSError, ValueError):
        return {}
