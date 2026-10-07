"""Rāga and tāla knowledge as plain data: scales and cycles, for colour and grooves.

Not wired into the band yet. Rāgas are a guide, not a cage (fusion bends the rules), so
everything here is pitch-class sets and beat groupings that other parts can use: a key
palette entry like "D kalyani", a percussion pattern in tintal, a count-off in khanda cāpu.

  melakarta(n)      the n-th of the 72 melakartas (1-72): (name, intervals)
  suladi(tala, jati)  one of the 35 sūlādi tālas: its beat groups
  ragas()           the janya / Hindustani rāgas of ragas.toml, plus all 72 melakartas
  talas()           the tālas of talas.toml, plus the sūlādi tālas
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from ..config import ConfigError, _read_toml

HERE = Path(__file__).parent

SWARAS = {"S": 0, "R1": 1, "R2": 2, "R3": 3, "G1": 2, "G2": 3, "G3": 4, "M1": 5, "M2": 6,
          "P": 7, "D1": 8, "D2": 9, "D3": 10, "N1": 9, "N2": 10, "N3": 11, "S'": 12}

# The 72 melakartas, in order (Katapayadi names). Their swaras follow from the number: the
# first 36 have M1, the rest M2; within each half, the chakra (six of six) gives R and G,
# the place within the chakra gives D and N.
MELAKARTAS = (
    "kanakangi", "ratnangi", "ganamurti", "vanaspati", "manavati", "tanarupi",
    "senavati", "hanumatodi", "dhenuka", "natakapriya", "kokilapriya", "rupavati",
    "gayakapriya", "vakulabharanam", "mayamalavagowla", "chakravakam", "suryakantam",
    "hatakambari", "jhankaradhvani", "natabhairavi", "keeravani", "kharaharapriya",
    "gourimanohari", "varunapriya", "mararanjani", "charukesi", "sarasangi", "harikambhoji",
    "dheerasankarabharanam", "naganandini", "yagapriya", "ragavardhini", "gangeyabhushani",
    "vagadheeswari", "shulini", "chalanata", "salagam", "jalarnavam", "jhalavarali",
    "navaneetam", "pavani", "raghupriya", "gavambhodhi", "bhavapriya", "shubhapantuvarali",
    "shadvidamargini", "suvarnangi", "divyamani", "dhavalambari", "namanarayani",
    "kamavardhini", "ramapriya", "gamanashrama", "vishwambari", "shamalangi",
    "shanmukhapriya", "simhendramadhyamam", "hemavati", "dharmavati", "neetimati",
    "kantamani", "rishabhapriya", "latangi", "vachaspati", "mechakalyani", "chitrambari",
    "sucharitra", "jyotiswarupini", "dhatuvardhani", "nasikabhushani", "kosalam",
    "rasikapriya",
)
RG = ((1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4))       # R1G1 R1G2 R1G3 R2G2 R2G3 R3G3
DN = ((8, 9), (8, 10), (8, 11), (9, 10), (9, 11), (10, 11))  # D1N1 D1N2 D1N3 D2N2 D2N3 D3N3

# Common short names for melakartas (and the Hindustani thāts that share their scales).
MELA_ALIASES = {
    "todi": 8, "mayamalavagowla": 15, "kiravani": 21, "kharaharapriya": 22,
    "harikambhoji": 28, "shankarabharanam": 29, "kalyani": 65, "subhapantuvarali": 45,
    "simhendramadhyamam": 57, "shanmukhapriya": 56, "gowrimanohari": 23, "gaurimanohari": 23,
    "sankarabharanam": 29, "dheerasankarabharanam": 29, "hanumatodi": 8, "natabhairavi": 20,
    "charukesi": 26, "keeravani": 21, "hemavati": 58, "latangi": 63, "vachaspati": 64,
    # thāts
    "bilawal": 29, "kalyan": 65, "khamaj": 28, "kafi": 22, "asavari": 20,
    "bhairavi-thaat": 8, "bhairav": 15, "purvi": 51, "marwa-thaat": 53, "todi-thaat": 45,
}

# Sūlādi tālas: the tāla's angas (laghu L, drutam O = 2, anudrutam U = 1); the laghu's
# length is the jāti.
SULADI = {"dhruva": "LOLL", "matya": "LOL", "rupaka": "OL", "jhampa": "LUO",
          "triputa": "LOO", "ata": "LLOO", "eka": "L"}
JATIS = {"tisra": 3, "chatusra": 4, "khanda": 5, "misra": 7, "sankeerna": 9}


@dataclass(frozen=True)
class Raga:
    name: str
    tradition: str
    arohana: tuple[int, ...]       # semitones above Sa, ascending
    avarohana: tuple[int, ...]     # descending
    note: str = ""

    @property
    def pitch_classes(self) -> frozenset[int]:
        return frozenset(i % 12 for i in self.arohana + self.avarohana)


@dataclass(frozen=True)
class Tala:
    name: str
    tradition: str
    groups: tuple[int, ...]        # beats per group (vibhāg / anga)
    khali: tuple[int, ...] = ()    # Hindustani: open beats (1-based)
    theka: str = ""
    note: str = ""

    @property
    def beats(self) -> int:
        return sum(self.groups)

    @property
    def group_starts(self) -> tuple[int, ...]:
        """0-based beats where each group starts (beat 0 is the sam)."""
        out, b = [], 0
        for g in self.groups:
            out.append(b)
            b += g
        return tuple(out)


def parse_swaras(text: str) -> tuple[int, ...]:
    try:
        return tuple(SWARAS[s] for s in text.split())
    except KeyError as e:
        raise ConfigError(f"'{e.args[0]}' is not a swara (S R1 R2 R3 G1 G2 G3 M1 M2 P D1 D2 D3 "
                          f"N1 N2 N3 S')") from None


def melakarta(n: int) -> Raga:
    if not 1 <= n <= 72:
        raise ConfigError(f"melakarta {n}: there are 72 (1-72)")
    k = n - 1
    m = 5 if k < 36 else 6
    r, g = RG[(k % 36) // 6]
    d, ni = DN[k % 6]
    up = (0, r, g, m, 7, d, ni, 12)
    return Raga(MELAKARTAS[k], "carnatic", up, tuple(reversed(up)), f"melakarta {n}")


def suladi(tala: str, jati: str) -> Tala:
    if tala not in SULADI or jati not in JATIS:
        raise ConfigError(f"sūlādi tāla '{jati} {tala}': tāla one of {', '.join(SULADI)}, jāti "
                          f"one of {', '.join(JATIS)}")
    sizes = {"L": JATIS[jati], "O": 2, "U": 1}
    return Tala(f"{jati}-{tala}", "carnatic", tuple(sizes[a] for a in SULADI[tala]))


@lru_cache(maxsize=1)
def ragas() -> dict[str, Raga]:
    out = {name: melakarta(i + 1) for i, name in enumerate(MELAKARTAS)}
    out.update({alias: melakarta(n) for alias, n in MELA_ALIASES.items()})
    for name, d in _read_toml(HERE / "ragas.toml")["ragas"].items():
        r = Raga(name, d["tradition"], parse_swaras(d["arohana"]), parse_swaras(d["avarohana"]),
                 d.get("note", ""))
        out[name] = r
        for alias in d.get("aliases", ()):
            out[alias] = r
    return out


@lru_cache(maxsize=1)
def talas() -> dict[str, Tala]:
    out = {f"{j}-{t}": suladi(t, j) for t in SULADI for j in JATIS}
    for name, d in _read_toml(HERE / "talas.toml")["talas"].items():
        t = Tala(name, d["tradition"], tuple(d["groups"]), tuple(d.get("khali", ())),
                 d.get("theka", ""), d.get("note", ""))
        out[name] = t
        for alias in d.get("aliases", ()):
            out[alias] = t
    return out


# Spellings vary (Gowri, Gauri, Gouri; Sankarabharanam, Shankarabharanam; Mohanam,
# Mohanamm): names are also matched on a plain form of their sound.
_SOUND = (("aa", "a"), ("ee", "i"), ("oo", "u"), ("ow", "ou"), ("au", "ou"), ("w", "v"),
          ("sh", "s"), ("th", "t"), ("dh", "d"), ("bh", "b"), ("kh", "k"), ("gh", "g"),
          ("ch", "c"), ("jh", "j"), ("ph", "p"), ("zh", "l"))


def plain_name(name: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFD", name.lower())
    s = "".join(c for c in s if c.isalpha() and not unicodedata.combining(c))
    for a, b in _SOUND:
        s = s.replace(a, b)
    out = []
    for c in s:                                  # doubled letters count once
        if not out or out[-1] != c:
            out.append(c)
    return "".join(out)


@lru_cache(maxsize=1)
def _by_sound() -> dict[str, Raga]:
    out: dict[str, Raga] = {}
    for n, r in ragas().items():
        out.setdefault(plain_name(n), r)
    return out


def raga(name: str) -> Raga:
    r = find(name)
    if r is None:
        raise ConfigError(f"unknown rāga '{name}' (see accompanist/indian/ragas.toml, or a "
                          f"melakarta name)")
    return r


def tala(name: str) -> Tala:
    t = talas().get(name.lower().strip())
    if t is None:
        raise ConfigError(f"unknown tāla '{name}' (see accompanist/indian/talas.toml, or a "
                          f"sūlādi tāla like 'khanda-jhampa')")
    return t


def find(name: str) -> Optional[Raga]:
    """A rāga by name, alias, or any common spelling of it."""
    key = name.lower().strip()
    return ragas().get(key) or ragas().get(key.replace(" ", "-")) or _by_sound().get(plain_name(name))
