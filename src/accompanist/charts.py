"""Chord charts from MusicXML (e.g. exported from iReal Pro): chords, bars and form.

Reads the <harmony> chord symbols (root, kind, degrees, slash bass) and where they fall
in each bar, then unrolls the form (repeats and 1st/2nd endings) into a flat list of bars,
which the chart harmony model plays through and loops. Notes in the file are ignored:
a chart is chords, not a melody.

Pure: no clock, no MIDI. Errors are ConfigError with a readable message.
"""
from __future__ import annotations

import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from .config import ConfigError

STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
FLAT_NAMES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")

# MusicXML <kind> values -> semitones above the root.
KINDS = {
    "major": (0, 4, 7), "minor": (0, 3, 7), "augmented": (0, 4, 8), "diminished": (0, 3, 6),
    "dominant": (0, 4, 7, 10), "major-seventh": (0, 4, 7, 11), "minor-seventh": (0, 3, 7, 10),
    "diminished-seventh": (0, 3, 6, 9), "augmented-seventh": (0, 4, 8, 10),
    "half-diminished": (0, 3, 6, 10), "major-minor": (0, 3, 7, 11), "major-sixth": (0, 4, 7, 9),
    "minor-sixth": (0, 3, 7, 9), "dominant-ninth": (0, 4, 7, 10, 14),
    "major-ninth": (0, 4, 7, 11, 14), "minor-ninth": (0, 3, 7, 10, 14),
    "dominant-11th": (0, 4, 7, 10, 14, 17), "major-11th": (0, 4, 7, 11, 14, 17),
    "minor-11th": (0, 3, 7, 10, 14, 17), "dominant-13th": (0, 4, 7, 10, 14, 21),
    "major-13th": (0, 4, 7, 11, 14, 21), "minor-13th": (0, 3, 7, 10, 14, 21),
    "suspended-second": (0, 2, 7), "suspended-fourth": (0, 5, 7), "power": (0, 7),
    "pedal": (0,),
}
KIND_TEXT = {"major": "", "minor": "m", "augmented": "+", "diminished": "dim", "dominant": "7",
             "major-seventh": "maj7", "minor-seventh": "m7", "diminished-seventh": "dim7",
             "half-diminished": "m7b5", "major-sixth": "6", "minor-sixth": "m6",
             "suspended-second": "sus2", "suspended-fourth": "sus4", "power": "5"}
# Degree number -> semitones in a major scale (for <degree> add/alter/subtract).
DEGREE = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7, 6: 9, 7: 11, 9: 14, 11: 17, 13: 21}


@dataclass(frozen=True)
class Chord:
    root: int                      # pitch class
    intervals: tuple[int, ...]     # semitones above the root (may exceed 12)
    bass: Optional[int] = None     # slash bass pitch class
    text: str = ""                 # as written on the chart, e.g. "C7(b9)", "Dm7b5"

    @property
    def pitch_classes(self) -> frozenset[int]:
        pcs = {(self.root + i) % 12 for i in self.intervals}
        if self.bass is not None:
            pcs.add(self.bass)
        return frozenset(pcs)

    def transposed(self, semitones: int) -> "Chord":
        if semitones % 12 == 0:
            return self
        bass = None if self.bass is None else (self.bass + semitones) % 12
        root = (self.root + semitones) % 12
        return Chord(root, self.intervals, bass, _retitle(self.text, self.root, root, self.bass, bass))


@dataclass
class Bar:
    number: str
    changes: list[tuple[float, Chord]] = field(default_factory=list)   # (beat in bar, chord)
    section: str = ""


# Typical tempi by iReal Pro style name (a starting point when the chart has no tempo:
# iReal's MusicXML carries the style but not the tempo). Checked in order; override with
# --tempo or harmony.chart_bpm.
STYLE_BPM = (("ballad", 60), ("slow", 80), ("medium up", 160), ("up tempo", 220),
             ("medium", 120), ("bossa", 130), ("samba", 180), ("latin", 150), ("waltz", 140),
             ("even 8", 110), ("rock", 110), ("funk", 100))


def style_bpm(style: str) -> Optional[float]:
    s = style.lower()
    return next((float(b) for key, b in STYLE_BPM if key in s), None)


