"""Georgian (and ru/en) name helpers used by the eponym stages.

Ported from the research prototype (``temp_scripts/prototypes/etymology/ka_names.py``) and kept
independent of the OSM parser: the enrichment stages only need the eponym part of a name
(genitive), its nominative variants and a few lexical lists.
"""
from __future__ import annotations

import re

KA = re.compile(r"[ა-ჿ]")
VOW = set("აეიოუ")

TYPES_KA = {
    "ქუჩა", "ქუჩას", "ქ.", "გამზირი", "მოედანი", "გზატკეცილი", "სანაპირო", "ხეივანი", "ხიდი", "აღმართი",
    "დაღმართი", "ხევი", "გზა", "რიგი", "ქედი", "წრე", "ბილიკი", "კიბეები", "გვირაბი", "პარკი", "ბაღი", "სკვერი",
    "შესახვევი", "შეს.", "ჩიხი", "გასასვლელი", "შესასვლელი", "გადასასვლელი", "გად.", "მდინარე", "ტბა", "არხი",
    "ღელე", "დასახლება", "მიკრორაიონი", "კვარტალი",
}
TYPE_GEN = {"ქუჩის", "გამზირის", "გზატკეცილის", "შესახვევის", "ჩიხის", "მოედნის", "სანაპიროს", "აღმართის"}
SETTLEMENT = {"დასახლების", "დასახლება", "კვარტალის", "კვარტალი", "მიკრორაიონის", "მიკრორაიონი", "დასახლებ",
              "მასივის", "მასივი"}
ORD = re.compile(r"^(?:[IVXLC]+|\d+(?:-ე|-ლი|\.)?|მე-\d+)$")
MONTHS = {"იანვრის": 1, "თებერვლის": 2, "მარტის": 3, "აპრილის": 4, "მაისის": 5, "ივნისის": 6,
          "ივლისის": 7, "აგვისტოს": 8, "სექტემბრის": 9, "ოქტომბრის": 10, "ნოემბრის": 11, "დეკემბრის": 12}
TITLES = {
    "მეფე": "king", "მეფის": "king", "მეფეს": "king", "დედოფალი": "queen", "დედოფლის": "queen",
    "დედოფალის": "queen", "წმინდა": "saint", "წმ.": "saint", "ბერი": "monk", "კათოლიკოსი": "catholicos",
    "კათოლიკოს-პატრიარქის": "catholicos", "პატრიარქის": "patriarch", "ეპისკოპოსის": "bishop",
    "პრეზიდენტ": "president", "პრეზიდენტის": "president", "მარშალ": "marshal", "გენერალ": "general",
    "გენერალის": "general", "აკადემიკოს": "academician", "პროფესორ": "professor", "მღვდელ": "priest",
    "დეკანოზ": "archpriest", "არქიმანდრიტ": "archimandrite", "ექიმ": "doctor", "მგოსანი": "poet",
    "მოციქულთა": "apostles", "მთავარეპისკოპოსის": "archbishop", "თავადი": "prince",
}
# title words dropped when building label variants for Wikidata lookups
TITLE_DROP = {"წმინდა", "წმ.", "მეფე", "მეფის", "ბერი", "პრეზიდენტ", "მარშალ", "გენერალ", "აკადემიკოს",
              "პროფესორ", "მღვდელ", "დეკანოზ", "არქიმანდრიტ", "ექიმ", "მგოსანი", "თავადი", "დედოფალი"}
REGNAL = {"პირველის": 1, "მეორის": 2, "მეორეს": 2, "მესამის": 3, "მეოთხის": 4, "მეხუთის": 5,
          "მეექვსის": 6, "მეშვიდის": 7, "მერვის": 8, "პირველი": 1, "მეორე": 2, "მესამე": 3}
REGNAL_ROMAN = {"პირველის": "I", "პირველი": "I", "მეორის": "II", "მეორეს": "II", "მეორე": "II",
                "მესამის": "III", "მესამე": "III", "მეოთხის": "IV", "მეოთხე": "IV", "მეხუთის": "V",
                "მეექვსის": "VI", "მეშვიდის": "VII", "მერვის": "VIII", "მეცხრის": "IX", "მეათის": "X"}
EPITHET = {"აღმაშენებლის", "აღმაშენებელის", "გორგასლის", "ბრწყინვალის", "დიდის", "ქველის",
           "ლაშა-გიორგის", "იბერის", "მთაწმინდელის", "ათონელის", "საბაწმინდელის", "ხანძთელის"}
