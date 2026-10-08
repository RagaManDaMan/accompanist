"""Tāla percussion: the theka (Hindustani) or sarvalaghu (Carnatic) of a tāla, played on
your tabla, mridangam, pakhwaj, dholak, khanjira... in time with the band.

A cycle is the tāla's beats (mātrās / aksharas), each a few strokes: "Dha Dhin Dhin Dha |
..." (one word a beat; strokes inside a beat start with a capital, DhaGe, TiRaKiTa; "." is
a rest). It comes from the song ([percussion] theka), else the tāla's theka in
indian/talas.toml, else a plain sarvalaghu in the song's nadai.

The strokes are played through a kit: kits/NAME.toml maps each stroke to the key your
sampler (a Kontakt tabla, say) plays it on. A stroke the kit doesn't name is found through
its parts (Dha = Na + Ge, Dhin = Tin + Ge) or its kin (Ti -> Te -> Na, Kat -> Ka, a tabla
bol on a mridangam: Dha -> Tham ...), so any theka plays on any kit.

Pure (no clock): the engine calls on_beat() for each beat and tick() to send what is due.
"""
from __future__ import annotations

import heapq
import itertools
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .config import ConfigError
from .responders import humanize_velocity

BUILTIN_KITS = Path(__file__).parent / "kits"
USER_KITS = Path("kits")
STROKE = re.compile(r"[A-Z][a-z]*|\.")
# What a stroke is made of, when the kit has no key for it (both hands at once).
PARTS = {"dha": ("na", "ge"), "dhin": ("tin", "ge"), "dhi": ("tin", "ge"), "dhit": ("te", "ge"),
         "tham": ("tha", "thom"), "dheem": ("dhi", "thom"), "dhom": ("tha", "thom")}
# Its nearest kin otherwise (tabla <-> mridangam, spellings).
KIN = {"ti": "te", "ta": "na", "ra": "te", "re": "te", "tu": "tin", "tun": "tin", "kat": "ka",
       "ke": "ka", "ki": "te", "ghe": "ge", "gi": "ge", "ga": "ge", "kda": "ka",
       "tin": "na", "te": "na", "ka": "ge",
       "tha": "na", "nam": "na", "dhi": "tin", "thom": "ge", "chapu": "na", "cha": "chapu",
       "gumki": "thom", "dheem": "dhin", "tham": "dha", "na": "tha", "ge": "thom",
    "tom": "thom", "tim": "tin", "dim": "dhin", "tam": "tham", "nom": "nam"}
BEAT_FIRST, INSIDE = 1.0, 0.8     # the stroke on the beat, and the ones between
KHALI_SOFT = 0.85                 # khālī beats a little lighter


_FOLD = (("th", "t"), ("dh", "d"), ("kh", "k"), ("gh", "g"), ("bh", "b"), ("ee", "i"),
         ("ii", "i"), ("oo", "u"), ("aa", "a"))


def fold(stroke: str) -> str:
    """A stroke's plain sound, so spellings meet: Thom = Tom, Dheem = Dhim, Tham = Tam."""
    s = "".join(c for c in stroke.lower() if c.isalpha())
    for a, b in _FOLD:
        s = s.replace(a, b)
    out: list[str] = []
    for c in s:
        if not out or out[-1] != c:
            out.append(c)
    return "".join(out)