@dataclass
class Chart:
    title: str
    beats_per_bar: int
    bars: list[Bar]                 # the form unrolled: repeats and endings already applied
    notes: list[str] = field(default_factory=list)   # things we could not follow, for the user
    style: str = ""                 # e.g. "Ballad" (iReal Pro writes it as the lyricist)

    def chord_at(self, beat: int) -> tuple[Chord, int, Bar]:
        """(chord, bar index, bar) sounding on beat `beat` of the (looping) form."""
        total = len(self.bars) * self.beats_per_bar
        beat %= total
        index, in_bar = divmod(beat, self.beats_per_bar)
        chord = self._last_before(index, in_bar)
        return chord, index, self.bars[index]

    def _last_before(self, index: int, in_bar: float) -> Chord:
        for i in range(index, index - len(self.bars), -1):
            bar = self.bars[i % len(self.bars)]
            here = [ch for t, ch in bar.changes if i != index or t <= in_bar + 1e-6]
            if here:
                return here[-1]
        raise ConfigError(f"chart '{self.title}' has no chords")


def _name(pc: int) -> str:
    return FLAT_NAMES[pc % 12]


def _retitle(text: str, old_root: int, new_root: int, old_bass, new_bass) -> str:
    """Swap the root (and slash bass) names in a chord label after transposing."""
    for n in sorted({_name(old_root), *[k for k, v in _ALIASES.items() if v == old_root]}, key=len, reverse=True):
        if text.startswith(n):
            text = _name(new_root) + text[len(n):]
            break
    if old_bass is not None and "/" in text:
        text = text.rsplit("/", 1)[0] + "/" + _name(new_bass)
    return text


_ALIASES = {"C#": 1, "D#": 3, "F#": 6, "G#": 8, "A#": 10, "Cb": 11, "Fb": 4, "E#": 5, "B#": 0}


def _pitch(step: str, alter: Optional[str]) -> int:
    return (STEPS[step.strip().upper()] + round(float(alter or 0))) % 12


def _spelled(step: str, alter: Optional[str]) -> str:
    a = round(float(alter or 0))
    return step.strip().upper() + ("#" * a if a > 0 else "b" * -a)


def parse_harmony(h: ET.Element) -> Optional[Chord]:
    """One <harmony> element -> Chord (None for 'no chord')."""
    root_el = h.find("root")
    kind_el = h.find("kind")
    kind = (kind_el.text or "").strip() if kind_el is not None else "major"
    if kind == "none" or root_el is None:
        return None
    step, alter = root_el.findtext("root-step"), root_el.findtext("root-alter")
    root = _pitch(step, alter)
    intervals = list(KINDS.get(kind, KINDS["major"]))
    extra = []
    for d in h.findall("degree"):
        value = int(d.findtext("degree-value", "0"))
        alt = round(float(d.findtext("degree-alter", "0") or 0))
        kind_of = (d.findtext("degree-type") or "add").strip()
        base = DEGREE.get(value, DEGREE.get(value % 7 or 7, 0))
        same = [i for i in intervals if abs(i - base) <= 1 and (i % 12) != 0 or (i == base)]
        if kind_of == "subtract":
            intervals = [i for i in intervals if i not in same]
        elif kind_of == "alter":
            intervals = [i for i in intervals if i not in same] + [base + alt]
        else:
            intervals.append(base + alt)
        extra.append(("b" if alt < 0 else "#" if alt > 0 else "") + str(value) if kind_of != "subtract"
                     else f"no{value}")
    bass_el = h.find("bass")
    bass = None
    bass_txt = ""
    if bass_el is not None:
        bass = _pitch(bass_el.findtext("bass-step"), bass_el.findtext("bass-alter"))
        bass_txt = "/" + _spelled(bass_el.findtext("bass-step"), bass_el.findtext("bass-alter"))
    shown = kind_el.get("text") if kind_el is not None and kind_el.get("text") is not None \
        else KIND_TEXT.get(kind, "")
    text = _spelled(step, alter) + shown + (f"({','.join(extra)})" if extra else "") + bass_txt
    return Chord(root, tuple(sorted(set(intervals))), bass, text)


def _root(path: Path) -> ET.Element:
    try:
        if path.suffix.lower() == ".mxl":           # compressed MusicXML
            with zipfile.ZipFile(path) as z:
                inner = next(n for n in z.namelist() if n.endswith((".xml", ".musicxml"))
                             and not n.startswith("META-INF"))
                return ET.fromstring(z.read(inner))
        return ET.parse(path).getroot()
    except (ET.ParseError, zipfile.BadZipFile, StopIteration, OSError) as e:
        raise ConfigError(f"{path}: not a readable MusicXML file ({e})") from None