SURNAME_SUFFIX_STRONG = ("შვილის", "შვილი", "ძის", "ძეს", "ძე")
SURNAME_SUFFIX_WEAK = ("იანის", "ავას", "უას", "იას", "ელის", "ულის", "ურის", "ონის", "ანის")
SURNAME_SUFFIX_ALL = SURNAME_SUFFIX_STRONG + SURNAME_SUFFIX_WEAK

RU_TYPES = (r"(?:улица|ул\.|переулок|пер\.|тупик|проезд|проход|съезд|въезд|площадь|пл\.|проспект|пр-т|мост|"
            r"подъём|подъем|спуск|шоссе|набережная|аллея|балка|дорога|ряд|тропа|лестница|тоннель|парк|сад|сквер|"
            r"хребет|круг|ущелье|тупик|микрорайон|квартал|массив|посёлок|поселок)")
RU_ORD = r"(?:\d+-?(?:й|я|е|ой|ый|ая)?\.?|[IVX]+)"
EN_TYPES = (r"(street|avenue|lane|dead end|deadend|blind alley|alley|square|embankment|highway|passage|ascent|"
            r"descent|bridge|road|turn|drive|park|garden|st\.?|ave\.?|str\.?)")

KA2LAT = dict(zip("აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ",
                  ["a", "b", "g", "d", "e", "v", "z", "t", "i", "k", "l", "m", "n", "o", "p", "zh", "r", "s", "t",
                   "u", "p", "k", "gh", "q", "sh", "ch", "ts", "dz", "ts", "ch", "kh", "j", "h"]))
KA2RU = dict(zip("აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ",
                 ["а", "б", "г", "д", "е", "в", "з", "т", "и", "к", "л", "м", "н", "о", "п", "ж", "р", "с", "т",
                  "у", "п", "к", "г", "к", "ш", "ч", "ц", "дз", "ц", "ч", "х", "дж", "х"]))


