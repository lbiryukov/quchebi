"""Thin adapter over :mod:`pipeline.names` (written by the names stage) with small fallbacks.

The history stages need: ``parse_name`` (kind, ordinal, base, family key), ``skeleton`` (search
key), ``ka_to_lat`` and ``normalize_ru_name``. If the names package is unavailable or its API
changes, minimal local versions keep the history stages (and their tests) working.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pipeline.log import get_logger

log = get_logger(__name__)

try:  # pragma: no cover - exercised implicitly
    from pipeline.names.parse import normalize_ru_name as _normalize_ru_name
    from pipeline.names.parse import parse_name as _parse_name
    from pipeline.names.translit import ka_to_lat as _ka_to_lat
    from pipeline.names.translit import skeleton as _skeleton
    HAVE_NAMES = True
except Exception as exc:  # noqa: BLE001
    log.warning("pipeline.names not importable (%s); using local fallbacks", exc)
    HAVE_NAMES = False

_KA = "აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ"
_LAT = ["a", "b", "g", "d", "e", "v", "z", "t", "i", "k", "l", "m", "n", "o", "p", "zh", "r", "s", "t", "u",
        "p", "k", "gh", "q", "sh", "ch", "ts", "dz", "ts", "ch", "kh", "j", "h"]
_KA_LAT = dict(zip(_KA, _LAT))


@dataclass
class _Parsed:
    raw: str
    kind: str
    type_word: str | None
    ord: int | None
    regnal: str | None
    base_ka: str
    family_key: str
    flags: list[str] = field(default_factory=list)


def skeleton(s: str) -> str:
    if HAVE_NAMES:
        return _skeleton(s)
    t = (s or "").lower()
    t = "".join(_KA_LAT.get(ch, ch) for ch in t)
    t = re.sub(r"[^a-z0-9]+", "", t)
    return t.replace("gh", "g").replace("q", "k").replace("w", "v").replace("y", "i")


def ka_to_lat(s: str) -> str:
    if HAVE_NAMES:
        return _ka_to_lat(s, capitalize=True)
    return "".join(_KA_LAT.get(ch, ch) for ch in s or "")


def ka_to_ru(s: str) -> str:
    """Per-character Georgian -> Cyrillic (search/display fallback when no Russian form is known)."""
    try:
        from pipeline.names.translit import ka_to_ru as _k2r
        return _k2r(s)
    except Exception:  # noqa: BLE001
        return s


def normalize_ru_name(s: str) -> str:
    if HAVE_NAMES:
        return _normalize_ru_name(s)
    return re.sub(r"\s+", " ", (s or "").replace("ё", "е")).strip()


def parse_name(ka: str, ru: str | None = None, en: str | None = None):
    if HAVE_NAMES:
        return _parse_name(ka, ru, en)
    from pipeline.history import georgian as G
    words = (ka or "").split()
    tw = G.kind_of(words[-1]) if words else None
    kind = G.KIND_CODE.get(tw, "other") if tw else "other"
    rest = words[:-1] if tw else words
    ord_ = None
    if rest and re.fullmatch(r"[IVX]+", rest[-1]):
        ord_ = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}.get(rest[-1])
        rest = rest[:-1]
    base = " ".join(rest)
    key = " ".join(sorted(re.sub(r"(ის|ს)$", "", w) for w in re.sub(r"\([^)]*\)", " ", base).split()))
    return _Parsed(ka, kind, tw, ord_, None, base, key)


def name_key(ka: str) -> tuple[str, str, int | None]:
    """(family_key, kind, ord) of a Georgian name — the main linking key."""
    p = parse_name(ka)
    return (p.family_key or "", p.kind or "other", p.ord)


def loose_key(ka: str) -> tuple[str, str, int | None]:
    """(surname-ish token, kind, ord): last eponym token only (initials/given names dropped)."""
    p = parse_name(ka)
    base = re.sub(r"\([^)]*\)", " ", p.base_ka or "")
    toks = [t for t in base.replace("„", " ").replace("“", " ").split() if not re.fullmatch(r"[ა-ჰ]\.", t)]
    last = toks[-1] if toks else ""
    last = re.sub(r"(ისა|ის|ს|სა)$", "", last)
    return (skeleton(last), p.kind or "other", p.ord)
