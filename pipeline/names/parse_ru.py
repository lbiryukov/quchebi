"""Parsing of Russian street / toponym names (cities whose primary OSM name is Russian).

Name shapes (Moscow):

* ``улица Льва Толстого``, ``проезд Девичьего Поля`` -- type word first, eponym in the genitive;
* ``Комсомольский проспект``, ``Большая Пироговская улица`` -- adjective + type word;
* ``1-й Неопалимовский переулок``, ``2-я Фрунзенская улица`` -- ordinal + adjective + type word;
* ``1-й переулок Тружеников`` -- ordinal + type word + genitive;
* ``улица Остоженка``, ``Крымский Вал`` -- old names with or without a type word;
* ``Зубовский бульвар (дублёр)`` -- service roadway of the same street (same family).

:func:`parse_name_ru` returns the same :class:`~pipeline.names.parse.ParsedName` as the Georgian
parser; ``base_ka`` holds the name without the type word and the ordinal (``Льва Толстого``,
``Большая Пироговская``). Russian streets are never "derived" (a lane has no parent street).

The family key joins the names that share one origin: size / age words (Большой, Малый, Новый ...)
are dropped and adjectives are reduced to a gender-neutral stem, so ``Большой Саввинский переулок``,
``Малый Саввинский переулок`` and ``Саввинская набережная`` are one family (``саввинск``), as are
``1-я ... 3-я Фрунзенская улица`` and ``Фрунзенская набережная``.
"""
from __future__ import annotations

import re

#: Russian type word (nominative, lower case) -> kind code.
RU_TYPE_KIND: dict[str, str] = {
    "улица": "street", "линия": "street", "просек": "street", "просека": "street",
    "проспект": "avenue",
    "шоссе": "highway",
    "дорога": "road", "кольцо": "road",
    "набережная": "embankment",
    "бульвар": "boulevard",
    "аллея": "alley", "тропа": "alley", "тропинка": "alley", "дорожка": "alley", "путь": "alley",
    "переулок": "lane",
    "тупик": "dead_end",
    "проезд": "passage",
    "площадь": "square", "площадка": "square",
    "мост": "bridge", "метромост": "bridge", "эстакада": "bridge", "путепровод": "bridge",
    "тоннель": "tunnel", "туннель": "tunnel",
    "сквер": "garden", "сад": "garden",
    "парк": "park",
    "река": "river", "ручей": "stream", "канал": "canal",
    "пруд": "lake", "пруды": "lake", "озеро": "lake",
    "район": "district",
}
#: Abbreviations seen in ``addr:street`` values -> full type word.
RU_TYPE_ABBR: dict[str, str] = {
    "ул.": "улица", "ул": "улица", "пер.": "переулок", "пер": "переулок", "пр-т": "проспект",
    "просп.": "проспект", "пр.": "проезд", "пр-д": "проезд", "наб.": "набережная", "наб": "набережная",
    "б-р": "бульвар", "бул.": "бульвар", "пл.": "площадь", "пл": "площадь", "ш.": "шоссе",
    "туп.": "тупик", "ал.": "аллея",
}
#: Size / age / position words: part of the name, but not of the family key.
RU_MODIFIERS = frozenset({
    "большой", "большая", "большое", "малый", "малая", "малое", "средний", "средняя", "среднее",
    "верхний", "верхняя", "верхнее", "нижний", "нижняя", "нижнее", "новый", "новая", "новое",
    "старый", "старая", "старое",
})
_ORD = re.compile(r"^(\d+)-?(?:й|я|е|ый|ий|ой|ая|ое|ья|ье)$", re.IGNORECASE)
_PAREN = re.compile(r"\s*\(([^)]*)\)\s*")
_WS = re.compile(r"\s+")
# adjective endings -> gender-neutral stem ("саввинский" / "саввинская" -> "саввинск")
_ADJ = (
    (re.compile(r"(?:ский|ская|ское|ской|ские|ских)$"), "ск"),
    (re.compile(r"(?:цкий|цкая|цкое|цкой|цкие|цких)$"), "цк"),
    (re.compile(r"(?:ный|ная|ное|ной|ные|ных)$"), "н"),
    (re.compile(r"(?:ний|няя|нее|ние|них)$"), "н"),
    (re.compile(r"(?:ий|ый|ой|ая|яя|ое|ее|ые|ие|ья|ье)$"), ""),
)
_EN_TYPES = re.compile(
    r"(?i)\b(street|st\.?|lane|avenue|ave\.?|embankment|boulevard|blvd\.?|square|passage|drive|highway|"
    r"road|alley|bridge|tunnel|dead[ -]end|side[ -]street|pereulok|ulitsa|prospekt|naberezhnaya|proezd|"
    r"ploshchad)\b")
