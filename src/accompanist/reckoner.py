"""The ready reckoner: how each song starts and how it finishes, before the show.

What a band leader settles in rehearsal ("how do we start this? how do we finish?") is set
per song ([start] and [ending] in its song file) and shown here, song by song for a set:
the patch, meter, tempo and keys, the start and the finish in words, and which band plays.
`accompanist reckoner --set NAME` prints it, the stage screen shows the current song's,
and `accompanist rehearse --song NAME` plays a song's start and finish, the band alone,
so you can hear them and choose. Pure (no MIDI, no clock).
"""
from __future__ import annotations

from typing import Any, Optional

METERS = {3: "3/4", 4: "4/4", 5: "5/4", 6: "6/8", 7: "7/8 (3+2+2)"}
START_SHAPES = ("count", "intro", "drums", "you", "drone")
FINISH_SHAPES = ("chord", "button", "tag", "ritardando", "piano-tag", "random")


def describe_start(cfg: Any) -> str:
    s, song = cfg.start, cfg.song
    beats = song.count or cfg.pulse.beats_per_bar
    clicks = " ".join(str(i + 1) for i in range(beats))
    bars = f"{s.bars} bar{'s' if s.bars != 1 else ''}"
    return {
        "count": f"s: a bar of clicks ({clicks}), then the band on the 1" + (
                 " from the top of the chart" if cfg.harmony.model == "chart" else
                 ": drums and bass first, the pad once it hears you"),
        "intro": f"s: clicks ({clicks}), then the band plays {bars} on the " + (
                 "chart's first chord" if cfg.harmony.model == "chart" else "home chord") +
                 ", the drums filling you in; you come in on the next 1 (the top)",
        "drums": f"s: clicks ({clicks}), then the drums alone for {bars}; you come in on the "
                 f"next 1 and the band with you",
        "you": "you start alone, no count: the band comes in when it has your tempo",
        "drone": "s: the " + ("chart's first chord" if cfg.harmony.model == "chart" else
                              "home chord") + " holds in free time (an alap over it); s again "
                 "counts the band in",
    }.get(s.shape, s.shape)


def describe_finish(cfg: Any) -> str:
    e = cfg.ending
    fill = ", the drums filling into it" if e.fill else ""
    bars = lambda n: f"{n} bar{'s' if n != 1 else ''}"
    return {
        "chord": f"f: the last chord on the next 1{fill}, ringing {e.ring_s:g} s as it fades",
        "button": "f: one short, tight hit on the next 1",
        "tag": f"f: the band plays {bars(e.tag_bars)} more, then the last chord{fill}",
        "ritardando": f"f: the band slows over {bars(e.rit_bars)} to {e.rit_to:.0%} of the "
                      f"tempo, into a held last chord",
        "piano-tag": "f: the band drops out for a bar of soft piano, then the last chord",
        "random": "f: one of chord, button, tag, ritardando or piano-tag, chosen then",
    }.get(e.shape, e.shape)


def band(cfg: Any) -> list[str]:
    out = []
    if cfg.pad.enabled:
        out.append("pad")
    if cfg.pulse.enabled:
        out.append("bass")
    if cfg.drums.enabled:
        style = getattr(cfg.drums, "style", "") or cfg.drums.pattern
        out.append(f"drums ({style})" if style else "drums")
    if cfg.percussion.enabled:
        out.append("percussion" + (" (tuned)" if getattr(cfg.percussion, "tuned", False) else ""))
    if cfg.piano.enabled:
        out.append("piano" + (f" (comps {cfg.piano.comp:g})" if cfg.piano.comp > 0 else ""))
    if cfg.response.enabled:
        out.append("guitar")
    return out


def harmony(cfg: Any) -> str:
    from pathlib import Path

    h = cfg.harmony
    if h.model == "chart" and h.chart:
        return f"chart: {Path(h.chart).stem}" + (f", transposed {h.transpose:+d}" if h.transpose else "")
    if h.keys:
        return " → ".join(k.strip() for k in h.keys.split(","))
    if h.root:
        return f"{h.root} {h.mode}"
    return "key heard from you"


def card(cfg: Any, name: str, style: Optional[str] = None) -> dict:
    """One song's line in the reckoner (JSON-able, for the stage screen too)."""
    s = cfg.song
    return {"name": name, "title": s.title or name, "patch": s.patch, "style": style,
            "transpose": s.transpose,
            "meter": METERS.get(s.count, "") if s.count else "",
            "tempo": s.tempo, "harmony": harmony(cfg),
            "start_shape": cfg.start.shape, "start": describe_start(cfg),
            "finish_shape": cfg.ending.shape, "finish": describe_finish(cfg),
            "band": band(cfg)}


def format_card(c: dict, number: Optional[int] = None) -> str:
    head = f"{number}. " if number is not None else ""
    facts = " · ".join(x for x in (
        f"patch {c['patch']}" if c["patch"] else "",
        c["meter"], f"{c['tempo']:g} bpm" if c["tempo"] else "tempo from you", c["harmony"],
        f"{c['transpose']:+d} semitones" if c.get("transpose") else "",
        f"{c['style']} style" if c.get("style") else "") if x)
    pad = " " * len(head)
    return (f"{head}{c['title']}\n{pad}   {facts}\n"
            f"{pad}   Start  ({c['start_shape']}): {c['start']}\n"
            f"{pad}   Finish ({c['finish_shape']}): {c['finish']}\n"
            f"{pad}   Band:  {', '.join(c['band']) or 'nobody'}")
