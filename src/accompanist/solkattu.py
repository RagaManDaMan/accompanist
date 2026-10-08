"""Solkattu: the spoken rhythmic language of Carnatic music, as the percussion speaks it.

A passage is syllables counted in pulses (mātrās; four to a beat by default, the nadai can
change that). Written as plain text, the way the owner's korvai tools do:
  ta ka di mi        four syllables, a pulse each
  tām / taam / dheem a long syllable: two pulses
  ,  ;               a rest of one pulse, of two
  ta3, tām4          a syllable held that many pulses
Designs land on a point of the tāla (the sam): the mōrā, (statement) [gap] (statement)
[gap] (statement), 3s + 2g pulses, ends there (D. P. Nelson, Solkattu Manual, ch. 2-4).
Each syllable is played as a stroke through a table (indian/solkattu.toml): the spoken
and the played follow different logics (of the voice, of the hand), so the table is a
starting point to correct, per instrument.

Pure: no clock, no MIDI.
"""
from __future__ import annotations

import random
import re
import unicodedata
from functools import lru_cache
from typing import Optional

from . import indian
from .config import ConfigError

LONG = {"tam", "taam", "tham", "thaam", "dheem", "dhim", "deem", "naam", "nam_", "thoom",
        "toom", "daam", "jhem", "jem", "kaam"}
# Phrases by length (D. P. Nelson's basic phrases, the owner's KorvaiSheets blocks).
PHRASES = {
    1: ("ta", "di", "tom", "nam", "ki", "din"),
    2: ("ta ka", "di mi", "jo ṇu", "ta ki", "ki ṭa"),
    3: ("ta ki ṭa", "ta di na", "di mi ta", "ta ka di", "ki ṭa ka"),
    4: ("ta ka di mi", "ta ka jo ṇu", "ta ka di ku", "ta din gi ṇa", "ta ki ṭa ka", "di mi ta ka"),
    5: ("ta ka ta ki ṭa", "ta di ki ṭa tom", "ta din gi ṇa tom"),
    6: ("ta ka di mi ta ka", "ta ki ṭa ta ki ṭa"),
    7: ("ta ka di mi ta ki ṭa", "ta ka ta di ki ṭa tom", "ta ka ta din gi ṇa tom"),
    8: ("ta ka di mi ta ka jo ṇu", "ta ki ṭa ta di ki ṭa tom", "ta ki ṭa ta din gi ṇa tom"),
    9: ("ta ka di mi ta ka ta ki ṭa", "ta ka di ku ta di ki ṭa tom", "ta ka di ku ta din gi ṇa tom"),
}
SHORT_STATEMENT = 5      # a statement shorter than this takes a gap of at least...
SOUNDED_GAP = 2          # ...this many pulses, sounded (tām)
MIN_MORA = 7             # the smallest mōrā: (ta) [tām ·] (ta) [tām ·] (ta)


def plain(syllable: str) -> str:
    s = unicodedata.normalize("NFD", syllable.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s


def weight(syllable: str) -> int:
    """Pulses a syllable takes: 1, 2 for a long one (tām, dheem), or a written count (ta3)."""
    m = re.fullmatch(r"([^\d₀-₉]+)([\d₀-₉]+)", syllable)
    if m:
        digits = m.group(2).translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789"))
        return max(1, int(digits))
    s = syllable.lower()
    if "ā" in s or "ī" in s or "ū" in s or "ē" in s or "ō" in s:
        return 2
    return 2 if plain(s) in LONG or re.search(r"(aa|ee|oo)", plain(s)) else 1


def parse(text: str) -> list[tuple[Optional[str], int]]:
    """'ta ka , tām' -> [('ta', 1), ('ka', 1), (None, 1), ('tām', 2)] (None: a rest)."""
    out: list[tuple[Optional[str], int]] = []
    for word in text.replace(",", " , ").replace(";", " ; ").replace("-", " ").split():
        if word == ",":
            out.append((None, 1))
        elif word == ";":
            out.append((None, 2))
        elif word in ("•", "·", "."):
            out.append((None, 1))
        else:
            out.append((re.sub(r"[\d₀-₉]+$", "", word), weight(word)))
    return out


def pulses(text: str) -> int:
    return sum(w for _, w in parse(text))


def statement(n: int, rng: random.Random) -> str:
    """A statement of exactly n pulses from the basic phrases (longer: joined)."""
    parts, left = [], n
    while left > 0:
        size = min(left, 9)
        if left > 9 and left - size < 3:                 # don't leave a scrap at the end
            size = left - 3
        parts.append(rng.choice(PHRASES[size]))
        left -= size
    return " ".join(parts)


def gap(n: int) -> str:
    """A gap of n pulses: sounded with tām when it is two or more."""
    if n <= 0:
        return ""
    if n == 1:
        return ","
    return "tām" + " ," * (n - 2)


def mora_shapes(total: int) -> list[tuple[int, int]]:
    """(statement, gap) pulse pairs with 3s + 2g = total, best first: Nelson's rule (a
    short statement takes a sounded gap of two or more), then statements of a middling
    length, then smaller gaps."""
    shapes = [(s, (total - 3 * s) // 2) for s in range(1, total // 3 + 1)
              if (total - 3 * s) >= 0 and (total - 3 * s) % 2 == 0]
    def good(sg):
        s, g = sg
        breaks = s < SHORT_STATEMENT and g < SOUNDED_GAP
        return (breaks, 1.5 * abs(g - 2) + max(0, 4 - s) + max(0, s - 9), g)
    return sorted(shapes, key=good)


def mora(total: int, rng: random.Random, close: bool = True) -> list[tuple[int, Optional[str], bool]]:
    """A mōrā of exactly `total` pulses ending where the next design point (the sam)
    begins: [(pulse, syllable or None, accented)]; with close, a tām on that point
    (pulse == total) seals it. Raises ConfigError if total is too short for any."""
    shapes = mora_shapes(total)
    if not shapes:
        raise ConfigError(f"no mōrā fits {total} pulses")
    s, g = shapes[0] if len(shapes) == 1 or rng.random() < 0.7 else shapes[1]   # mostly the best
    text_s = statement(s, rng)
    parts = [text_s, gap(g), text_s, gap(g), text_s]
    out: list[tuple[int, Optional[str], bool]] = []
    at = 0
    for k, part in enumerate(parts):
        first = True
        for syl, w in parse(part):
            out.append((at, syl, k % 2 == 0 and first and syl is not None))
            if syl is not None:
                first = False
            at += w
    assert at == total, (at, total, s, g)
    if close:
        out.append((total, "tām", True))
    return out


@lru_cache(maxsize=8)
def stroke_table(instrument: str = "mridangam") -> dict[str, str]:
    """Syllable -> stroke, for an instrument (indian/solkattu.toml)."""
    from .config import _read_toml

    data = _read_toml(indian.HERE / "solkattu.toml").get("strokes", {})
    table = data.get(instrument) or data.get("mridangam", {})
    return {k.lower(): v for k, v in table.items()}


def stroke(syllable: str, instrument: str = "mridangam") -> str:
    """The stroke a syllable is played with (itself, if the table doesn't say). Exact
    spelling first (ṭa, retroflex, isn't ta), then without marks or aspiration."""
    table = stroke_table(instrument)
    s = syllable.lower()
    if s in table:
        return table[s]
    bare = {k: v for k, v in table.items() if plain(k) == k}     # entries written plainly
    p = plain(s)
    return bare.get(p) or bare.get(p.replace("h", "")) or syllable
