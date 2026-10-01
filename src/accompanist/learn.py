"""`accompanist learn`: press each switch when asked; your [controls] are written for you.

This module is the pure part: deciding what a switch or an expression pedal sends, and
rewriting config.toml's text. The CLI does the prompting and the MIDI listening.

A message here is (port, kind, number, value) with kind 'cc', 'pc' or 'note'
(value 0 for a note-off). Each switch is pressed twice: a momentary CC switch sends 127
(then 0 on release) both times; a latching one sends 127 the first time and 0 the next.
"""
from __future__ import annotations

from typing import Optional

Msg = tuple[str, str, int, int]          # (port, kind, number, value)

# What can be learnt, in the order it is asked for.
STEPS = (
    ("tap_tempo", "COUNT OFF (tap the tempo: 3 = waltz, 4, 5, 6, 7)"),
    ("lock_toggle", "LOCK / UNLOCK the tempo"),
    ("chord_toggle", "HOLD / RELEASE the chord"),
    ("panic", "PANIC (silence everything)"),
    ("resume", "RESUME after a panic (the panic switch again = one switch for both)"),
    ("song_start", "START THE SONG: count in at its tempo (a chart from the top)"),
    ("finish", "FINISH the song: a last chord on the next 1"),
)
EXPRESSION_TARGETS = ("pad.feel", "pulse.feel", "drums.feel", "response.feel")
MIN_SWEEP_VALUES = 8                     # an expression pedal sends many different values


def assign(switches: dict[str, tuple[str, int, bool]], action: str,
           switch: tuple[str, int, bool]) -> str:
    """Give `switch` (kind, number, latching) to `action` in `switches`. One switch can't do
    two things, with one exception: the panic switch pressed again for resume becomes a
    panic/resume toggle. Returns what happened, for the display."""
    kind, number, latching = switch
    taken = next((a for a, (k, n, _) in switches.items() if (k, n) == (kind, number)), None)
    if taken is None:
        switches[action] = switch
        return f"{kind} {number}" + (" (toggles on each press)" if latching else "")
    if taken == "panic" and action == "resume":
        switches["panic_toggle"] = switches.pop("panic")
        return f"{kind} {number}: one switch for panic, and again to resume"
    return f"that switch is already {taken}: skipped (run learn again to use another one)"


def press_of(messages: list[Msg]) -> Optional[Msg]:
    """The message that is the press: a program change, a note-on, or a CC."""
    return next((m for m in messages if m[1] in ("pc", "cc") or (m[1] == "note" and m[3] > 0)), None)


def classify_switch(first: list[Msg], second: list[Msg]) -> Optional[tuple[str, str, int, bool]]:
    """(port, kind, number, latching) from the messages of two presses of one switch."""
    a, b = press_of(first), press_of(second)
    if a is None:
        return None
    port, kind, number, value = a
    latching = False
    if kind == "cc" and b is not None and b[1] == "cc" and b[2] == number:
        latching = (value >= 64) != (b[3] >= 64)          # 127 then 0: it toggles
    return port, kind, number, latching


def pick_expression(messages: list[Msg]) -> Optional[tuple[str, int]]:
    """(port, cc) of the controller swept through the most values (an expression pedal)."""
    values: dict[tuple[str, int], set] = {}
    for port, kind, number, value in messages:
        if kind == "cc":
            values.setdefault((port, number), set()).add(value)
    if not values:
        return None
    best = max(values, key=lambda k: len(values[k]))
    return best if len(values[best]) >= MIN_SWEEP_VALUES else None


def controls_toml(switches: dict[str, tuple[str, int, bool]],
                  expressions, program_bank: int = 0) -> str:
    """The [controls] table: switches {action: (kind, number, latching)}; expressions: one
    (cc, parameter) or a list of them (expression pedals)."""
    lines = ["[controls]  # written by `accompanist learn`"]
    if program_bank:
        lines.append(f"program_bank = {program_bank}   # the pedal's bank switches don't remap it")
    for action, (kind, number, latching) in switches.items():
        key = f'"{kind}:{number}"'
        if latching:
            lines.append(f'{key} = {{ action = "{action}", latching = true }}')
        else:
            lines.append(f'{key} = "{action}"')
    if expressions and isinstance(expressions[0], int):
        expressions = [expressions]                 # a single (cc, parameter)
    for cc, target in expressions or []:
        lines.append(f'"cc:{cc}" = "{target}"   # expression pedal')
    return "\n".join(lines) + "\n"


def update_config(text: str, controls: str, pedal_port: Optional[str] = None) -> str:
    """config.toml's text with its [controls] table replaced by `controls`, and (if the pedal
    is a port not yet among the inputs) a control input for it."""
    lines = text.splitlines()
    out, skipping = [], False
    for line in lines:
        header = line.strip().startswith("[")
        if line.strip() == "[controls]" or line.strip().startswith("[controls]"):
            skipping = True
            continue
        if skipping and header:
            skipping = False
        if not skipping:
            out.append(line)
    if pedal_port and not any(f'"{pedal_port}"' in line for line in lines):
        block = ["", "[[inputs]]", 'name = "pedal"', f'port = "{pedal_port}"', 'role = "control"']
        last = max((i for i, line in enumerate(out) if line.strip() == "[[inputs]]"), default=None)
        if last is None:
            out += block
        else:
            end = next((i for i in range(last + 1, len(out)) if out[i].strip().startswith("[")), len(out))
            while end > last + 1 and not out[end - 1].strip():
                end -= 1
            out[end:end] = block
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n\n" + controls