@dataclass(frozen=True)
class Kit:
    """strokes: each stroke's keys. Several keys for one stroke are alternatives (the same
    stroke sampled more than once): one of them plays each time, as a player varies."""
    name: str
    strokes: dict[str, tuple[int, ...]]

    def _own(self, s: str) -> tuple[int, ...]:
        if s in self.strokes:
            return self.strokes[s]
        f = fold(s)
        return next((v for k, v in self.strokes.items() if fold(k) == f), ())

    def choices(self, stroke: str, _seen: Optional[set] = None,
                kin_first: bool = False) -> list[tuple[int, ...]]:
        """What to play for a stroke: one tuple of alternative keys per part (Dha: Na's, then
        Ge's). Empty if nothing fits. kin_first (a mridangam): a stroke's nearest kin before
        its tabla parts (a mridangam Dhi is one stroke, a tabla Dhi two)."""
        s = stroke.lower()
        own = self._own(s)
        if own:
            return [own]
        seen = _seen or set()
        if s in seen:
            return []
        seen.add(s)
        kin = KIN.get(s) or KIN.get(fold(s))
        if kin_first and kin:
            found = self.choices(kin, set(seen), kin_first)
            if found:
                return found
        if s in PARTS:
            parts = [self.choices(p, set(seen), kin_first) for p in PARTS[s]]
            if all(parts):
                return [alt for p in parts for alt in p]
        if kin:
            return self.choices(kin, seen, kin_first)
        return []

    def notes(self, stroke: str, kin_first: bool = False) -> tuple[int, ...]:
        """One key per part (the first alternative): for checks and tests."""
        return tuple(alts[0] for alts in self.choices(stroke, kin_first=kin_first))

    def pick(self, stroke: str, rng, kin_first: bool = False) -> tuple[int, ...]:
        """The keys to play this time: one alternative per part, varied."""
        return tuple(rng.choice(alts) for alts in self.choices(stroke, kin_first=kin_first))


def load_kit(name: str) -> Kit:
    from .config import _read_toml

    for d in (USER_KITS, BUILTIN_KITS):
        p = d / f"{name}.toml"
        if p.is_file():
            data = _read_toml(p)
            strokes = {}
            for k, v in (data.get("strokes") or {}).items():
                notes = tuple(dict.fromkeys(v)) if isinstance(v, list) else (v,)
                if not all(isinstance(n, int) and 0 <= n <= 127 for n in notes):
                    raise ConfigError(f"kit '{name}' ({p}): {k} = {v!r}: a MIDI note 0-127 "
                                      f"(or a list of them)")
                strokes[k.lower()] = notes
            return Kit(name, strokes)
    have = sorted({p.stem for d in (USER_KITS, BUILTIN_KITS) if d.is_dir() for p in d.glob("*.toml")})
    raise ConfigError(f"unknown kit '{name}'; available: {', '.join(have)} (kits go in "
                      f"./kits/NAME.toml with [strokes] dha = 48 ...)")


AUTO_KITS = {"carnatic": "mridangam", "hindustani": "tabla"}


def available_kits() -> list[str]:
    return sorted({p.stem for d in (USER_KITS, BUILTIN_KITS) if d.is_dir() for p in d.glob("*.toml")})


def kit_for(name: str, tradition: str) -> str:
    """percussion.kit auto: your mridangam kit for a Carnatic tāla, your tabla for a
    Hindustani one (kits/mridangam.toml, kits/tabla.toml), else the General MIDI stand-in."""
    if name != "auto":
        return name
    mine = AUTO_KITS.get(tradition)
    return mine if mine and (USER_KITS / f"{mine}.toml").is_file() else "gm-tabla"


def save_kit(name: str, strokes: dict[str, list[int]], description: str = "") -> Path:
    """Write ./kits/NAME.toml from {stroke: [notes]}."""
    USER_KITS.mkdir(exist_ok=True)
    p = USER_KITS / f"{name}.toml"
    lines = [f"# {description or name}: made with `accompanist kitmap percussion --make {name}`.",
             "# Each stroke and the key(s) your instrument plays it on.",
             f'description = "{description or name}"', "", "[strokes]"]
    for stroke, notes in strokes.items():
        notes = list(dict.fromkeys(notes))             # each key once
        lines.append(f"{stroke} = {notes[0] if len(notes) == 1 else notes}")
    p.write_text("\n".join(lines) + "\n")
    load_kit(name)                                   # it must read back
    return p


def parse_beats(text: str) -> list[list[str]]:
    """'Dha Dhin | DhaGe TiRaKiTa' -> [['Dha'], ['Dhin'], ['Dha', 'Ge'], ['Ti', 'Ra', 'Ki', 'Ta']]."""
    beats = []
    for word in text.replace("|", " ").split():
        strokes = STROKE.findall(word[0].upper() + word[1:]) if word else []
        if not strokes:
            raise ConfigError(f"theka: '{word}' has no strokes (each starts with a capital, "
                              f"'.' is a rest)")
        beats.append(strokes)
    return beats


