"""Parsing of Georgian street / toponym names into type, ordinal, eponym and family key.

Name shape (Georgian): ``<eponym in genitive> [<ordinal>] <type word>``, e.g.

* ``ლადო ასათიანის II შესახვევი`` -- 2nd lane of Lado Asatiani (street);
* ``ერეკლე II შესახვევი`` -- lane of Erekle II (regnal number, not an ordinal);
* ``ზიგზაგის I ქუჩის ჩიხი`` -- dead end of the 1st Zigzag street (nested parent);
* ``ნავთლუღის I დასახლების VII შესახვევი`` -- 7th lane of the Navtlugi-1 settlement;
* ``ჰეიდარ ალიევის სახელობის მარჯვენა სანაპირო`` -- "named after" construction.

Public API (used by the OSM, export and enrichment stages):

* :func:`parse_name` -> :class:`ParsedName`;
* :func:`normalize_ka`, :func:`normalize_ru_name`, :func:`strip_type`;
* :data:`KIND_TABLE` (canonical kinds, order = index in ``core.kinds``), :func:`kinds_for_pack`,
  :data:`KIND_CODES`, :data:`KIND_INDEX`, :data:`KIND_GROUP`.

The family key is order-insensitive and spelling-tolerant (genitive endings, final vowels,
syncope, hyphens, titles); keys that can only be reconciled across names (initials such as
``ზ. გამსახურდიას``) are resolved by :func:`pipeline.names.families.group_families`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import NamedTuple

from pipeline.log import get_logger
from pipeline.names.translit import ka_to_lat, ka_to_ru, skeleton

log = get_logger(__name__)


# --------------------------------------------------------------------------------------- kinds
class Kind(NamedTuple):
    code: str
    ka: tuple[str, ...]   # Georgian type words (last word of a name); () if set from tags
    ru: str               # Russian DSL value
    syn: tuple[str, ...]  # Russian synonyms accepted by the DSL
    group: str            # "street*" | "lane*" | "water*" | "area*" | "" (no group)


#: Canonical kinds, exactly as in docs/pack-format.md (index = ``core.f[].k``).
KIND_TABLE: list[Kind] = [
    Kind("street", ("ქუჩა",), "улица", (), "street*"),
    Kind("avenue", ("გამზირი",), "проспект", (), "street*"),
    Kind("highway", ("გზატკეცილი",), "шоссе", (), "street*"),
    Kind("road", ("გზა",), "дорога", (), "street*"),
    Kind("embankment", ("სანაპირო",), "набережная", (), "street*"),
    Kind("ascent", ("აღმართი",), "подъём", (), "street*"),
    Kind("descent", ("დაღმართი",), "спуск", (), "street*"),
    Kind("alley", ("ხეივანი",), "аллея", (), "street*"),
    Kind("row", ("რიგი",), "ряд", (), "street*"),
    Kind("lane", ("შესახვევი",), "переулок", (), "lane*"),
    Kind("dead_end", ("ჩიხი",), "тупик", (), "lane*"),
    Kind("passage", ("გასასვლელი",), "проезд", ("съезд",), "lane*"),
    Kind("square", ("მოედანი",), "площадь", (), ""),
    Kind("bridge", ("ხიდი",), "мост", (), ""),
    Kind("tunnel", ("გვირაბი",), "тоннель", (), ""),
    Kind("ravine", ("ხევი",), "балка", ("ущелье",), ""),
    Kind("garden", ("სკვერი", "ბაღი"), "сквер", ("сад",), ""),
    Kind("park", ("პარკი",), "парк", (), ""),
    Kind("river", ("მდინარე",), "река", (), "water*"),
    Kind("stream", ("ღელე",), "ручей", (), "water*"),
    Kind("canal", ("არხი",), "канал", (), "water*"),
    Kind("lake", ("ტბა",), "озеро", (), "water*"),
    Kind("metro", (), "метро", ("станция",), ""),
    Kind("district", ("რაიონი",), "район", (), "area*"),
    Kind("neighbourhood", (), "микрорайон", ("квартал", "местность"), "area*"),
    Kind("other", (), "прочее", (), ""),
    # --- landmarks (pipeline/osm/landmarks.py): kind comes from OSM tags, never from the name of a
    # street; appended after "other" so that the indices of the kinds above stay unchanged.
    # "landmark" goes first: the group is offered in autocomplete as "место*".
    Kind("landmark", (), "место", ("места", "достопримечательность"), "place*"),
    Kind("museum", ("მუზეუმი",), "музей", ("галерея",), "place*"),
    Kind("theatre", ("თეატრი",), "театр", ("опера", "филармония", "цирк", "концертзал"), "place*"),
    Kind("cinema", ("კინოთეატრი",), "кинотеатр", ("кино",), "place*"),
    Kind("monument", ("ძეგლი", "მემორიალი", "ბიუსტი"), "памятник",
         ("монумент", "мемориал", "статуя", "скульптура", "бюст"), "place*"),
    Kind("plaque", ("დაფა",), "доска", ("мемдоска", "табличка", "звезда"), "place*"),
    Kind("church", ("ეკლესია", "ტაძარი", "მონასტერი"), "храм",
         ("церковь", "собор", "монастырь", "базилика", "часовня"), "place*"),
    Kind("mosque", ("მეჩეთი",), "мечеть", (), "place*"),
    Kind("synagogue", ("სინაგოგა",), "синагога", (), "place*"),
    Kind("fortress", ("ციხე", "ციხესიმაგრე"), "крепость", ("замок", "цитадель"), "place*"),
    Kind("palace", ("სასახლე",), "дворец", ("усадьба",), "place*"),
    Kind("bath", ("აბანო",), "баня", ("бани",), "place*"),
    Kind("stadium", ("სტადიონი", "არენა"), "стадион", ("арена", "спорткомплекс"), "place*"),
    Kind("mall", ("მოლი",), "торгцентр", ("тц", "молл", "универмаг"), "place*"),
    Kind("market", ("ბაზარი", "ბაზრობა"), "рынок", ("базар",), "place*"),
    Kind("station", ("სადგური", "ავტოსადგური"), "вокзал", ("автовокзал", "жд"), "place*"),
    Kind("university", ("უნივერსიტეტი", "აკადემია", "ინსტიტუტი"), "университет",
         ("вуз", "академия", "институт", "консерватория", "колледж"), "place*"),
    Kind("school", ("სკოლა",), "школа", ("лицей", "гимназия"), "place*"),
    Kind("library", ("ბიბლიოთეკა",), "библиотека", (), "place*"),
    Kind("cemetery", ("სასაფლაო", "პანთეონი"), "кладбище", ("пантеон",), "place*"),
    Kind("factory", ("ქარხანა", "ფაბრიკა"), "завод", ("фабрика", "комбинат"), "place*"),
    Kind("residential", (), "жилкомплекс", ("жк",), "place*"),
]
KIND_CODES: list[str] = [k.code for k in KIND_TABLE]
KIND_INDEX: dict[str, int] = {k.code: i for i, k in enumerate(KIND_TABLE)}
KIND_GROUP: dict[str, str] = {k.code: k.group for k in KIND_TABLE}
LANE_KINDS = frozenset(k.code for k in KIND_TABLE if k.group == "lane*")
#: Landmark kinds (group "place*"): set from OSM tags by the landmarks stage. Their Georgian words
#: are shipped in ``core.kinds`` (search and DSL), but never turn a street name into a landmark.
PLACE_GROUP = "place*"
LANDMARK_KINDS = frozenset(k.code for k in KIND_TABLE if k.group == PLACE_GROUP)


def kinds_for_pack() -> list[dict]:
    """``core.kinds`` entries: ``{"code", "ka", "ru", "syn", "group"}`` (``ka`` = first type word)."""
    return [
        {"code": k.code, "ka": k.ka[0] if k.ka else "", "ru": k.ru, "syn": list(k.syn), "group": k.group}
        for k in KIND_TABLE
    ]


# ------------------------------------------------------------------------- type-word tables
#: Georgian type word (nominative, last word of the name) -> kind code.
KA_TYPE_WORDS: dict[str, str] = {w: k.code for k in KIND_TABLE for w in k.ka if k.group != PLACE_GROUP}
KA_TYPE_WORDS.update({
    "ბულვარი": "alley",
    "შესასვლელი": "passage",      # entrance
    "გადასასვლელი": "passage",    # (pedestrian) crossing
    "ტრასა": "highway",
    "მაგისტრალი": "highway",
    "ბილიკი": "other",            # path
    "კიბე": "other",              # stairs
    "კიბეები": "other",
    "ქედი": "other",              # ridge
    "წრე": "other",               # circle
    "დასახლება": "neighbourhood",
    "მიკრორაიონი": "neighbourhood",
    "კვარტალი": "neighbourhood",
})
#: Type words that precede the proper name ("მდინარე ვერე").
KA_PREFIX_TYPES: dict[str, str] = {"მდინარე": "river", "ტბა": "lake", "არხი": "canal"}

#: Misspelled / abbreviated type words -> canonical (only replaced as the last word).
KA_TYPE_TYPOS: dict[str, str] = {
    "ქუჯა": "ქუჩა", "ქყჩა": "ქუჩა", "ქუჩას": "ქუჩა", "ქ.": "ქუჩა", "ქ": "ქუჩა", "ქუჩ.": "ქუჩა",
    "ქუჩა.": "ქუჩა", "ქუცა": "ქუჩა", "ქიჩა": "ქუჩა", "ქუჩაა": "ქუჩა",
    "შეს.": "შესახვევი", "შეს": "შესახვევი", "შესახ.": "შესახვევი", "შესახვ.": "შესახვევი",
    "შესახვევი.": "შესახვევი", "შესახვევ": "შესახვევი", "შესახვეი": "შესახვევი",
    "ჩიხ.": "ჩიხი", "ჩიხი.": "ჩიხი",
    "გამზ.": "გამზირი", "გამზ": "გამზირი", "გამზირ": "გამზირი",
    "შესასვლეი": "შესასვლელი", "შესასლელი": "შესასვლელი",
    "გასას.": "გასასვლელი", "გასასვლეი": "გასასვლელი", "გასასველი": "გასასვლელი", "გასული": "გასასვლელი",
    "გად.": "გადასასვლელი", "მოედ.": "მოედანი", "ხეივ.": "ხეივანი",
}

#: Genitive / nominative forms of street-like type words inside a nested lane name.
KA_PARENT_TYPES: dict[str, str] = {
    "ქუჩის": "street", "ქუჩა": "street", "გამზირის": "avenue", "გამზირი": "avenue",
    "გზატკეცილის": "highway", "გზის": "road", "სანაპიროს": "embankment",
    "აღმართის": "ascent", "დაღმართის": "descent", "ხეივნის": "alley", "რიგის": "row",
    "შესახვევის": "lane", "ჩიხის": "dead_end", "გასასვლელის": "passage", "მოედნის": "square",
}

#: Settlement markers: "ნავთლუღის I დასახლების" -> eponym "ნავთლუღი".
SETTLEMENT = frozenset({
    "დასახლების", "დასახლება", "კვარტალის", "კვარტალი", "მიკრორაიონის", "მიკრორაიონი",
    "დასახლებ", "დასახლებას", "მასივის", "მასივი",
})

#: Titles / ranks: optional in the family key when at least two other tokens remain.
TITLES = frozenset({
    "მეფე", "მეფის", "მეფეს", "დედოფალი", "დედოფლის", "დედოფალის", "ბერი", "ბერის",
    "კათოლიკოსი", "კათოლიკოს", "კათოლიკოსის", "კათოლიკოს-პატრიარქი", "კათოლიკოს-პატრიარქის",
    "პატრიარქი", "პატრიარქის", "ეპისკოპოსი", "ეპისკოპოსის", "მთავარეპისკოპოსის",
    "პრეზიდენტი", "პრეზიდენტ", "პრეზიდენტის", "მარშალი", "მარშალ", "მარშალის",
    "გენერალი", "გენერალ", "გენერალის", "აკადემიკოსი", "აკადემიკოს", "აკადემიკოსის",
    "პროფესორი", "პროფესორ", "პროფესორის", "მღვდელი", "მღვდელ", "მღვდლის", "დეკანოზი",
    "დეკანოზ", "დეკანოზის", "არქიმანდრიტი", "არქიმანდრიტ", "ექიმი", "ექიმ", "ექიმის",
    "მგოსანი", "მგოსნის", "თავადი", "თავად", "თავადის", "ბატონი", "ბატონ", "ქალბატონი",
    "მწერალი", "მწერლის", "პოეტი", "პოეტის", "კომპოზიტორი", "კომპოზიტორის", "მხატვარი",
    "მხატვრის", "სარდალი", "სარდლის",
})
#: Always dropped from the family key ("saint", "and").
KEY_DROP = frozenset({"წმინდა", "წმინდის", "წმ.", "წმ", "და"})

#: Ordinal words: nominative before a type word = lane ordinal, inside an eponym = regnal number.
_ORD_WORDS = {
    1: ("პირველი", "პირველის"), 2: ("მეორე", "მეორის", "მეორეს"), 3: ("მესამე", "მესამის", "მესამეს"),
    4: ("მეოთხე", "მეოთხის", "მეოთხეს"), 5: ("მეხუთე", "მეხუთის", "მეხუთეს"),
    6: ("მეექვსე", "მეექვსის", "მეექვსეს"), 7: ("მეშვიდე", "მეშვიდის", "მეშვიდეს"),
    8: ("მერვე", "მერვის", "მერვეს"), 9: ("მეცხრე", "მეცხრის", "მეცხრეს"),
    10: ("მეათე", "მეათის", "მეათეს"), 11: ("მეთერთმეტე", "მეთერთმეტის"), 12: ("მეთორმეტე", "მეთორმეტის"),
}
ORD_WORD_VALUE: dict[str, int] = {w: n for n, ws in _ORD_WORDS.items() for w in ws}
ORD_WORD_NOM: dict[str, int] = {ws[0]: n for n, ws in _ORD_WORDS.items()}

#: Royal / church names that take a regnal number ("ერეკლე II", "თეიმურაზ I").
ROYAL_NAMES = frozenset({
    "ერეკლე", "თეიმურაზ", "თეიმურაზი", "ვახტანგ", "ვახტანგი", "ფარნავაზ", "ფარნავაზი", "დავით",
    "გიორგი", "ბაგრატ", "სოლომონ", "დემეტრე", "ლუარსაბ", "ალექსანდრე", "სიმონ", "კონსტანტინე",
    "ქეთევან", "თამარ", "მირიან", "ფარსმან", "აბრაჰამ", "მელქისედეკ", "ანტონ", "კირიონ", "ილია",
    "ლევან", "როსტომ", "არჩილ", "იესე", "ბაქარ", "გურგენ", "აშოტ", "ადარნასე", "ვამეყ", "ლეონ",
    "ფარსმანი", "მითრიდატე", "საურმაგ", "ამაზასპ",
})

_SURNAME_NOM_SUFFIX = ("შვილი", "ძე", "ია", "ავა", "უა", "ანი", "ელი", "ური", "ული", "ონი", "ენი",
                       "ინი", "ეთი", "ოვი", "ევი", "ინა", "ოვა", "ევა", "აია", "ანტი")
_VOW = frozenset("აეიოუ")

_KA_CHAR = re.compile(r"[ა-ჿᲐ-Ჿ]")
_LAT_CHAR = re.compile(r"[A-Za-z]")
_CYR_CHAR = re.compile(r"[А-Яа-яЁё]")
_MTAVRULI = re.compile(r"[Ა-Ჿ]")
_ROMAN = re.compile(r"[IVXLC]+")
_ROMAN_TOKEN = re.compile(r"(?<![A-Za-z])[IVXLC]+(?![A-Za-z])")
_ROMAN_GEN = re.compile(r"([IVXLC]+)-(?:ის|ს|ე|ლი)")
_ORD_TOKEN = re.compile(r"(?:(\d+)(?:-?(?:ე|ლი|ი))?\.?|მე-(\d+)(?:-ე)?|([IVXLC]+)(?:-(?:ე|ლი))?)")
_NUM_GEN = re.compile(r"\d+-(?:ის|ე|ლი)")
_INITIAL = re.compile(r"[ა-ჰ]\.")
_PAREN = re.compile(r"\s*\(([^)]*)\)\s*")
_WS = re.compile(r"\s+")


def _roman_to_int(s: str) -> int | None:
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total, prev = 0, 0
    for ch in reversed(s):
        v = vals.get(ch)
        if v is None:
            return None
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return total or None


def _int_to_roman(n: int) -> str:
    out = []
    for v, r in ((100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out.append(r)
            n -= v
    return "".join(out)


# --------------------------------------------------------------------------------- dataclass
@dataclass
class ParsedName:
    raw: str
    kind: str                       # canonical kind code ('other' if the type is not recognised)
    type_word: str | None           # canonical Georgian type word, e.g. 'შესახვევი'
    ord: int | None                 # lane ordinal: 'II შესახვევი' -> 2
    regnal: str | None              # regnal number inside the eponym: 'ერეკლე II' -> 'II'
    base_ka: str                    # eponym part as written (genitive): 'ლადო ასათიანის'
    eponym_nom_ka: str | None       # nominative guess (only the last word is declined)
    family_key: str                 # order/spelling-insensitive key; '#...' for non-eponymic names
    is_derived: bool                # lane / dead end / passage named after another street
    parent_family_key: str | None   # family key of the parent street (derived names only)
    parent_ord: int | None          # 'ზიგზაგის I ქუჩის ჩიხი' -> 1
    parent_kind: str | None         # 'ზიგზაგის I ქუჩის ჩიხი' -> 'street'
    flags: list[str]
    # --- extras (not part of the frozen interface, safe to ignore)
    aliases: list[str] = field(default_factory=list)  # pseudonyms in parentheses: '(საშა)'
    alt_ka: str | None = None       # second name after ' / '
    base_ru: str | None = None      # strip_type(normalize_ru_name(ru))
    base_en: str | None = None      # strip_type(en)


# ----------------------------------------------------------------------------- normalization
def _ka_lower(s: str) -> str:
    """Mtavruli / Asomtavruli -> Mkhedruli; other characters unchanged."""
    if not _MTAVRULI.search(s) and not re.search(r"[Ⴀ-Ⴥ]", s):
        return s
    out = []
    for ch in s:
        o = ord(ch)
        if 0x1C90 <= o <= 0x1CBF:
            ch = ch.lower()
        elif 0x10A0 <= o <= 0x10C5:
            ch = chr(o - 0x10A0 + 0x10D0)
        out.append(ch)
    return "".join(out)


def _normalize_ka(s: str) -> tuple[str, bool]:
    """Implementation of :func:`normalize_ka`; also reports whether the type word was fixed."""
    if not s:
        return "", False
    t = _ka_lower(s)
    t = t.replace(chr(0xA0), " ").replace(chr(0x200B), "")
    t = re.sub(r"[„“”\"«»]", "", t)
    t = re.sub(r"\s+[-–—]+\s+", " — ", t)          # spaced dash: route / alternative separator
    t = re.sub(r"(?<=\S)[–‐‑](?=\S)", "-", t)       # en dash / unicode hyphen inside a word
    t = re.sub(r"(?<=[ა-ჰ])\.(?=[ა-ჰ])", ". ", t)   # "ზ.გამსახურდია"
    t = _WS.sub(" ", t).strip(" ,;")
    if not t:
        return "", False
    toks = t.split(" ")
    last = toks[-1]
    fixed = False
    if len(toks) > 1 and last in KA_TYPE_TYPOS:
        toks[-1] = KA_TYPE_TYPOS[last]
        fixed = True
    elif len(toks) > 1 and last.endswith(".") and last[:-1] in KA_TYPE_WORDS:
        toks[-1] = last[:-1]
        fixed = True
    return " ".join(toks), fixed


def normalize_ka(s: str) -> str:
    """Canonical spelling of a Georgian name.

    Mtavruli -> Mkhedruli, whitespace and dashes ("–" inside a word -> "-", spaced dashes -> " — "),
    quotes removed, initials spaced ("ზ.გამსახურდია" -> "ზ. გამსახურდია"), and common typos /
    abbreviations of the final type word fixed (ქუჯა, ქყჩა, ქუჩას, ქ., შეს. ...).
    """
    return _normalize_ka(s)[0]


_RU_ORD = re.compile(r"(?<![\w-])(\d+)\s*-?\s*(ый|ой|ий|й|ая|яя|я|ое|ее|ье|е|го|ого|му|ому|м|ом)(?![\w])", re.IGNORECASE)
_RU_ABBR = [
    (re.compile(r"(?i)(?<!\w)пер\.(?=\s|$)"), "переулок"),
    (re.compile(r"(?i)(?<!\w)ул\.(?=\s|$)"), "улица"),
    (re.compile(r"(?i)(?<!\w)пр-т\.?(?=\s|$)"), "проспект"),
    (re.compile(r"(?i)(?<!\w)просп\.(?=\s|$)"), "проспект"),
    (re.compile(r"(?i)(?<!\w)пл\.(?=\s|$)"), "площадь"),
    (re.compile(r"(?i)(?<!\w)туп\.(?=\s|$)"), "тупик"),
    (re.compile(r"(?i)(?<!\w)наб\.(?=\s|$)"), "набережная"),
]
_RU_ORD_FIX = {
    "ый": "й", "ой": "й", "ий": "й", "й": "й", "ая": "я", "яя": "я", "я": "я",
    "ое": "е", "ее": "е", "ье": "е", "е": "е", "го": "го", "ого": "го", "му": "му", "ому": "му",
    "м": "м", "ом": "м",
}

RU_TYPE_WORDS: dict[str, str] = {
    "улица": "street", "проспект": "avenue", "шоссе": "highway", "трасса": "highway",
    "дорога": "road", "набережная": "embankment", "подъём": "ascent", "подъем": "ascent",
    "спуск": "descent", "аллея": "alley", "бульвар": "alley", "ряд": "row", "переулок": "lane",
    "тупик": "dead_end", "проезд": "passage", "съезд": "passage", "проход": "passage",
    "въезд": "passage", "площадь": "square", "мост": "bridge", "тоннель": "tunnel",
    "туннель": "tunnel", "балка": "ravine", "ущелье": "ravine", "овраг": "ravine", "сквер": "garden",
    "сад": "garden", "парк": "park", "река": "river", "ручей": "stream", "канал": "canal",
    "озеро": "lake", "метро": "metro", "станция": "metro", "район": "district",
    "микрорайон": "neighbourhood", "квартал": "neighbourhood", "местность": "neighbourhood",
    "посёлок": "neighbourhood", "поселок": "neighbourhood", "лестница": "other", "тропа": "other",
    "хребет": "other", "круг": "other",
}
_RU_TYPE_GEN = (
    "улицы", "проспекта", "шоссе", "дороги", "набережной", "переулка", "тупика", "проезда",
    "площади", "моста", "аллеи", "подъёма", "подъема", "спуска",
)
_RU_TYPES_RE = re.compile(
    r"(?i)(?<![\w-])(?:" + "|".join(sorted(set(RU_TYPE_WORDS) | set(_RU_TYPE_GEN), key=len, reverse=True)) + r")(?![\w-])"
)
_RU_ORD_STRIP = re.compile(r"(?i)(?<![\w-])\d+-(?:й|я|е)(?![\w-])")

EN_TYPE_WORDS: dict[str, str] = {
    "street": "street", "st": "street", "str": "street", "avenue": "avenue", "ave": "avenue",
    "av": "avenue", "highway": "highway", "hwy": "highway", "road": "road", "rd": "road",
    "embankment": "embankment", "ascent": "ascent", "rise": "ascent", "descent": "descent",
    "alley": "alley", "boulevard": "alley", "blvd": "alley", "row": "row", "lane": "lane",
    "dead end": "dead_end", "deadend": "dead_end", "dead-end": "dead_end", "passage": "passage",
    "exit": "passage", "entrance": "passage", "drive": "passage", "turn": "lane", "square": "square", "sq": "square",
    "bridge": "bridge", "tunnel": "tunnel", "gorge": "ravine", "ravine": "ravine", "garden": "garden",
    "park": "park", "river": "river", "stream": "stream", "canal": "canal", "lake": "lake",
    "district": "district", "steps": "other", "stairs": "other", "staircase": "other",
    "trail": "other", "path": "other",
}
_EN_TYPES_RE = re.compile(
    r"(?i)(?<![\w-])(?:" + "|".join(re.escape(w) for w in sorted(EN_TYPE_WORDS, key=len, reverse=True)) + r")\.?(?![\w-])"
)
_EN_ORD_RE = re.compile(r"(?i)(?<![\w-])\d+(?:st|nd|rd|th)(?![\w-])")


def normalize_ru_name(s: str) -> str:
    """Normalize a Russian name: ordinals ``1-ый``/``1-ой``/``2-ой``/``1й`` -> ``1-й`` (``1-ая`` -> ``1-я``),
    abbreviations (``пер.`` -> ``переулок``, ``ул.`` -> ``улица``), lower-case type words, whitespace."""
    if not s:
        return ""
    t = s.replace(chr(0xA0), " ").replace("–", "-").replace("‑", "-")
    t = _WS.sub(" ", t).strip()
    t = _RU_ORD.sub(lambda m: m.group(1) + "-" + _RU_ORD_FIX[m.group(2).lower()], t)
    for rx, rep in _RU_ABBR:
        t = rx.sub(rep, t)
    t = _RU_TYPES_RE.sub(lambda m: m.group(0).lower(), t)
    return t


def _script(s: str) -> str:
    if _KA_CHAR.search(s):
        return "ka"
    if _CYR_CHAR.search(s):
        return "ru"
    return "lat"


def _strip_ru(s: str) -> str:
    t = normalize_ru_name(s)
    t = _RU_ORD_STRIP.sub(" ", t)
    t = _RU_TYPES_RE.sub(" ", t)
    return _WS.sub(" ", t).strip(" ,.-")


def _strip_en(s: str) -> str:
    t = _WS.sub(" ", s.replace("–", "-")).strip()
    t = _EN_ORD_RE.sub(" ", t)
    t = _EN_TYPES_RE.sub(" ", t)
    return _WS.sub(" ", t).strip(" ,.-")


def strip_type(name: str) -> str:
    """Base name without the type word and the ordinal (Georgian, Russian or Latin input).

    ``ლადო ასათიანის II შესახვევი`` -> ``ლადო ასათიანის``; ``2-й переулок Ладо Асатиани`` ->
    ``Ладо Асатиани``; ``Lado Asatiani 2nd Lane`` -> ``Lado Asatiani``.
    """
    if not name:
        return ""
    sc = _script(name)
    if sc == "ka":
        p = parse_name(name)
        if p.base_ka:
            return p.base_ka
        return normalize_ka(name) if p.type_word is None else ""
    if sc == "ru":
        return _strip_ru(name)
    return _strip_en(name)


def _ru_kind(ru: str | None) -> str | None:
    if not ru:
        return None
    for m in _RU_TYPES_RE.finditer(ru):
        k = RU_TYPE_WORDS.get(m.group(0).lower())
        if k:
            return k
    return None


def _en_kind(en: str | None) -> str | None:
    if not en:
        return None
    found = [EN_TYPE_WORDS[m.group(0).lower().rstrip(".")] for m in _EN_TYPES_RE.finditer(en)
             if m.group(0).lower().rstrip(".") in EN_TYPE_WORDS]
    if not found:
        return None
    # head noun: last type word ("Rustaveli Avenue Bridge"), first one in "Steps to X Street"
    return found[0] if re.search(r"(?i)\b(?:of|to)\b", en) else found[-1]


_RU_ORD_FIND = re.compile(r"(?<![\w-])(\d+)-(?:й|я|е)(?![\w-])")


def _ru_ord(ru_norm: str) -> int | None:
    m = _RU_ORD_FIND.search(ru_norm)
    return int(m.group(1)) if m else None


_RU_REGNAL_RE = re.compile(
    r"(?i)(?<![\w-])(?:[IVXLC]+|пирвели|меори|меоре|месаме|меотхе|мехуте|мееквсе|мееквси|"
    r"первого|первый|второго|второй|третьего|третий|i-го|ii-го)(?![\w-])"
)


# ------------------------------------------------------------------------------- key helpers
def _is_genitive(tok: str) -> bool:
    if tok.endswith("ის") or tok.endswith("-ს") or tok.endswith("თა"):
        return True
    return len(tok) > 2 and tok.endswith("ს") and tok[-2] in _VOW


def _is_surname_nom(tok: str) -> bool:
    return tok.endswith(_SURNAME_NOM_SUFFIX)


def key_tok(tok: str) -> str:
    """Spelling-tolerant token key: drop genitive and final vowel, fold syncope (ელ ~ ლ)."""
    if tok in ORD_WORD_VALUE:
        return _int_to_roman(ORD_WORD_VALUE[tok])
    m = _ROMAN_GEN.fullmatch(tok)
    if m:
        return m.group(1)
    if _ROMAN.fullmatch(tok) or _INITIAL.fullmatch(tok):
        return tok
    t = tok
    if t.endswith("-ის"):
        t = t[:-3]
    elif t.endswith("-ს"):
        t = t[:-2]
    elif t.endswith("ის") and len(t) > 3:
        t = t[:-2]
    elif t.endswith("ს") and len(t) > 2 and t[-2] in _VOW:
        t = t[:-1]
    while len(t) > 3 and t[-1] in _VOW:          # სამტრედია ~ სამტრედიის, ფშაველა ~ ფშაველას
        t = t[:-1]
    if len(t) > 3 and t[-2] in "აეო" and t[-1] in "ლრნ":   # syncope: აღმაშენებელ ~ აღმაშენებლ
        t = t[:-2] + t[-1]
    return t


_TITLE_KEYS = frozenset(key_tok(t) for t in TITLES)

#: Nominative of syncopated genitives that the generic rule gets wrong.
_NOM_EXCEPTIONS = {
    "დედოფლის": "დედოფალი", "აღმაშენებლის": "აღმაშენებელი", "გორგასლის": "გორგასალი",
    "მეფის": "მეფე", "მღვდლის": "მღვდელი", "მწერლის": "მწერალი", "მხატვრის": "მხატვარი",
    "სარდლის": "სარდალი", "მგოსნის": "მგოსანი", "მოედნის": "მოედანი", "ხეივნის": "ხეივანი",
    "ქველის": "ქველი", "ბრწყინვალის": "ბრწყინვალე", "დიდის": "დიდი",
}


def nominative_tok(tok: str) -> str:
    """Nominative guess for one Georgian token in genitive (best effort)."""
    if tok in _NOM_EXCEPTIONS:
        return _NOM_EXCEPTIONS[tok]
    m = _ROMAN_GEN.fullmatch(tok)
    if m:
        return m.group(1)
    if tok in ORD_WORD_VALUE and not tok.endswith(("ი", "ე")):
        n = ORD_WORD_VALUE[tok]
        return _ORD_WORDS[n][0]
    if tok.endswith("-ის") or tok.endswith("-ს"):
        return tok.rsplit("-", 1)[0]
    if tok.endswith("ძის"):
        return tok[:-3] + "ძე"
    if tok.endswith("იის") and len(tok) > 4:     # სამტრედიის -> სამტრედია
        return tok[:-2] + "ა"
    if tok.endswith("ის") and len(tok) > 3:
        return tok[:-2] + "ი"
    if tok.endswith("თა"):
        return tok
    if len(tok) > 2 and tok.endswith("ს") and tok[-2] in _VOW:
        return tok[:-1]
    return tok


def _eponym_core(toks: list[str]) -> list[str]:
    """Eponym tokens: cut "<X> სახელობის ...", drop settlement markers and their numbers."""
    if "სახელობის" in toks:
        i = toks.index("სახელობის")
        if i > 0:
            toks = toks[:i]
    core: list[str] = []
    for i, t in enumerate(toks):
        if t in SETTLEMENT:
            continue
        # settlement / district number after a genitive toponym ("ნავთლუღის I", "ავჭალა 2-ის")
        if i > 0 and (_ROMAN.fullmatch(t) or t.isdigit() or _NUM_GEN.fullmatch(t)) and (
            _is_genitive(toks[i - 1]) or toks[i - 1] in SETTLEMENT or (i + 1 < len(toks) and toks[i + 1] in SETTLEMENT)
        ):
            continue
        core.append(t)
    return core or toks


def _family_key(core: list[str]) -> str:
    toks: list[str] = []
    for t in core:
        if t in KEY_DROP:
            continue
        for part in t.split("-") if "-" in t and not _ROMAN_GEN.fullmatch(t) and not _NUM_GEN.fullmatch(t) else (t,):
            if part and part not in KEY_DROP:
                toks.append(key_tok(part))
    non_title = [t for t in toks if t not in _TITLE_KEYS]
    if len(non_title) >= 2:
        toks = non_title
    return " ".join(sorted(toks))


def _ord_value(tok: str) -> tuple[int | None, bool]:
    """(value, is_roman) of an ordinal token, (None, False) if not an ordinal."""
    if tok in ORD_WORD_NOM:
        return ORD_WORD_NOM[tok], False
    m = _ORD_TOKEN.fullmatch(tok)
    if not m:
        return None, False
    if m.group(1):
        return int(m.group(1)), False
    if m.group(2):
        return int(m.group(2)), False
    return _roman_to_int(m.group(3)), True


def _regnal_context(prev: list[str]) -> bool:
    """A roman numeral right before the type word is a regnal number when the eponym is a bare
    (royal) given name in nominative: "ერეკლე II შესახვევი", "მეფე ფარნავაზი I ქუჩა"."""
    if not prev or _is_genitive(prev[-1]):
        return False
    names = [t for t in prev if t not in TITLES and t not in KEY_DROP]
    if len(names) != 1:
        return False
    t = names[0]
    if t in ROYAL_NAMES:
        return True
    return not _is_genitive(t) and not _is_surname_nom(t) and not t[0].isdigit()


_CONS_DROP = re.compile(r"[aeiouh]")


def _cons(s: str) -> str:
    """Consonant skeleton used to compare ka with ru/en spellings (f ~ p, doubles collapsed)."""
    t = _CONS_DROP.sub("", skeleton(s).replace("f", "p").replace("w", "v"))
    return "".join(c for i, c in enumerate(t) if i == 0 or c != t[i - 1])


def _consistency_flags(core: list[str], base_ru: str | None, base_en: str | None) -> list[str]:
    """'ru_mismatch' / 'en_mismatch' when the eponym's main word is not found in the ru/en name
    (OSM errors like 'თამარ ჭავჭავაძის ქუჩა' = 'улица Николы Теслы', or translated names)."""
    stem = None
    for t in reversed(core):
        if (t in TITLES or t in KEY_DROP or t in ORD_WORD_VALUE or _ROMAN_GEN.fullmatch(t)
                or _ROMAN.fullmatch(t) or t[0].isdigit() or _INITIAL.fullmatch(t)):
            continue
        stem = t
        break
    if stem is None:
        return []
    t = stem.split("-")[-1]
    if t.endswith("ის") and len(t) > 3:
        t = t[:-2]
    elif t.endswith("ს") and len(t) > 2 and t[-2] in _VOW:
        t = t[:-1]
    if t.endswith("ებ") and len(t) > 4:          # plural: ორბელების -> Орбели
        t = t[:-2]
    c = _cons(t)[:4]
    if len(c) < 3:
        return []
    out = []
    if base_ru and c not in _cons(base_ru):
        out.append("ru_mismatch")
    if base_en and c not in _cons(base_en):
        out.append("en_mismatch")
    return out


# -------------------------------------------------------------------------------- parse_name
def _pseudo(raw: str, norm: str, kind: str, type_word: str | None, flags: list[str],
            ru: str | None, en: str | None) -> ParsedName:
    return ParsedName(
        raw=raw, kind=kind, type_word=type_word, ord=None, regnal=None, base_ka=norm,
        eponym_nom_ka=None, family_key="#" + norm, is_derived=False, parent_family_key=None,
        parent_ord=None, parent_kind=None, flags=flags,
        base_ru=_strip_ru(ru) if ru else None, base_en=_strip_en(en) if en else None,
    )


def parse_name(ka: str, ru: str | None = None, en: str | None = None) -> ParsedName:
    """Parse a (Georgian) name; ``ru``/``en`` only refine ambiguous cases and set flags.

    Results are memoized on ``(ka, ru, en)``; every call returns a fresh object (lists copied),
    so callers may mutate it.
    """
    p = _parse_cached(ka or "", ru or None, en or None)
    return replace(p, flags=list(p.flags), aliases=list(p.aliases))


@lru_cache(maxsize=1 << 16)
def _parse_cached(ka: str, ru: str | None, en: str | None) -> ParsedName:
    raw = ka
    flags: list[str] = []
    if _MTAVRULI.search(raw):
        flags.append("mtavruli")
    s, typo = _normalize_ka(raw)
    if typo:
        flags.append("typo_type")
    ru_n = normalize_ru_name(ru) if ru else None
    base_ru = _strip_ru(ru) if ru else None
    base_en = _strip_en(en) if en else None

    if not s:
        return ParsedName(raw, "other", None, None, None, "", None, "#", False, None, None, None,
                          flags + ["empty"], base_ru=base_ru, base_en=base_en)

    # ---- non-Georgian or mixed-script names
    if not _KA_CHAR.search(s):
        kind = (_ru_kind(s) if _CYR_CHAR.search(s) else _en_kind(s)) or _ru_kind(ru_n) or _en_kind(en) or "other"
        if " — " in s:
            kind = "road"
            flags.append("route")
        flags.append("non_ka")
        log.debug("non-Georgian name %r -> %s", raw, kind)
        return _pseudo(raw, s, kind, None, flags, ru, en)
    s_noroman = _ROMAN_TOKEN.sub("", s)
    if _LAT_CHAR.search(s_noroman) or _CYR_CHAR.search(s_noroman):
        flags.append("latin_in_ka" if _LAT_CHAR.search(s_noroman) else "cyrillic_in_ka")
        # keep the longest run of Georgian words: "Trail of Meliqidze - მელიქიძის ბილიკი"
        runs = re.findall(r"[ა-ჰ][ა-ჰ0-9IVXLC.\- ]*[ა-ჰ.]|[ა-ჰ]", s)
        if runs:
            s = normalize_ka(max(runs, key=len).strip(" -"))

    alt_ka = None
    if " / " in s:
        s, alt_ka = (x.strip() for x in s.split(" / ", 1))
        flags.append("alt_name")
    if " — " in s:
        flags.append("route")
        return _pseudo(raw, s, "road", None, flags, ru, en)

    aliases = [a.strip() for a in _PAREN.findall(s) if a.strip()]
    s_core = _WS.sub(" ", _PAREN.sub(" ", s)).strip() if aliases else s
    if aliases:
        flags.append("alias")
    toks = s_core.split(" ")
    kind: str | None = None
    type_word: str | None = None
    if len(toks) > 1 and toks[-1] in KA_TYPE_WORDS:
        type_word = toks.pop()
        kind = KA_TYPE_WORDS[type_word]
    elif len(toks) > 1 and toks[0] in KA_PREFIX_TYPES:
        type_word = toks.pop(0)
        kind = KA_PREFIX_TYPES[type_word]
    elif len(toks) == 1 and toks[0] in KA_TYPE_WORDS:
        type_word = toks.pop()
        kind = KA_TYPE_WORDS[type_word]

    ord_: int | None = None
    regnal: str | None = None
    parent_kind: str | None = None
    parent_ord: int | None = None
    ru_ord = _ru_ord(ru_n) if ru_n else None

    if type_word and toks:
        val, is_roman = _ord_value(toks[-1])
        if val is not None:
            if is_roman and _regnal_context(toks[:-1]):
                ru_says_ord = (ru_ord == val and not _RU_REGNAL_RE.search(base_ru or ""))
                if ru_says_ord:
                    ord_ = val
                    toks.pop()
                    flags.append("regnal_ambiguous")
                else:
                    regnal = toks[-1]
                    flags.append("regnal_ambiguous")
                    if ru_ord is not None:
                        flags.append("regnal_mismatch_ru")
            else:
                ord_ = val
                toks.pop()
        # nested parent: "<base> [ord] ქუჩის <ord> ჩიხი"
        if kind in LANE_KINDS and len(toks) > 1 and toks[-1] in KA_PARENT_TYPES:
            parent_kind = KA_PARENT_TYPES[toks.pop()]
            if len(toks) > 1:
                pval, _ = _ord_value(toks[-1])
                if pval is not None:
                    parent_ord = pval
                    toks.pop()
    if kind is None:
        kind = _ru_kind(ru_n) or _en_kind(en)
        if kind:
            flags.append("type_from_ru" if _ru_kind(ru_n) else "type_from_en")
        else:
            kind = "other"
            flags.append("no_type")
            log.debug("no type word in %r (ru=%r, en=%r)", raw, ru, en)

    base_toks = toks
    base_ka = " ".join(base_toks)
    if aliases and base_toks:
        # keep the pseudonym in base_ka as written: "ალექსანდრე (საშა) იოსელიანის"
        base_ka = _base_with_aliases(s, base_toks)

    if not base_toks:
        fk = "#" + s_core
        return ParsedName(raw, kind, type_word, ord_, None, "", None, fk, False, None, parent_ord,
                          parent_kind, flags, aliases, alt_ka, base_ru, base_en)

    # regnal numbers inside the eponym: "ფარსმან II ქველის", "თეიმურაზ I-ის", "ერეკლე მეორის"
    if regnal is None:
        for i, t in enumerate(base_toks):
            if i == 0:
                continue
            prev = base_toks[i - 1]
            if _is_genitive(prev) or prev in SETTLEMENT:
                continue
            m = _ROMAN_GEN.fullmatch(t)
            if m:
                regnal = m.group(1)
                break
            if _ROMAN.fullmatch(t):
                regnal = t
                break
            if t in ORD_WORD_VALUE and not prev[0].isdigit():
                regnal = _int_to_roman(ORD_WORD_VALUE[t])
                break

    core = _eponym_core(base_toks)
    family_key = _family_key(core) or ("#" + s_core)
    nom = " ".join(core[:-1] + [nominative_tok(core[-1])]) if core else None

    is_derived = kind in LANE_KINDS or parent_kind is not None
    flags.extend(_consistency_flags(core, base_ru, base_en))
    if ord_ is not None and ru_ord is not None and ru_ord != ord_:
        flags.append("ord_mismatch_ru")

    return ParsedName(
        raw=raw, kind=kind, type_word=type_word, ord=ord_, regnal=regnal, base_ka=base_ka,
        eponym_nom_ka=nom, family_key=family_key, is_derived=is_derived,
        parent_family_key=family_key if is_derived else None, parent_ord=parent_ord,
        parent_kind=parent_kind, flags=flags, aliases=aliases, alt_ka=alt_ka,
        base_ru=base_ru, base_en=base_en,
    )


def _base_with_aliases(s: str, base_toks: list[str]) -> str:
    """Re-insert parenthesised aliases into the base: walk ``s`` and keep its prefix that covers
    all base tokens (with the parentheses in between)."""
    want = len(base_toks)
    seen = 0
    out: list[str] = []
    depth = 0
    for tok in s.split(" "):
        if seen == want and depth == 0 and not tok.startswith("("):
            break
        out.append(tok)
        if tok.startswith("("):
            depth += 1
        if depth == 0:
            seen += 1
        if tok.endswith(")"):
            depth = max(0, depth - 1)
    return " ".join(out)


# ------------------------------------------------------------- generated ru / en names
_RU_ORD_SUFFIX = {
    "улица": "я", "набережная": "я", "аллея": "я", "площадь": "я", "дорога": "я", "балка": "я",
    "река": "я", "шоссе": "е", "озеро": "е", "метро": "е",
}
_RU_PARENT_GEN = {
    "street": ("улицы", "й"), "avenue": ("проспекта", "го"), "highway": ("шоссе", "го"),
    "road": ("дороги", "й"), "embankment": ("набережной", "й"), "ascent": ("подъёма", "го"),
    "descent": ("спуска", "го"), "alley": ("аллеи", "й"), "row": ("ряда", "го"),
    "lane": ("переулка", "го"), "dead_end": ("тупика", "го"), "passage": ("проезда", "го"),
    "square": ("площади", "й"),
}
_EN_TYPE = {
    "street": "Street", "avenue": "Avenue", "highway": "Highway", "road": "Road",
    "embankment": "Embankment", "ascent": "Ascent", "descent": "Descent", "alley": "Alley",
    "row": "Row", "lane": "Lane", "dead_end": "Dead End", "passage": "Passage", "square": "Square",
    "bridge": "Bridge", "tunnel": "Tunnel", "ravine": "Ravine", "garden": "Garden", "park": "Park",
    "river": "River", "stream": "Stream", "canal": "Canal", "lake": "Lake", "district": "District",
}


def _title_words(s: str) -> str:
    return " ".join("-".join(p[:1].upper() + p[1:] for p in w.split("-")) for w in s.split(" "))


def _en_ordinal(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def make_ru_name(p: ParsedName) -> str | None:
    """Russian name for features without ``name:ru`` (``feature.ru_generated = 1``), OSM style:
    ``2-й переулок Ладо Асатиани``, ``улица Эрекле II``, ``тупик 1-й улицы Зигзаги``.
    ``None`` for names that cannot be built (no eponym and no ordinal, pseudo families)."""
    if p.family_key.startswith("#") and not (p.ord and p.type_word):
        return None
    ru_type = KIND_TABLE[KIND_INDEX[p.kind]].ru if p.kind != "other" else ""
    epo = _title_words(ka_to_ru(p.eponym_nom_ka)) if p.eponym_nom_ka else ""
    parts: list[str] = []
    if p.ord:
        parts.append(f"{p.ord}-{_RU_ORD_SUFFIX.get(ru_type, 'й')}")
    if ru_type:
        parts.append(ru_type)
    if p.parent_kind in _RU_PARENT_GEN:
        gen, suf = _RU_PARENT_GEN[p.parent_kind]
        if p.parent_ord:
            parts.append(f"{p.parent_ord}-{suf}")
        parts.append(gen)
    if epo:
        parts.append(epo)
    return " ".join(parts) or None


def make_en_name(p: ParsedName) -> str | None:
    """Latin/English name for features without ``name:en``: ``Lado Asatiani 2nd Lane``."""
    if p.family_key.startswith("#") and not (p.ord and p.type_word):
        return None
    epo = ka_to_lat(p.eponym_nom_ka, capitalize=True) if p.eponym_nom_ka else ""
    parts = [epo] if epo else []
    if p.parent_kind in _EN_TYPE:
        if p.parent_ord:
            parts.append(_en_ordinal(p.parent_ord))
        parts.append(_EN_TYPE[p.parent_kind])
    if p.ord:
        parts.append(_en_ordinal(p.ord))
    if p.kind in _EN_TYPE:
        parts.append(_EN_TYPE[p.kind])
    return " ".join(parts) or None


__all__ = [
    "Kind", "KIND_TABLE", "KIND_CODES", "KIND_INDEX", "KIND_GROUP", "LANE_KINDS", "LANDMARK_KINDS", "PLACE_GROUP",
    "kinds_for_pack", "ParsedName", "parse_name", "normalize_ka", "normalize_ru_name", "strip_type", "key_tok",
    "nominative_tok", "make_ru_name", "make_en_name", "KA_TYPE_WORDS", "RU_TYPE_WORDS", "EN_TYPE_WORDS",
]