def clean(name: str | None) -> str:
    s = (name or "").replace("–", "-").replace("—", " — ").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def strip_parens(s: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", s or "").strip()


def has_ka(s: str | None) -> bool:
    return bool(KA.search(s or ""))


def stem_gen(tok: str) -> str:
    """Genitive token -> nominative guess (best effort)."""
    t = tok
    if t.endswith("-ის") or t.endswith("-ს"):
        return t.rsplit("-", 1)[0]
    if t.endswith("ძის"):
        return t[:-3] + "ძე"
    if t.endswith("ის") and len(t) > 3:
        return t[:-2] + "ი"
    if t.endswith("ს") and len(t) > 2 and t[-2] in VOW:
        return t[:-1]
    return t


def key_tok(tok: str) -> str:
    """Spelling-variant tolerant token key: drop genitive and final vowel; tolerate syncope."""
    t = tok.replace("–", "-")
    if t.endswith("-ის"):
        t = t[:-3]
    elif t.endswith("ის") and len(t) > 3:
        t = t[:-2]
    elif t.endswith("ს") and len(t) > 2 and t[-2] in VOW:
        t = t[:-1]
    if len(t) > 3 and t[-1] in VOW:
        t = t[:-1]
    return re.sub(r"[აეო]([ლრნ])$", r"\1", t)


KEY_DROP = {"წმინდა", "წმ.", "და"}


def norm_key(s: str) -> str:
    """Order-insensitive token key of a Georgian eponym (genitive or nominative)."""
    s = strip_parens(clean(s)).replace("-", " ")
    toks = [key_tok(t) for t in s.split() if t and t not in KEY_DROP]
    return " ".join(sorted(toks))


def nominative(core: str) -> str:
    """Only the LAST word inflects in Georgian: "ილია ჭავჭავაძის" -> "ილია ჭავჭავაძე"."""
    toks = core.split(" ")
    if not toks or not core:
        return core
    return " ".join(toks[:-1] + [stem_gen(toks[-1])])


def split_ka_name(name: str) -> dict:
    """Split a full Georgian feature name into eponym part (genitive), type and ordinal."""
    s = strip_parens(clean(name))
    out = {"base": "", "type": None, "ordinal": None, "kind": None}
    if not has_ka(s):
        out["kind"] = "non_ka"
        out["base"] = s
        return out
    if " — " in s or re.search(r"[ა-ჿ] - [ა-ჿ]", s):
        out["kind"] = "route"
        out["base"] = s
        return out
    toks = s.split(" ")
    if toks and toks[-1] in TYPES_KA:
        out["type"] = toks[-1]
        toks = toks[:-1]
    if toks and ORD.match(toks[-1]) and (len(toks) > 1 or out["type"]):
        out["ordinal"] = toks[-1]
        toks = toks[:-1]
    if toks and (toks[-1] in TYPE_GEN or (out["type"] and toks[-1] in TYPES_KA)):
        toks = toks[:-1]
        if toks and ORD.match(toks[-1]):
            toks = toks[:-1]
    if "სახელობის" in toks:
        toks = toks[:toks.index("სახელობის")] or toks
    core = [t for t in toks if t not in SETTLEMENT]
    while len(core) > 1 and ORD.match(core[-1]):
        core = core[:-1]
    if not core:
        out["kind"] = "numbered"
        return out
    out["base"] = " ".join(core)
    return out


def translit(s: str, table: dict[str, str] = KA2LAT) -> str:
    return "".join(table.get(c, c) for c in s)


def _dedupe(s: str) -> str:
    return "".join(c for i, c in enumerate(s) if i == 0 or c != s[i - 1])


def skel_lat(s: str) -> str:
    s = (s or "").lower()
    for a, b in (("kh", "k"), ("gh", "g"), ("q", "k"), ("ts", "c"), ("tz", "c"), ("dz", "z"), ("zh", "j"),
                 ("sh", "s"), ("ch", "c"), ("y", "i"), ("w", "v"), ("ph", "p"), ("f", "p"), ("th", "t"), ("h", ""),
                 ("x", "ks")):
        s = s.replace(a, b)
    return _dedupe(re.sub(r"[^bcdgjklmnprstvz]", "", s))


def skel_ru(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    for a, b in (("дз", "з"), ("дж", "ж"), ("тс", "ц"), ("х", "к"), ("г", "к"), ("щ", "ш"), ("ф", "п")):
        s = s.replace(a, b)
    return _dedupe(re.sub(r"[^бвджзклмнпрстцчшк]", "", s))


def ru_base(ru: str | None) -> str:
    """Russian eponym part: drop type words and a leading ordinal (idempotent on a base)."""
    if not ru:
        return ""
    s = re.sub(r"\s+", " ", ru.replace("–", "-")).strip()
    s = re.sub(r"\s*\([^)]*\)", "", s)
    s = re.sub(r"(?i)^" + RU_ORD + r"\s+", "", s)
    s = re.sub(r"(?i)(?<![\w-])" + RU_TYPES + r"(?![\w-])", "", s)
    s = re.sub(r"(?i)\s" + RU_ORD + r"$", "", s.strip())
    return re.sub(r"\s+", " ", s).strip(" ,.")


def en_base(en: str | None) -> str:
    if not en:
        return ""
    s = re.sub(r"\(([^)]*)\)", "", en)
    s = re.sub(r"(?i)\b\d+(st|nd|rd|th)\b", "", s)
    s = re.sub(r"(?i)\b" + EN_TYPES + r"(?=\s|$)", "", s)
    s = re.sub(r"(?i)\s[IVX]+$", "", s.strip())
    s = re.sub(r"'s\b", "", s)
    return re.sub(r"\s+", " ", s).strip(" ,.-'")


def ru_is_adjective(ru_b: str) -> bool:
    """Toponym/concept streets are often adjectives in Russian: 'Лагодехская'."""
    b = ru_base(ru_b)
    if not b or " " in b:
        return False
    return bool(re.search(r"(ская|ский|цкая|цкий|ная|ный|вая|вый|ая|ий|ой|ое|ые)$", b))


def translit_consistent(core: str, ru_b: str, en_b: str) -> bool | None:
    """Is name:ru/en a transliteration (proper noun) rather than a translation (common noun)?"""
    toks = [re.sub(r"(ის|ს)$", "", t) for t in core.split() if has_ka(t)]
    if not toks or (not ru_b and not en_b):
        return None
    ok = 0
    for t in toks:
        t2 = re.sub(r"[აეიოუ]$", "", t)
        sk_ru = skel_ru(translit(t2, KA2RU))[:5]
        sk_en = skel_lat(translit(t2))[:5]
        hit = (ru_b and sk_ru and sk_ru in skel_ru(ru_b)) or (en_b and sk_en and sk_en in skel_lat(en_b))
        ok += bool(hit)
    return ok / len(toks) >= 0.5


def surname_consistent(core_nom: str, ru_b: str, en_b: str) -> bool | None:
    """Does the last Georgian word (transliterated) appear in name:ru / name:en?"""
    if not core_nom or not has_ka(core_nom):
        return None
    last = re.sub(r"[აეიოუ]$", "", core_nom.split(" ")[-1])
    res = []
    if en_b:
        res.append(skel_lat(translit(last))[:6] in skel_lat(en_b))
    if ru_b:
        res.append(skel_ru(translit(last, KA2RU))[:6] in skel_ru(ru_b))
    return any(res) if res else None


def gen_forms(nom: str) -> set[str]:
    """Nominative label -> plausible genitive forms (only the last word inflects) + itself."""
    nom = strip_parens(nom)
    if not nom or not has_ka(nom):
        return set()
    *head, last = nom.split(" ")
    pre = " ".join(head) + (" " if head else "")
    f: set[str] = set()
    if last.endswith("ი"):
        b = last[:-1]
        f.add(b + "ის")
        if len(b) > 3 and b[-2] in "აეო" and b[-1] in "ლრნმ":
            f.add(b[:-2] + b[-1] + "ის")          # syncope ავლაბარი -> ავლაბრის
    elif last.endswith(("ა", "ე")):
        f.add(last + "ს")
        f.add(last[:-1] + "ის")
    elif last.endswith(("ო", "უ")):
        f.add(last + "ს")
    else:
        f.add(last + "ის")
    return {pre + x for x in f} | {nom}


def ka_label_variants(gen: str, aliases: list[str] | None = None) -> list[str]:
    """Nominative label variants of a genitive eponym for exact-label lookups in Wikidata.

    Genitive -ის may come from -ი / -ე / -ა stems; syncope (წერეთლის -> წერეთელი); regnal ordinal
    words -> Roman numerals (ვახტანგ მეექვსის -> ვახტანგ VI); title words dropped.
    """
    gen = strip_parens(clean(gen))
    if not gen or not has_ka(gen):
        return []
    out: list[str] = []
    nom = re.sub(r"-ის$", "", nominative(gen))
    out.append(nom)
    toks_nt = [t for t in nom.split() if t not in TITLE_DROP]
    if toks_nt and " ".join(toks_nt) != nom:
        out.append(" ".join(toks_nt))
    gtoks = [t for t in gen.split() if t not in TITLE_DROP]
    if any(t in REGNAL_ROMAN for t in gtoks):
        out.append(" ".join(REGNAL_ROMAN.get(t, t) for t in gtoks))
    if gtoks and re.match(r"^[IVX]+-ის$", gtoks[-1]):
        out.append(" ".join(gtoks[:-1] + [gtoks[-1][:-3]]))
    toks = gen.split()
    head, last = toks[:-1], toks[-1]
    heads = [head, [t for t in head if t not in TITLE_DROP]]
    lasts: set[str] = set()
    if last.endswith("ის"):
        x = last[:-2]
        lasts.update({x + "ი", x + "ე", x + "ა"})
        if len(x) > 2 and x[-1] in "ლრნმ" and x[-2] not in VOW:
            lasts.update({x[:-1] + "ე" + x[-1] + "ი", x[:-1] + "ა" + x[-1] + "ი"})
    elif last.endswith("ს") and len(last) > 2 and last[-2] in VOW:
        lasts.add(last[:-1])
    else:
        lasts.add(last)
    for h in heads:
        for lst in sorted(lasts):
            out.append(" ".join(h + [lst]))
            for a in aliases or []:
                if len(h) == 1 and len(a.split()) == 1:
                    out.append(a + " " + lst)
    return list(dict.fromkeys(x.strip() for x in out if x and x.strip()))


def label_key(s: str) -> str:
    """Comparable key of a Georgian label (nominative or genitive)."""
    return norm_key(s)


def norm_latin(s: str | None) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[^a-zа-я ]", " ", s.replace("-", " "))
    return " ".join(s.split())


def core_tokens(gen: str) -> list[str]:
    """Tokens of the eponym without titles and regnal numbers (for the single-token guard)."""
    toks = strip_parens(clean(gen)).split()
    return [t for t in toks if t not in TITLES and t not in TITLE_DROP and not re.match(r"^[IVX]+(-ის)?$", t)
            and t not in REGNAL]