def sarvalaghu(groups: tuple[int, ...], nadai: int) -> list[list[str]]:
    from . import indian
    from .config import _read_toml

    table = _read_toml(indian.HERE / "talas.toml").get("sarvalaghu", {})
    first, rest = table.get(str(nadai), table["4"])
    out = []
    for g in groups:
        out.append(parse_beats(first)[0])
        out += [parse_beats(rest)[0] for _ in range(g - 1)]
    return out


@dataclass(frozen=True)
class Cycle:
    tala: str
    beats: tuple[tuple[str, ...], ...]     # strokes per beat
    group_starts: frozenset[int]           # 0-based beats that start a vibhāg / anga
    khali: frozenset[int]                  # 0-based open beats


def cycle_for(cfg: Any, name: Optional[str] = None) -> Cycle:
    """The song's cycle: its theka, the tāla's, or sarvalaghu in its nadai."""
    from . import indian

    p = cfg.percussion
    t = indian.tala(name or p.tala or cfg.song.tala)
    if p.theka:
        beats = parse_beats(p.theka)
    elif t.theka:
        beats = parse_beats(t.theka)
    else:
        beats = sarvalaghu(t.groups, p.nadai)
    if len(beats) != t.beats:
        raise ConfigError(f"theka for {t.name}: {len(beats)} beats, but the tāla has {t.beats} "
                          f"({' + '.join(map(str, t.groups))}): one word per beat")
    return Cycle(t.name, tuple(tuple(b) for b in beats), frozenset(t.group_starts),
                 frozenset(k - 1 for k in t.khali))


