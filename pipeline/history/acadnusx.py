"""AcadNusx (and related legacy Georgian fonts) -> Unicode Mkhedruli.

Legacy Georgian fonts (AcadNusx, AcadMtavr, LitNusx, LiterMtavr, Kolhety, LadoGrigolia ...)
draw Georgian glyphs at Latin code points. The text layer of the 2008 encyclopedia PDF is
therefore Latin; only runs set in such fonts must be converted, digits/Roman numerals and runs
in ordinary fonts (Times, Berlin Sans) stay as they are.
"""
from __future__ import annotations

import re

LATIN = "abgdevzTiklmnopJrstufqRySCcZwWxjh"
GEORGIAN = "აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ"
assert len(LATIN) == len(GEORGIAN) == 33

# Punctuation remapped by the legacy fonts.
PUNCT = {"`": "„", "~": "“", "_": "–"}

TABLE = str.maketrans({**dict(zip(LATIN, GEORGIAN)), **PUNCT})
REVERSE = str.maketrans({g: l for l, g in zip(LATIN, GEORGIAN)})

# Font families that use the AcadNusx code layout (subset prefixes like "ZMYIBV+" are stripped).
_GEORGIAN_FONT_RX = re.compile(
    r"acad|lit(er)?(nusx|mtavr|aturuli)|kolhety|grigolia|lado|nusx|mtavr|geo-", re.I
)
_PLAIN_FONT_RX = re.compile(r"times|arial|berlin|helvetica|courier|symbol|calibri|verdana", re.I)


def base_font(fontname: str) -> str:
    """Strip the 6-letter subset prefix: 'ZMYIBV+LitNusx' -> 'LitNusx'."""
    return fontname.split("+", 1)[-1]


def is_georgian_font(fontname: str) -> bool:
    name = base_font(fontname)
    if _PLAIN_FONT_RX.search(name):
        return False
    return bool(_GEORGIAN_FONT_RX.search(name))


def is_bold_font(fontname: str) -> bool:
    return "bold" in base_font(fontname).lower()


def is_mtavruli_font(fontname: str) -> bool:
    """Capital-style (Mtavruli) display fonts used for headwords."""
    name = base_font(fontname).lower()
    return "mtavr" in name or "kolhetymt" in name


def convert(text: str) -> str:
    """Convert AcadNusx-encoded text to Unicode Georgian (Roman numerals, digits kept)."""
    return text.translate(TABLE)


def to_acad(text: str) -> str:
    """Inverse mapping (for tests and for building synthetic fixtures)."""
    return text.translate(REVERSE)