def load(path: str | Path) -> Chart:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"chart not found: {p}")
    root = _root(p)
    part = root.find("part")
    if part is None:
        raise ConfigError(f"{p}: no <part> in this MusicXML file")
    title = root.findtext("work/work-title") or root.findtext("movement-title") or p.stem
    divisions, beats, beat_type = 1, 4, 4
    raw = []          # (Bar, forward_repeat, backward_repeat, ending_numbers, ending_ends)
    notes: list[str] = []
    for m in part.findall("measure"):
        attrs = m.find("attributes")
        if attrs is not None:
            divisions = int(attrs.findtext("divisions") or divisions)
            if attrs.find("time") is not None:
                beats = int(attrs.findtext("time/beats") or beats)
                beat_type = int(attrs.findtext("time/beat-type") or beat_type)
        bar = Bar(m.get("number", str(len(raw) + 1)))
        section = m.findtext("direction/direction-type/rehearsal")
        if section:
            bar.section = section.strip()
        pos = 0.0
        forward = backward = False
        endings: set[int] = set()
        ending_ends = False
        for el in m:
            if el.tag == "harmony":
                ch = parse_harmony(el)
                if ch is not None:
                    offset = float(el.findtext("offset") or 0) / divisions
                    bar.changes.append((pos + offset, ch))
            elif el.tag in ("note", "forward"):
                if el.find("chord") is None:
                    pos += float(el.findtext("duration") or 0) / divisions * beat_type / 4
            elif el.tag == "backup":
                pos -= float(el.findtext("duration") or 0) / divisions * beat_type / 4
            elif el.tag == "barline":
                rep = el.find("repeat")
                if rep is not None:
                    forward |= rep.get("direction") == "forward"
                    backward |= rep.get("direction") == "backward"
                end = el.find("ending")
                if end is not None:
                    nums = {int(x) for x in end.get("number", "").replace(" ", "").split(",") if x.isdigit()}
                    if end.get("type") == "start":
                        endings |= nums
                    else:
                        endings |= nums
                        ending_ends = True
            elif el.tag == "direction":
                for tag in ("segno", "coda"):
                    if el.find(f"direction-type/{tag}") is not None:
                        notes.append(f"bar {bar.number}: {tag} marking ignored (the form plays straight through)")
                words = el.findtext("direction-type/words")
                if words and any(w in words.upper() for w in ("D.C", "D.S", "FINE", "CODA")):
                    notes.append(f"bar {bar.number}: '{words.strip()}' ignored (the form plays straight through)")
        bar.changes.sort(key=lambda x: x[0])
        raw.append((bar, forward, backward, endings, ending_ends))
    if not any(b.changes for b, *_ in raw):
        raise ConfigError(f"{p}: no chord symbols (<harmony>) found")
    style = (root.findtext("identification/creator[@type='lyricist']") or "").strip()
    return Chart(title, beats, _unroll(raw), notes, style)


def _unroll(raw) -> list[Bar]:
    """Apply repeats and numbered endings: |: A1 [1. x :| [2. y  ->  A1 x A1 y.

    An ending being skipped runs until its backward repeat (or the next ending), whatever
    its end marker says: iReal Pro marks a two-bar 1st ending as ending after one bar."""
    out, i, start, passno, guard = [], 0, 0, 1, 0
    active: Optional[set[int]] = None
    while i < len(raw) and guard < 10 * len(raw):
        guard += 1
        bar, forward, backward, endings, ending_ends = raw[i]
        if forward and i != start:
            start, passno = i, 1
        if endings:
            active = endings
        skipping = active is not None and passno not in active
        if skipping:
            if backward:
                active = None                 # the skipped bracket is over
            i += 1
            continue
        out.append(bar)
        if active is not None and ending_ends:
            active = None
        if backward and passno == 1:
            passno, i, active = 2, start, None
            continue
        i += 1
    return out


# Colour tones you may add to a chart chord by playing them, by chord family: semitones
# above the root -> label. Notes that clash with the family (e.g. the 11 over a major
# chord) are left out, so playing them never changes the pad.
TENSIONS = {
    "major": {2: "9", 6: "#11", 9: "13"},
    "dominant": {1: "b9", 2: "9", 3: "#9", 6: "#11", 8: "b13", 9: "13"},
    "minor": {2: "9", 5: "11", 9: "13"},
    "half-diminished": {2: "9", 5: "11", 8: "b13"},
    "diminished": {2: "9", 5: "11", 8: "b13", 11: "maj7"},
    "other": {2: "9", 9: "13"},
}


def family(intervals) -> str:
    ivs = {i % 12 for i in intervals}
    if {3, 6, 9} <= ivs and 10 not in ivs:
        return "diminished"
    if {3, 6, 10} <= ivs:
        return "half-diminished"
    if {4, 10} <= ivs:
        return "dominant"
    if 3 in ivs and 7 in ivs:
        return "minor"
    if 4 in ivs:
        return "major"
    return "other"


