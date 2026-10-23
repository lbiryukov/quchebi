"""Transliteration helpers for Georgian / Russian / Latin names.

Three functions:

* :func:`skeleton` -- the search skeleton shared with the client
  (``app/src/search/normalize.js``); shared vectors in ``tests/vectors/translit.json``.
  Rule: lowercase (Mtavruli -> Mkhedruli via ``str.lower``), ``ё -> е``, ``дж -> j``,
  per-character Georgian/Cyrillic -> Latin, keep only ``[a-z0-9]``, then ``gh -> g``,
  ``q -> k``, ``w -> v``, ``y -> i`` (applied in that order).
* :func:`ka_to_ru` -- per-character Georgian -> Russian (word-initial ``ე`` -> ``э``), used to
  generate a Russian name when OSM has none (``feature.ru_generated``).
* :func:`ka_to_lat` -- Georgian national romanization (2002) without apostrophes, for display.

Characters that are not Georgian are passed through unchanged by ``ka_to_ru``/``ka_to_lat``.
"""
from __future__ import annotations

import re

# Mkhedruli letters U+10D0..U+10F0 (33 modern + a few archaic ones handled below).
_KA_LETTERS = "აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ"

_KA_LAT = [
    "a", "b", "g", "d", "e", "v", "z", "t", "i", "k", "l", "m", "n", "o", "p", "zh", "r", "s", "t",
    "u", "p", "k", "gh", "q", "sh", "ch", "ts", "dz", "ts", "ch", "kh", "j", "h",
]
_KA_RU = [
    "а", "б", "г", "д", "е", "в", "з", "т", "и", "к", "л", "м", "н", "о", "п", "ж", "р", "с", "т",
    "у", "п", "к", "г", "к", "ш", "ч", "ц", "дз", "ц", "ч", "х", "дж", "х",
]
# Archaic Mkhedruli letters (rare, but appear in old texts and some OSM tags).
_KA_ARCHAIC_LAT = {"ჱ": "e", "ჲ": "i", "ჳ": "v", "ჴ": "q", "ჵ": "o", "ჶ": "p", "ჷ": "", "ჸ": "", "ჹ": "", "ჺ": ""}
_KA_ARCHAIC_RU = {"ჱ": "е", "ჲ": "й", "ჳ": "в", "ჴ": "х", "ჵ": "о", "ჶ": "ф", "ჷ": "", "ჸ": "", "ჹ": "", "ჺ": ""}

KA_TO_LAT: dict[str, str] = dict(zip(_KA_LETTERS, _KA_LAT)) | _KA_ARCHAIC_LAT
KA_TO_RU: dict[str, str] = dict(zip(_KA_LETTERS, _KA_RU)) | _KA_ARCHAIC_RU

# Russian -> Latin for the skeleton (simple scholarly-ish scheme, y/w folded later).
RU_TO_LAT: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _nuskhuri_to_mkhedruli(ch: str) -> str:
    """``str.lower`` maps Asomtavruli (U+10A0..) to Nuskhuri (U+2D00..); fold that to Mkhedruli."""
    o = ord(ch)
    if 0x2D00 <= o <= 0x2D25:
        return chr(o - 0x2D00 + 0x10D0)
    return ch


# Per-character table for the skeleton (applied after lowercasing). Deliberately limited to the
# 33 modern Georgian letters and the 33 Russian letters so that the JS mirror stays exact;
# anything else is dropped by the [a-z0-9] filter.
SKELETON_TABLE: dict[str, str] = dict(zip(_KA_LETTERS, _KA_LAT)) | RU_TO_LAT
_SKEL_TRANS = str.maketrans(SKELETON_TABLE)
_NON_SKEL = re.compile(r"[^a-z0-9]+")


def skeleton(s: str) -> str:
    """Search skeleton: Latin ``[a-z0-9]`` only, spelling-tolerant (see module docstring)."""
    if not s:
        return ""
    t = s.lower().replace("ё", "е").replace("дж", "j")
    t = t.translate(_SKEL_TRANS)
    t = _NON_SKEL.sub("", t)
    return t.replace("gh", "g").replace("q", "k").replace("w", "v").replace("y", "i").replace("f", "p")


_LETTER = re.compile(r"[^\W\d_]")


def _ka_lower(s: str) -> str:
    """Lowercase only Georgian capitals (Mtavruli, Asomtavruli); other scripts keep their case."""
    out = []
    for ch in s:
        o = ord(ch)
        if 0x1C90 <= o <= 0x1CBF or 0x10A0 <= o <= 0x10CF:
            ch = _nuskhuri_to_mkhedruli(ch.lower())
        out.append(ch)
    return "".join(out)


def ka_to_ru(s: str) -> str:
    """Per-character Georgian -> Russian. Word-initial ``ე`` becomes ``э``."""
    if not s:
        return ""
    s = _ka_lower(s)
    out = []
    prev_letter = False
    for ch in s:
        if ch == "ე" and not prev_letter:
            out.append("э")
        else:
            out.append(KA_TO_RU.get(ch, ch))
        prev_letter = bool(_LETTER.match(ch))
    return "".join(out)


def ka_to_lat(s: str, capitalize: bool = False) -> str:
    """Georgian national romanization (2002) without apostrophes: ``ჭავჭავაძე`` -> ``chavchavadze``.

    With ``capitalize=True`` every word that starts with a Georgian letter gets an initial
    capital (``ილია ჭავჭავაძის`` -> ``Ilia Chavchavadzis``).
    """
    if not s:
        return ""
    s = _ka_lower(s)
    if not capitalize:
        return "".join(KA_TO_LAT.get(ch, ch) for ch in s)
    out = []
    prev_letter = False
    for ch in s:
        lat = KA_TO_LAT.get(ch)
        if lat is None:
            out.append(ch)
        elif not prev_letter and lat:
            out.append(lat[0].upper() + lat[1:])
        else:
            out.append(lat)
        prev_letter = bool(_LETTER.match(ch))
    return "".join(out)


__all__ = ["skeleton", "ka_to_ru", "ka_to_lat", "KA_TO_LAT", "KA_TO_RU", "RU_TO_LAT", "SKELETON_TABLE"]