class TalaPlayer:
    """Plays the cycle, beat by beat, on the percussion channel."""

    def __init__(self, cfg: Any, out, seed: int = 0, tala: Optional[str] = None) -> None:
        self.cfg, self.out = cfg.percussion, out
        self.cycle = cycle_for(cfg, tala)
        self.kit = load_kit(kit_for(self.cfg.kit, self.tradition(cfg, tala)))
        self.rng = random.Random(seed + 37)
        self._queue: list = []
        self._seq = itertools.count()
        self.carnatic = self.tradition(cfg, tala) == "carnatic"
        self.missing = sorted({s for b in self.cycle.beats for s in b
                               if s != "." and not self.kit.notes(s, self.carnatic)})
        self.instrument = "mridangam" if self.carnatic else "tabla"
        self.busy_until = float("-inf")              # a mōrā is playing: no theka till then
        self.mora_text: Optional[str] = None         # the last mōrā, as spoken
        self.pending = None                          # a korvai waiting for its start
        self.design_text: Optional[str] = None       # the last korvai played
        self.design_end_t: Optional[float] = None    # the sam it lands on

    @staticmethod
    def tradition(cfg: Any, tala: Optional[str]) -> str:
        from . import indian
        return indian.tala(tala or cfg.percussion.tala or cfg.song.tala).tradition

    def beats_to_sam(self, form_beat: int) -> int:
        """Beats from this one (included) to the next sam."""
        n = len(self.cycle.beats)
        return n - form_beat % n

    def queue_design(self, design: list, total: int, text: str = "") -> None:
        """A design (a korvai) to play so that it ends on a sam: it starts on whichever
        beat (or pulse within it) that takes."""
        self.pending = (design, total, text)

    def _start_pending(self, beat_t: float, period: float, form_beat: int, gain: float) -> bool:
        """At a beat: if the pending design must start within it to end on a sam, start it."""
        if self.pending is None:
            return False
        design, total, text = self.pending
        nadai = max(1, self.cfg.nadai)
        cycle = len(self.cycle.beats) * nadai
        to_sam = self.beats_to_sam(form_beat) * nadai
        lead = (to_sam - total) % cycle                    # pulses from this beat to its start
        if lead >= nadai:
            return False
        self.pending = None
        self._schedule(design, beat_t + lead * period / nadai, period / nadai, gain)
        self.busy_until = beat_t + (lead + total) * period / nadai + period / (2 * nadai)
        self.design_end_t = beat_t + (lead + total) * period / nadai    # its sam
        self.design_text = text
        return True

    def _schedule(self, design: list, start: float, step: float, gain: float) -> None:
        from . import solkattu

        top = self.cfg.velocity * gain + self.cfg.accent
        for at, syl, accent in design:
            if syl is None:
                continue
            vel = humanize_velocity(min(max(round(top if accent else top * INSIDE), 1), 127),
                                    self.cfg.velocity_spread, self.rng)
            for note in self.kit.pick(solkattu.stroke(syl, self.instrument), self.rng, self.carnatic):
                heapq.heappush(self._queue, (start + at * step, next(self._seq), note, vel))

    def play_mora(self, beat_t: float, period: float, beats: int, gain: float,
                  close: bool = True) -> int:
        """A mōrā from this beat, `beats` beats long, landing on the sam that follows (its
        tām on it, with close). The theka rests meanwhile. Returns its length in pulses."""
        from . import solkattu

        nadai = max(1, self.cfg.nadai)
        total = beats * nadai
        design = solkattu.mora(total, self.rng, close)
        step = period / nadai
        top = self.cfg.velocity * gain + self.cfg.accent
        spoken = []
        for at, syl, accent in design:
            spoken.append(syl or ",")
            if syl is None:
                continue
            vel = humanize_velocity(min(max(round(top if accent else top * INSIDE), 1), 127),
                                    self.cfg.velocity_spread, self.rng)
            stroke = solkattu.stroke(syl, self.instrument)
            for note in self.kit.pick(stroke, self.rng, self.carnatic):
                heapq.heappush(self._queue, (beat_t + at * step, next(self._seq), note, vel))
        self.busy_until = beat_t + total * step + (step / 2 if close else -step / 2)
        self.mora_text = " ".join(spoken)
        return total

    def on_beat(self, beat_t: float, period: float, gain: float, form_beat: int,
                busy: float = 0.0) -> None:
        """busy (0-1, how busily you sing or play): the busier you are, the more the strokes
        between the beats drop out, leaving you room (percussion.breathe)."""
        if self._start_pending(beat_t, period, form_beat, gain):
            return                                         # a korvai begins
        if beat_t < self.busy_until:                       # a mōrā is speaking
            return
        c, cyc = self.cfg, self.cycle
        pos = form_beat % len(cyc.beats)
        strokes = cyc.beats[pos]
        level = c.velocity * gain
        if pos == 0:
            level += c.accent * c.sam_accent               # the sam: lightly (Nelson: a
                                                           # tāla has no built-in accent)
        elif pos in cyc.group_starts:
            level += c.accent / 2
        if pos in cyc.khali:
            level *= KHALI_SOFT
        keep_inside = 1.0 - self.cfg.breathe * busy
        for i, s in enumerate(strokes):
            if s == "." or (i > 0 and self.rng.random() > keep_inside):
                continue
            t = beat_t + i * period / len(strokes)
            if c.timing_ms > 0 and i:
                t += self.rng.uniform(-c.timing_ms, c.timing_ms) / 1000
            vel = humanize_velocity(min(max(round(level * (BEAT_FIRST if i == 0 else INSIDE)), 1),
                                        127), c.velocity_spread, self.rng)
            for note in self.kit.pick(s, self.rng, self.carnatic):
                heapq.heappush(self._queue, (t, next(self._seq), note, vel))

    def tick(self, now: float) -> None:
        ch = self.cfg.channel - 1
        while self._queue and self._queue[0][0] <= now:
            t, _, note, vel = heapq.heappop(self._queue)
            self.out.note_on(ch, note, vel)
            self.out.note_off_at(t + self.cfg.note_length_s, ch, note)

    def reset(self) -> None:
        self._queue.clear()
        self.busy_until = float("-inf")
        self.pending = None