_EN_ORD = re.compile(r"(?i)(?<![\w-])\d+(?:st|nd|rd|th)(?![\w-])")


def normalize_ru(s: str) -> str:
    """Trim, collapse spaces, unify dashes and quotes; keeps the case and ``ё``."""
    s = (s or "").replace(chr(0xA0), " ").replace("–", "-").replace("—", "-")
    s = s.replace("«", '"').replace("»", '"')
    return _WS.sub(" ", s).strip()


def key_word(w: str, adjective: bool) -> str:
    """Family-key form of one word: lower case, ``ё`` -> ``е``, adjective endings reduced."""
    k = w.lower().replace("ё", "е").strip('".,')
    if adjective and len(k) > 4:
        for rx, rep in _ADJ:
            m = rx.search(k)
            if m and m.start() >= 2:
                return k[:m.start()] + rep
    return k


def strip_en(en: str | None) -> str | None:
    """English name without the type word and the ordinal (``Lva Tolstogo Street`` -> ``Lva Tolstogo``)."""
    if not en:
        return None
    s = _EN_ORD.sub(" ", _EN_TYPES.sub(" ", en))
    s = _WS.sub(" ", s).strip(" ,")
    return s or None


def split_name(name: str) -> tuple[str | None, int | None, list[str], list[str]]:
    """(type word, ordinal, base tokens, flags) of a normalized Russian name."""
    flags: list[str] = []
    norm = normalize_ru(name)
    m = _PAREN.search(norm)
    if m:
        inner = m.group(1).strip().lower().replace("ё", "е")
        flags.append("service_road" if inner.startswith("дубл") else "paren")
        norm = _WS.sub(" ", _PAREN.sub(" ", norm)).strip()
    toks = norm.split(" ") if norm else []
    low = [RU_TYPE_ABBR.get(t.lower(), t.lower()) for t in toks]
    ti = None
    if low and low[0] in RU_TYPE_KIND:
        ti = 0
    elif low and low[-1] in RU_TYPE_KIND:
        ti = len(low) - 1
    else:
        for i, t in enumerate(low):
            if t in RU_TYPE_KIND:
                ti = i
                break
    type_word = low[ti] if ti is not None else None
    ordinal = None
    base: list[str] = []
    for i, t in enumerate(toks):
        if i == ti:
            continue
        mo = _ORD.match(t)
        if mo and ordinal is None and i <= (ti if ti is not None else len(toks)) + 1:
            ordinal = int(mo.group(1))
            continue
        base.append(t)
    if ti is not None and ti != 0 and ti != len(toks) - 1:
        flags.append("type_inside")
    if ti is None:
        flags.append("no_type")
    return type_word, ordinal, base, flags


def parse_name_ru(name: str, en: str | None = None):
    """Parse a Russian primary name into a :class:`~pipeline.names.parse.ParsedName`."""
    from pipeline.names.parse import ParsedName  # late import: parse.py dispatches here

    raw = name or ""
    type_word, ordinal, base, flags = split_name(raw)
    kind = RU_TYPE_KIND.get(type_word or "", "other")
    base_s = " ".join(base)
    # agreement form ("Комсомольский проспект"): the type word is not the first word
    norm = normalize_ru(_PAREN.sub(" ", raw))
    first_is_type = bool(type_word) and RU_TYPE_ABBR.get(norm.split(" ")[0].lower(), norm.split(" ")[0].lower()) == type_word
    key_toks = [t for t in base if not (t.lower().replace("ё", "е") in RU_MODIFIERS and len(base) > 1)]
    # adjectives are reduced only in the agreement form; a genitive ("Льва Толстого") stays as written
    key = " ".join(key_word(t, adjective=not first_is_type) for t in key_toks if key_word(t, False))
    numbered = bool(re.search(r"№|\d{3,}", base_s)) or base_s.lower().startswith("проектируем")
    if not key or numbered:
        family_key = "#" + normalize_ru(raw).lower().replace("ё", "е")
        if numbered:
            flags.append("numbered")
    else:
        family_key = key
    return ParsedName(
        raw=raw, kind=kind, type_word=type_word, ord=ordinal, regnal=None, base_ka=base_s,
        eponym_nom_ka=None, family_key=family_key, is_derived=False, parent_family_key=None,
        parent_ord=None, parent_kind=None, flags=flags, base_ru=base_s or None, base_en=strip_en(en),
    )


__all__ = ["RU_TYPE_KIND", "RU_MODIFIERS", "normalize_ru", "key_word", "split_name", "strip_en", "parse_name_ru"]
