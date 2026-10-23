"""Georgian text helpers shared by the encyclopedia and ka.wikipedia parsers.

Kind words with their case forms, abbreviations used by the 2008 encyclopedia, time
expressions ("1923", "1959-იდან", "XX ს. 30-იან წლებში", "XIX ს. II ნახევარში") and the
2008 district names.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# nominative -> (kind code, genitive, ablative, terminative, dative)
KIND_FORMS: dict[str, tuple[str, str, str, str, str]] = {
    "ქუჩა": ("street", "ქუჩის", "ქუჩიდან", "ქუჩამდე", "ქუჩას"),
    "გამზირი": ("avenue", "გამზირის", "გამზირიდან", "გამზირამდე", "გამზირს"),
    "პროსპექტი": ("avenue", "პროსპექტის", "პროსპექტიდან", "პროსპექტამდე", "პროსპექტს"),
    "გზატკეცილი": ("highway", "გზატკეცილის", "გზატკეცილიდან", "გზატკეცილამდე", "გზატკეცილს"),
    "გზა": ("road", "გზის", "გზიდან", "გზამდე", "გზას"),
    "სანაპირო": ("embankment", "სანაპიროს", "სანაპიროდან", "სანაპირომდე", "სანაპიროს"),
    "აღმართი": ("ascent", "აღმართის", "აღმართიდან", "აღმართამდე", "აღმართს"),
    "დაღმართი": ("descent", "დაღმართის", "დაღმართიდან", "დაღმართამდე", "დაღმართს"),
    "ხეივანი": ("alley", "ხეივნის", "ხეივნიდან", "ხეივნამდე", "ხეივანს"),
    "რიგი": ("row", "რიგის", "რიგიდან", "რიგამდე", "რიგს"),
    "შესახვევი": ("lane", "შესახვევის", "შესახვევიდან", "შესახვევამდე", "შესახვევს"),
    "ჩიხი": ("dead_end", "ჩიხის", "ჩიხიდან", "ჩიხამდე", "ჩიხს"),
    "გასასვლელი": ("passage", "გასასვლელის", "გასასვლელიდან", "გასასვლელამდე", "გასასვლელს"),
    "მოედანი": ("square", "მოედნის", "მოედნიდან", "მოედნამდე", "მოედანს"),
    "ხიდი": ("bridge", "ხიდის", "ხიდიდან", "ხიდამდე", "ხიდს"),
    "გვირაბი": ("tunnel", "გვირაბის", "გვირაბიდან", "გვირაბამდე", "გვირაბს"),
    "ხევი": ("ravine", "ხევის", "ხევიდან", "ხევამდე", "ხევს"),
    "სკვერი": ("garden", "სკვერის", "სკვერიდან", "სკვერამდე", "სკვერს"),
    "ბაღი": ("garden", "ბაღის", "ბაღიდან", "ბაღამდე", "ბაღს"),
    "პარკი": ("park", "პარკის", "პარკიდან", "პარკამდე", "პარკს"),
    "დასახლება": ("neighbourhood", "დასახლების", "დასახლებიდან", "დასახლებამდე", "დასახლებას"),
    "უბანი": ("neighbourhood", "უბნის", "უბნიდან", "უბნამდე", "უბანს"),
    "მიკრორაიონი": ("neighbourhood", "მიკრორაიონის", "მიკრორაიონიდან", "მიკრორაიონამდე", "მიკრორაიონს"),
    "კვარტალი": ("neighbourhood", "კვარტალის", "კვარტალიდან", "კვარტალამდე", "კვარტალს"),
    "ბაზარი": ("other", "ბაზრის", "ბაზრიდან", "ბაზრამდე", "ბაზარს"),
    "ბულვარი": ("alley", "ბულვარის", "ბულვარიდან", "ბულვარამდე", "ბულვარს"),
}
KIND_ABBR = {"შეს.": "შესახვევი", "გას.": "გასასვლელი", "ქ.": "ქუჩა", "გამზ.": "გამზირი",
             "მოედ.": "მოედანი", "ჩიხ.": "ჩიხი"}
NOMINATIVE = set(KIND_FORMS)
GENITIVE = {v[1]: k for k, v in KIND_FORMS.items()}
ABLATIVE = {v[2]: k for k, v in KIND_FORMS.items()}
TERMINATIVE = {v[3]: k for k, v in KIND_FORMS.items()}
DATIVE = {v[4]: k for k, v in KIND_FORMS.items() if v[4] != v[1]}
KIND_CODE = {k: v[0] for k, v in KIND_FORMS.items()}
# adverbial case: "მოღნინის ქუჩად იწოდება" (was called ... street)
ADVERBIAL = {(v[1][:-2] + "ად" if v[1].endswith("ის") else v[1][:-1] + "დ"): k for k, v in KIND_FORMS.items()}

# 2008 districts (as written in the encyclopedia) -> today's districts (stems, for matching)
DISTRICTS_2008 = {
    "ძველი თბილისის": ("მთაწმინდ", "კრწანის", "ჩუღურეთ", "ისნ", "ძველი თბილის"),
    "ვაკე-საბურთალოს": ("ვაკ", "საბურთალ"),
    "დიდუბე-ჩუღურეთის": ("დიდუბ", "ჩუღურეთ"),
    "გლდანი-ნაძალადევის": ("გლდან", "ნაძალადევ"),
    "ისანი-სამგორის": ("ისნ", "ისან", "სამგორ"),
    "დიდგორის": ("დიდგორ", "მთაწმინდ", "ვაკ", "საბურთალ"),
    "მთაწმინდის": ("მთაწმინდ",),
    "კრწანისის": ("კრწანის",),
    "ვაკის": ("ვაკ",),
    "საბურთალოს": ("საბურთალ",),
    "დიდუბის": ("დიდუბ",),
    "ჩუღურეთის": ("ჩუღურეთ",),
    "გლდანის": ("გლდან",),
    "ნაძალადევის": ("ნაძალადევ",),
    "ისნის": ("ისნ", "ისან"),
    "სამგორის": ("სამგორ",),
}

ROMAN_CENTURY = {"XV": 15, "XVI": 16, "XVII": 17, "XVIII": 18, "XIX": 19, "XX": 20, "XXI": 21}
ROMAN_RE = re.compile(r"^[IVXLC]+$")
INITIAL_RE = re.compile(r"^[ა-ჰ]\.$")


def kind_of(word: str) -> str | None:
    """Return the nominative kind word for any case form / abbreviation of it."""
    w = word.strip()
    if w in NOMINATIVE:
        return w
    if w in KIND_ABBR:
        return KIND_ABBR[w]
    return GENITIVE.get(w) or ABLATIVE.get(w) or TERMINATIVE.get(w) or DATIVE.get(w) or ADVERBIAL.get(w)


def to_nominative_phrase(phrase: str) -> str:
    """'ილია ჭავჭავაძის გამზირიდან' -> 'ილია ჭავჭავაძის გამზირი' (only the kind word changes)."""
    words = phrase.split()
    if not words:
        return phrase
    last = words[-1]
    for table in (ABLATIVE, TERMINATIVE, DATIVE, GENITIVE):
        if last in table:
            words[-1] = table[last]
            return " ".join(words)
    if last in KIND_ABBR:
        words[-1] = KIND_ABBR[last]
    return " ".join(words)


# ------------------------------------------------------------------------- time expressions

@dataclass
class TimeExpr:
    year: int
    rel: str          # at | from | until
    prec: str         # year | decade | century
    start: int        # char offsets in the clause
    end: int
    year_to: int | None = None   # "1925–26"
    explicit_century: bool = True

    def as_dict(self) -> dict:
        d = {"year": self.year, "rel": self.rel, "prec": self.prec}
        if self.year_to:
            d["year_to"] = self.year_to
        return d


_YEAR_RE = re.compile(
    r"(?<![\d#№])(1[5-9]\d\d|20[0-2]\d)(?:\s*[–\-]\s*(\d{2,4})(?![\d]))?"
    r"(?:\s*(?:წწ?\.|წლ(?:ის|ებ\w*)|წელს|წელი))?"
    r"(?:-?(იდან|დან|მდე|ში)|\s+(წლიდან|წლამდე|წლიდანვე))?"
)
_DECADE_RE = re.compile(
    r"(?:\b(XV|XVI|XVII|XVIII|XIX|XX|XXI)\s*(?:ს\.|საუკუნის)\s*)?(?<!\d)([1-9]0)(?:\s*[–\-]\s*[1-9]0)?-?"
    r"(?:იან|იანი|იანებ)\w*"
    r"(?:\s*წლ(?:ებ|ები)(ში|ამდე|იდან|ის|ს)?)?"
)
_CENTURY_RE = re.compile(
    r"\b(XV|XVI|XVII|XVIII|XIX|XX|XXI)\s*(?:ს(?:ს)?(?:\.|-ში|-ის|-მდე|-იდან|-ში\.)|საუკუნ(?:ის|ეში|ემდე|იდან|ე)\b)"
    r"(?:\s*(I|II)\s+ნახევარ\w*|\s*(დასაწყის\w*|ბოლო\w*|მიწურულ\w*|დასასრულ\w*|შუა\s+\w+|შუა\s+ხან\w*))?"
)


def find_times(text: str, default_century: int = 20) -> list[TimeExpr]:
    """Find time expressions in ``text`` (years, decades, centuries), non-overlapping, in order."""
    out: list[TimeExpr] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    for m in _YEAR_RE.finditer(text):
        y = int(m.group(1))
        if y < 1500 or y > 2008 + 20:
            continue
        suf = m.group(3) or m.group(4) or ""
        rel = "from" if suf in ("იდან", "დან", "წლიდან", "წლიდანვე") else "until" if suf in ("მდე", "წლამდე") else "at"
        y2 = None
        if m.group(2):
            t = m.group(2)
            y2 = int(t) if len(t) == 4 else int(str(y)[: 4 - len(t)] + t)
            if y2 < y:
                y2 = None
        out.append(TimeExpr(y, rel, "year", m.start(), m.end(), y2))
        taken.append((m.start(), m.end()))
    for m in _DECADE_RE.finditer(text):
        if not free(m.start(), m.end()):
            continue
        cent = ROMAN_CENTURY.get(m.group(1) or "", default_century)
        y = (cent - 1) * 100 + int(m.group(2))
        suf = m.group(3) or ""
        rel = "from" if suf == "იდან" else "until" if suf == "ამდე" else "at"
        out.append(TimeExpr(y, rel, "decade", m.start(), m.end(), explicit_century=bool(m.group(1))))
        taken.append((m.start(), m.end()))
    for m in _CENTURY_RE.finditer(text):
        if not free(m.start(), m.end()):
            continue
        cent = ROMAN_CENTURY[m.group(1)]
        base = (cent - 1) * 100
        half, qual = m.group(2), (m.group(3) or "")
        if half == "I":
            y = base + 25
        elif half == "II":
            y = base + 75
        elif qual.startswith("დასაწყის"):
            y = base + 5
        elif qual.startswith(("ბოლო", "მიწურულ", "დასასრულ")):
            y = base + 95
        elif qual.startswith("შუა"):
            y = base + 50
        else:
            y = base + 50
        g0 = m.group(0)
        rel = "until" if ("-მდე" in g0 or "ემდე" in g0) else "from" if ("-იდან" in g0 or "იდან" in g0) else "at"
        out.append(TimeExpr(y, rel, "century", m.start(), m.end()))
        taken.append((m.start(), m.end()))
    out.sort(key=lambda t: t.start)
    return out


def last_century(text: str) -> int | None:
    """The last explicit century mentioned in ``text`` (for bare decades like '50-იან წლებში')."""
    found = re.findall(r"\b(XV|XVI|XVII|XVIII|XIX|XX|XXI)\s*ს", text)   # "ს." and "საუკუნე"
    return ROMAN_CENTURY[found[-1]] if found else None
