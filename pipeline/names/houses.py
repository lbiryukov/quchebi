"""House-number parsing (``addr:housenumber`` -> structured parts).

``parse_house(raw)`` returns a list of :class:`HousePart` (several for lists such as ``27;29``),
or ``[]`` when the value is not a usable house number (``office 208``, ``15ბროწეულის ქუჩა``,
kilometre markers, bare building letters). Junk values are logged at DEBUG level, never returned.
Shared vectors: ``tests/vectors/house_numbers.json``.

Rules (see the vectors' ``notes``):

* single letters are normalized to Georgian by sound, from Latin and Cyrillic
  (``3a``/``3а``/``3ა`` -> ``n=3, lit="ა"``), see :func:`normalize_letter`;
* ``A/B`` is a fraction (``sub="B"``); ``A-B`` with ``B > A`` is a range (``n_to=B``),
  with ``B <= A`` it is a sub-number (``221-4`` -> ``sub="4"``);
  ``A-A<letter>`` (``10-10ა``) is the list ``[10, 10ა]``; ``A-B-C`` increasing is a list;
* ``;`` or ``,`` separate a list: one part per number, every part gets ``kind="list"``;
* ``N კორპ. M`` / ``N ბლოკი M`` / ``korp.`` / ``корп.`` / ``block`` give ``corp="M"``.

``kind`` is one of ``int | letter | fraction | corp | range | list`` (``junk`` is never returned).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from pipeline.log import get_logger

log = get_logger(__name__)


@dataclass
class HousePart:
    n: int
    lit: str = ""
    sub: str = ""
    n_to: int | None = None
    corp: str = ""
    kind: str = "int"

    def as_dict(self) -> dict:
        return asdict(self)


# Latin and Cyrillic letters -> Georgian by sound (the list from the vectors' notes).
_LETTER_MAP: dict[str, str] = {}
for _ka, _lat, _cyr in (
    ("ა", "a", "а"), ("ბ", "b", "б"), ("გ", "g", "г"), ("დ", "d", "д"), ("ე", "e", "е"),
    ("ვ", "v", "в"), ("ზ", "z", "з"), ("ი", "i", "и"), ("კ", "k", "к"), ("ლ", "l", "л"),
    ("მ", "m", "м"), ("ნ", "n", "н"), ("ო", "o", "о"), ("პ", "p", "п"), ("რ", "r", "р"),
    ("ს", "s", "с"), ("ტ", "t", "т"), ("უ", "u", "у"),
):
    _LETTER_MAP[_lat] = _ka
    _LETTER_MAP[_cyr] = _ka
_LETTER_MAP["ё"] = "ე"


def normalize_letter(ch: str) -> str:
    """Normalize one house-number letter to Georgian (``a``/``А`` -> ``ა``).

    Georgian letters (Mtavruli folded to Mkhedruli) are kept; Latin/Cyrillic letters without a
    mapping are returned lower-cased unchanged.
    """
    if not ch:
        return ""
    c = ch.strip().lower()
    return _LETTER_MAP.get(c, c)


def _norm_sub(s: str) -> str:
    """Normalize letters inside a sub-number/fraction part (``14A`` -> ``14ა``)."""
    s = s.replace(" ", "")
    return "".join(normalize_letter(c) if c.isalpha() else c for c in s)


# One "letter" of a house number: Georgian (incl. Mtavruli), Latin or Cyrillic.
_L = r"[ა-ჰᲐ-Ჰa-zA-Zа-яА-ЯёЁ]"
_CORP = (
    r"(?:კორპ(?:უსი)?\.?|ქორპ(?:უსი)?\.?|ბლოკი|შენობა|სახლი|korp(?:us)?\.?|корп(?:ус)?\.?|block|bl\.?)"
)

_RE_INT = re.compile(r"(\d+)")
_RE_LETTER = re.compile(rf"(\d+) ?({_L})")
_RE_LETTER_DASH = re.compile(rf"(\d+)-({_L})")
_RE_FRACTION = re.compile(rf"(\d+) ?({_L})? ?/ ?((?:\d+ ?{_L}?|{_L})(?: ?[/-] ?(?:\d+{_L}?|{_L}))*)")
_RE_DASH = re.compile(rf"(\d+)({_L})?((?:-\d+{_L}?)+)(?:/(\d+{_L}?))?")
_RE_CORP = re.compile(
    rf"(\d+) ?({_L})?(?: ?/ ?(\d+{_L}?))? ?(?:\\ ?)?{_CORP} ?([0-9IVXivx]+{_L}?(?:-\d+)?|{_L})",
    re.IGNORECASE,
)
_RE_PAREN = re.compile(r"\s*\([^)]*\)\s*")
_RE_NUM_PREFIX = re.compile(r"^(?:№|#|No\.?|N\.?) ?(?=\d)", re.IGNORECASE)
_RE_LIST_SPLIT = re.compile(r"\s*[;,]\s*")
_RE_DASH_ITEM = re.compile(rf"(\d+)({_L})?")


def _clean(raw: str) -> str:
    s = raw.replace(chr(0xA0), " ").replace("–", "-").replace("—", "-").strip()
    s = _RE_PAREN.sub(" ", s).strip()
    # backslash: between digits it is a fraction separator ("1\2\3"), otherwise noise ("265 \ korp. 6")
    s = re.sub(r"(?<=\d)\\(?=\d)", "/", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _RE_NUM_PREFIX.sub("", s)
    s = re.sub(r"\s*-\s*", "-", s)
    return s


def _parse_dash(m: re.Match) -> list[HousePart] | None:
    """``A-B``, ``A-B-C``, optional trailing ``/sub``."""
    first_n, first_lit = int(m.group(1)), normalize_letter(m.group(2) or "")
    rest = [(int(x.group(1)), normalize_letter(x.group(2) or "")) for x in _RE_DASH_ITEM.finditer(m.group(3))]
    tail_sub = _norm_sub(m.group(4)) if m.group(4) else ""
    items = [(first_n, first_lit)] + rest
    if len(items) == 2:
        (a, la), (b, lb) = items
        if b > a:
            return [HousePart(a, la, tail_sub, b, "", "range")]
        if b == a and lb and not la and not tail_sub:
            return [HousePart(a, "", "", None, "", "list"), HousePart(a, lb, "", None, "", "list")]
        sub = str(b) + lb + ("/" + tail_sub if tail_sub else "")
        return [HousePart(a, la, sub, None, "", "fraction")]
    # chain of three or more: increasing -> list, otherwise a compound sub-number
    if not tail_sub and all(items[i] < items[i + 1] for i in range(len(items) - 1)):
        return [HousePart(n, lit, "", None, "", "list") for n, lit in items]
    sub = m.group(3)[1:] + ("/" + tail_sub if tail_sub else "")
    return [HousePart(first_n, first_lit, _norm_sub(sub), None, "", "fraction")]


def _parse_one(s: str) -> list[HousePart] | None:
    if m := _RE_INT.fullmatch(s):
        return [HousePart(int(m.group(1)), "", "", None, "", "int")]
    if m := _RE_LETTER.fullmatch(s):
        return [HousePart(int(m.group(1)), normalize_letter(m.group(2)), "", None, "", "letter")]
    if m := _RE_LETTER_DASH.fullmatch(s):
        return [HousePart(int(m.group(1)), normalize_letter(m.group(2)), "", None, "", "letter")]
    if m := _RE_FRACTION.fullmatch(s):
        return [HousePart(int(m.group(1)), normalize_letter(m.group(2) or ""), _norm_sub(m.group(3)), None, "", "fraction")]
    if m := _RE_DASH.fullmatch(s):
        return _parse_dash(m)
    if m := _RE_CORP.fullmatch(s):
        corp = m.group(4).strip().rstrip(".")
        return [
            HousePart(
                int(m.group(1)), normalize_letter(m.group(2) or ""),
                _norm_sub(m.group(3)) if m.group(3) else "", None, corp, "corp",
            )
        ]
    return None


def parse_house(raw: str) -> list[HousePart]:
    """Parse an ``addr:housenumber`` value; ``[]`` if it is not a usable house number."""
    if not raw:
        return []
    s = _clean(str(raw))
    if not s:
        return []
    items = [x for x in _RE_LIST_SPLIT.split(s) if x]
    if len(items) > 1:
        parts: list[HousePart] = []
        for it in items:
            p = _parse_one(it)
            if p is None:
                log.debug("house number junk (list item %r): %r", it, raw)
                return []
            parts.extend(p)
        for p in parts:
            p.kind = "list"
        return parts
    p = _parse_one(s)
    if p is None:
        log.debug("house number junk: %r", raw)
        return []
    return p


__all__ = ["HousePart", "parse_house", "normalize_letter"]