def with_tensions(chord: Chord, added: list[int]) -> Chord:
    """The chord with extra colour tones (semitones above the root, 1-11) and its label."""
    if not added:
        return chord
    labels = [TENSIONS[family(chord.intervals)][i] for i in added]
    text = chord.text
    base, bass = (text.rsplit("/", 1) + [""])[:2] if chord.bass is not None else (text, "")
    base = base[:-1] + "," + ",".join(labels) + ")" if base.endswith(")") else base + f"({','.join(labels)})"
    return Chord(chord.root, tuple(sorted(set(chord.intervals) | {i + 12 for i in added})),
                 chord.bass, base + (f"/{bass}" if bass else ""))


class ChartModel:
    """Harmony model that plays a chart: the chord for each beat comes from the chart,
    in time with the beat clock, looping the form. Your playing sets the tempo; the pad
    still chooses its own voicings.

    Beat-driven: the engine calls restart() when the beat clock starts (or on a count-in /
    restart action) and on_beat() on every beat, so the chart's bar 1 lands on a beat.
    """

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        path = cfg.harmony.chart
        if not path:
            raise ConfigError("harmony.model = \"chart\" needs a chart: harmony.chart = "
                              "\"charts/NAME.musicxml\" or `--chart FILE`")
        self.chart = load(path)
        self.beats_per_bar = self.chart.beats_per_bar
        self._next = 0
        self.pos: Optional[int] = None          # the current beat of the form (None: not started)
        self._chord: Optional[Chord] = None    # the chart chord sounding now
        self._heard: Counter = Counter()        # pitch classes you played during it
        self._added: list[int] = []             # colour tones added to it (semitones)

    @property
    def default_bpm(self) -> Optional[float]:
        """--tempo / harmony.chart_bpm, else a typical tempo for the chart's style."""
        return self.cfg.harmony.chart_bpm or style_bpm(self.chart.style)

    # ---- HarmonyModel ---------------------------------------------------------------
    def observe(self, onset) -> None:
        """The chart decides the chords; what you play only colours them (harmony.melody_colors)."""
        if self.pos is not None:
            self._heard[onset.note % 12] += 1

    def _colour(self, chord: Chord) -> Chord:
        """Add the chord's colour tones you have played (melody_min_notes times) while it lasts,
        up to melody_max_tensions; once added they stay until the chord changes."""
        h = self.cfg.harmony
        if not h.melody_colors:
            return chord
        allowed = TENSIONS[family(chord.intervals)]
        present = {i % 12 for i in chord.intervals}
        for pc, n in self._heard.most_common():
            iv = (pc - chord.root) % 12
            if (n >= h.melody_min_notes and iv in allowed and iv not in present
                    and iv not in self._added and len(self._added) < h.melody_max_tensions):
                self._added.append(iv)
        return with_tensions(chord, [i for i in self._added if i not in present])

    def propose(self, now: float):
        from .harmony import Voicing

        chord, _, _ = self.chart.chord_at(self.pos or 0)
        chord = self._colour(chord.transposed(self.cfg.harmony.transpose))
        bass = chord.bass if chord.bass is not None else chord.root
        base = 12 * (self.cfg.pad.octave + 1) + bass
        notes = tuple(sorted({base} | {base + 12 + (pc - bass) % 12 for pc in chord.pitch_classes}))
        ivs = {i % 12 for i in chord.intervals}
        third = 4 if 4 in ivs else 3 if 3 in ivs else None
        return Voicing(bass, third, notes, chord.text, scheduled=True)

    # ---- beat-driven ------------------------------------------------------------------
    def restart(self) -> None:
        """The next beat is bar 1, beat 1."""
        self._next, self.pos = 0, None
        self._chord, self._heard, self._added = None, Counter(), []

    def on_beat(self, beat_t: float) -> None:
        self.pos = self._next
        self._next += 1
        chord = self.chart.chord_at(self.pos)[0]
        if chord is not self._chord:           # a new chord: its colour starts from scratch
            self._chord, self._heard, self._added = chord, Counter(), []

    @property
    def position(self) -> Optional[dict]:
        if self.pos is None:
            return None
        _, index, _ = self.chart.chord_at(self.pos)
        section = next((self.chart.bars[i].section for i in range(index, -1, -1)
                        if self.chart.bars[i].section), "")
        return {"bar": index + 1, "bars": len(self.chart.bars), "section": section,
                "beat": self.pos % self.beats_per_bar + 1, "title": self.chart.title}
