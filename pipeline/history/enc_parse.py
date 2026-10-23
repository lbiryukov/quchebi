"""Rule-based fact extraction from encyclopedia (and ka.wikipedia) street articles.

The articles are formulaic:

    ატენის ქუჩა, ვაკე-საბურთალოს რ-ნი (ვაკე); ილია ჭავჭავაძის გამზირიდან ვერის ხეობამდე.
    წარმოიქმნა XX ს. 20-იან წლებში. ... თბილისის 1934 წ. გეგმაზე ... დატანილია როგორც ატენის
    შესახვევი, შემდეგ ... ხომასურიძის (1856–1948) სახელი დაერქვა, 1959-იდან – ისევ ატენის,
    1996 გ. აბაშიძის სახელი ეწოდა. 2001 დაუბრუნდა ძვ. სახელი. ... აქვს 1 შეს., 2 ჩიხი.

We split the text into sentences and comma clauses, detect time expressions and name
mentions (``როგორც X ქუჩა``, ``X ქუჩა ერქვა``, ``X-ის სახელი ეწოდა``, list items
``1923 – ს. მგალობლიშვილის,``), build a chronological chain of names and derive former
names with from/to years. Only facts are returned (names, years, district, counts); the
article text itself is never stored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pipeline.history import georgian as G

# ----------------------------------------------------------------------------- vocabulary
NAMING_VERBS = ("დაერქვა", "ეწოდა", "შეერქვა", "დაარქვეს", "უწოდეს", "მიენიჭა", "მიეკუთვნა",
                "ეწოდება", "მიიღო", "დაუბრუნდა", "დაუბრუნეს", "ეწოდა.", "შეეცვალა", "გადაერქვა")
WAS_VERBS = ("ერქვა", "ეწოდებოდა", "ერქვას", "იწოდებოდა", "ცნობილი იყო", "იყო")
PRESENT_VERBS = ("ჰქვია", "ეწოდება", "ატარებს")
ATTEST_VERBS = ("შეტანილია", "დატანილია", "აღნიშნულია", "მოხსენიებულია", "მონიშნულია", "გვხვდება",
                "ასევეა", "ასეა", "ფიგურირებს", "იხსენიება", "აღნიშნული", "დატანილი", "იწოდება")
STOP = {
    "როგორც", "ადრე", "შემდეგ", "შემდგომ", "შემდგომში", "ერქვა", "ეწოდა", "დაერქვა", "დაარქვეს",
    "ჰქვია", "ეწოდებოდა", "შეერქვა", "მიენიჭა", "მიეკუთვნა", "უწოდეს", "ისევ", "კვლავ", "კი",
    "ხოლო", "და", "ან", "ანუ", "თავდაპირველად", "ერთხანს", "მანამდე", "ამჟამად", "ყოფ.", "ყოფილი",
    "ახლანდ.", "ახლანდელი", "ადრინდ.", "ადრინდელი", "ადრინდელ", "ყოფილ", "ახლანდელ", "ძვ.",
    "ერთი", "ორი", "სამი", "ზოგი", "მთელი", "საუკუნის", "საუკუნეში", "წლიდან", "წლამდე",
    "სახელია", "სახელწოდებაა", "პირვანდელი", "თავდაპირველი", "ძველი სახელი", "ეწოდებოდა.",
    "მოდის", "დროს", "გადაიქცა", "იქცა", "მიმავალი", "აგებული", "უძველესი", "ცნობილი", "მთავარი",
    "აქვეა", "აქაა", "არის", "დგას", "გადის", "მდებარეობს", "მდებარეობდა", "იწყება", "იწყებოდა",
    "ქუჩაზეა", "ქუჩაზე", "აქვს", "ჰქონდა", "მეორენაირად", "იგივე", "ანუ", "თვით", "ამავე", "იქვე", "უსახელო", "მას", "მის", "მისი",
    "გეგმაზე", "გეგმაზეც", "სიაში", "ცნობარში", "წიგნში", "დატანილია", "შეტანილია", "აღნიშნულია",
    "მონიშნულია", "წ.", "წწ.", "წლის", "წელს", "წლებში", "ს.", "ს-ში", "ნახევარში", "ბოლოს",
    "დასაწყისში", "მიწურულს", "დასასრულს", "იყო", "გახდა", "მონაკვეთი", "მონაკვეთს", "ნაწილი",
    "ნაწილს", "ეწოდება", "გადაერქვა", "დაუბრუნდა", "–", "-", "—", "იგი", "ეს", "ამ", "რომ", "რაც",
    "აქ", "თუ", "ზოგ", "ზოგჯერ", "სახელით", "სახელწოდებით", "სახელი", "სახელწოდება", "აგრეთვე",
    "ასევე", "ასევეა", "ასეა", "კიდევ", "მერე", "იწოდებოდა", "ცნობილი", "ჩამოყალიბდა", "გაჩნდა",
    "წარმოიქმნა", "ქუჩად", "ერთ", "მეორე", "მეორეს", "პირველ", "შეიცვალა", "მიიღო", "ჰქონდა",
    "მოიცავდა", "რომელსაც", "რ-საც", "რ-იც", "რომელიც", "გასწვრივ", "მიმდებარე", "ახალ", "ორი",
    "ქუჩები", "ქუჩებს", "შესახვევები", "ქუჩების", "შემოიერთა", "მიუერთდა", "გაერთიანდა", "ტერიტორიაზე",
    "მიენიჭათ", "ეწოდათ", "დაერქვათ", "ერქვათ", "XX", "XIX", "XVIII", "XXI",
}
COMPLEX_MARKERS = ("მონაკვეთ", "ნაწილ", "გაერთიან", "ორი ქუჩის", "პარალელურ", "გაგრძელებ", "შემოუერთდა",
                   "მიუერთდა", "შეუერთდა", "გაიყო", "ორ ნაწილად", "დაიშალა")

_ABBR_GENERIC = {"წ", "წწ", "ს", "სს", "ქ", "შეს", "გას", "ყოფ", "ძვ", "მდ", "სოფ", "ახლანდ", "ინჟ",
                 "არქიტ", "ქართ", "რუს", "სომხ", "გერმ", "დას", "აღმ", "სამხრ", "ჩრდ", "მარჯვ", "მარცხ",
                 "სახ", "საზ", "ადრინდ", "გვიანდ", "საცხოვრ", "სასწ", "სამეცნ", "ადმ", "კულტ",
                 "ფეოდ", "საქართვ", "ტრად", "ისტ", "ევრ", "მ", "შ", "იხ", "ე", "წმ", "გენ", "პოლკ",
                 "საავტ", "სამხ", "ამხ", "ცენტრ", "ნამდვ", "ხელმძღვ", "სსრ", "მთ", "დამს", "აკად", "მეცნ",
                 "საერთაშ", "ლიტ", "ტექ", "პარტ", "საბჭ", "სატრანსპ", "კვ", "ქ-ის", "ა"}


#: Given names that may precede a genitive surname ("გრიგოლ ხანძთელის სახელი"); extended at
#: run time with the given names of the encyclopedia's biographies (register_given_names).
GIVEN_NAMES: set[str] = {
    "ილია", "გიორგი", "ნიკოლოზ", "ნიკო", "ვასილ", "ვასო", "დავით", "ალექსანდრე", "მიხეილ", "ივანე",
    "შოთა", "აკაკი", "ვაჟა", "გრიგოლ", "კონსტანტინე", "კოტე", "იაკობ", "სერგო", "სერგი", "ლადო",
    "ვლადიმერ", "პეტრე", "პავლე", "სიმონ", "სოლომონ", "ზაქარია", "ნინო", "თამარ", "მარიამ", "ელენე",
    "ანდრია", "ირაკლი", "ერეკლე", "ვახტანგ", "ბესარიონ", "გალაკტიონ", "ტიციან", "პაოლო", "ლევან",
    "ზურაბ", "მერაბ", "ზვიად", "ოთარ", "რევაზ", "გურამ", "თენგიზ", "ჯანსუღ", "ნოდარ", "ავთანდილ",
    "იოსებ", "ალექსი", "დიმიტრი", "ეგნატე", "ფილიპე", "სტეფანე", "ანტონ", "იოანე", "თეიმურაზ", "არჩილ",
    "ვიქტორ", "ალექსეი", "ფრიდრიხ", "კარლ", "როზა", "ვერა", "ოლღა", "ქეთევან", "ბარბარე", "ეკატერინე",
}


def register_given_names(names: set[str] | list[str]) -> None:
    for n in names:
        n = n.strip()
        if n and re.fullmatch(r"[ა-ჰ]{2,15}", n):
            GIVEN_NAMES.add(n)


# ----------------------------------------------------------------------------- data classes

@dataclass
class NameEvent:
    name: str                  # Georgian name as it should be stored (nominative, expanded)
    etype: str                 # name | was | attested | present | restored
    time: dict | None          # TimeExpr.as_dict()
    pos: int                   # position in the text (for ordering)
    is_current: bool = False
    raw: str = ""
    eponym_gen: str | None = None   # genitive eponym phrase for "X-ის სახელი" events
    sent: int = -1                  # sentence index
    note: str | None = None         # "part" when only a section of the street had this name


@dataclass
class FormerName:
    name_ka: str
    from_year: int | None = None
    to_year: int | None = None
    attested: list[int] = field(default_factory=list)
    prec: str = "year"
    eponym_gen: str | None = None
    note: str | None = None
    confidence: float = 0.8

    def as_dict(self) -> dict:
        d = {"name_ka": self.name_ka}
        for k in ("from_year", "to_year", "eponym_gen", "note"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        if self.attested:
            d["attested"] = sorted(set(self.attested))
        if self.prec != "year":
            d["prec"] = self.prec
        d["confidence"] = round(self.confidence, 2)
        return d


# ----------------------------------------------------------------------------- helpers

_LANE_ITEM = (r"\d+\s+(?:შესახვევი|გასასვლელი|ჩიხი|შეს\.|გას\.|ჩიხ\.|აღმ\.|დაღმ\.|ქუჩა|აღმართი|დაღმართი)"
              r"(?:\s*\([^()]*(?:\([^()]*\)[^()]*)*\))?")
_LANES_RE = re.compile(rf"აქვს\s+{_LANE_ITEM}(?:(?:,\s*|\s+და\s+){_LANE_ITEM})*\.?")

def title_abbrev_regex(bold: str) -> re.Pattern | None:
    """'აფხაზის ქუჩა' -> regex for 'ა. ქ.' / 'ა. ქ-ის' (the article refers to itself this way)."""
    words = [w for w in re.split(r"\s+", bold.strip()) if w]
    if not words:
        return None
    parts = []
    for w in words:
        if G.ROMAN_RE.match(w) or re.fullmatch(r"\d+(-ე)?", w):
            parts.append(re.escape(w))
        else:
            parts.append(re.escape(w[0]))
    if len(parts) == 1:
        return None
    body = r"\.?\s*".join(parts[:-1])
    # last part followed by '.' or '-suffix'
    rx = rf"(?<![\w.])(?:{body}\.?\s*{parts[-1]})(?:\.|-\w+)"
    return re.compile(rx)


def split_sentences(text: str) -> list[tuple[int, str]]:
    """Split on '. ' / '; ' when the word before the dot is not an abbreviation or initial."""
    out: list[tuple[int, str]] = []
    start = 0
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and ch in ".;" and (i + 1 == n or text[i + 1] == " "):
            if ch == ";":
                out.append((start, text[start:i + 1]))
                start = i + 2
            else:
                m = re.search(r"([\w\-]+)$", text[start:i])
                word = m.group(1) if m else ""
                nxt = text[i + 2:i + 3] if i + 2 < n else ""
                is_abbr = (len(word) <= 2 and not word.isdigit()) or word in _ABBR_GENERIC or \
                    bool(re.fullmatch(r"[IVX]+", word))
                if word.isdigit() and len(word) == 4:
                    is_abbr = False
                if not is_abbr or nxt == "":
                    out.append((start, text[start:i + 1]))
                    start = i + 2
        i += 1
    if start < n:
        out.append((start, text[start:]))
    return [(s, t.strip()) for s, t in out if t.strip()]


def strip_parens(s: str) -> tuple[str, list[tuple[int, str]]]:
    """Replace top-level parentheticals by spaces (same length); return them separately."""
    out = list(s)
    groups = []
    depth = 0
    start = 0
    for i, ch in enumerate(s):
        if ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
            if depth == 0:
                groups.append((start, s[start + 1:i]))
                for j in range(start, i + 1):
                    out[j] = " "
    return "".join(out), groups


def _tokens(s: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)) for m in re.finditer(r"„[^“„]{1,30}“|[^\s,;:]+", s)]


def _is_name_word(w: str) -> bool:
    if w in STOP:
        return False
    if G.kind_of(w) and w not in G.GENITIVE and w not in G.NOMINATIVE:
        return False      # "ქუჩას", "ქუჩიდან": a case form of a type word ends the name
    if w in G.NOMINATIVE:
        return False      # "ქუჩა პირველი ჩიხი" -> another name ends here
    if re.fullmatch(r"(1[5-9]\d\d|20[0-2]\d)\S*", w):
        return False
    if re.fullmatch(r"\d0-?იან\S*|\d+-?(იან|იანი)\S*", w):
        return False
    if G.INITIAL_RE.match(w) or re.fullmatch(r"[ა-ჰ]\.[ა-ჰ]\.", w):
        return True
    if G.ROMAN_RE.match(w) or re.fullmatch(r"[IVX]+-(ის|ე)", w):
        return True
    if re.fullmatch(r"\d{1,3}(-ე)?|მე-\d{1,3}(-ე)?", w):
        return True
    if w.startswith("„"):
        return True
    if re.fullmatch(r"[ა-ჰ][ა-ჰ\-]*[ა-ჰ]\.?|[ა-ჰ]", w):
        if w.endswith("."):
            return w[:-1] in ("ქ", "შეს", "გას")
        # case endings that never occur inside a street name (ablative, instrumental, locatives),
        # past-tense verbs and truncated participles
        if re.search(r"(?:დან|ით|მდე|ში|ზე|ზეა|შია|ზეც|შიც|ეა|ად|ულ|ოდა|ებოდა|ოდნენ|ავდა|ილ|ეს|კენ|თვის|"
                     r"ებული|ავალი|მა|ცა)$", w) and not w.endswith("ის"):
            return False
        return True
    return False


def _collect_back(tokens: list[tuple[int, str]], k: int, limit: int = 7) -> list[str]:
    words: list[str] = []
    j = k - 1
    while j >= 0 and len(words) < limit:
        w = tokens[j][1]
        if w == "ს." and words and not (j > 0 and G.ROMAN_RE.match(tokens[j - 1][1])):
            words.insert(0, w)       # initial "ს.", not the century abbreviation
            j -= 1
            continue
        if not _is_name_word(w):
            break
        words.insert(0, w)
        j -= 1
    # leading initials of a different person / dangling genitive titles are kept as is
    return words


def _expand_abbr(name: str) -> str:
    words = name.split()
    if words and words[-1] in G.KIND_ABBR:
        words[-1] = G.KIND_ABBR[words[-1]]
    return " ".join(words)


def _clean_name(words: list[str]) -> str:
    s = " ".join(words)
    s = re.sub(r"\s+", " ", s).strip(" ,.;:–-")
    return s


_MONTH_DAT = re.compile(r"^(?:იანვარ|თებერვალ|მარტ|აპრილ|მაის|ივნის|ივლის|აგვისტო|სექტემბერ|ოქტომბერ|ნოემბერ|"
                        r"დეკემბერ)ს$")


def _genitive_like(w: str) -> bool:
    if _MONTH_DAT.match(w):
        return False
    return bool(G.INITIAL_RE.match(w) or re.search(r"(ის|ს|სა|ისა|ების|თა)$", w) or
                re.fullmatch(r"[IVX]+-ის", w) or w in ("ძმები", "ძმების", "და"))


# ----------------------------------------------------------------------------- core parser

@dataclass
class ArticleFacts:
    title: str
    kind: str | None
    district_2008: str | None = None
    place: str | None = None
    route_from: str | None = None
    route_to: str | None = None
    between: str | None = None
    origin: dict | None = None
    events: list[NameEvent] = field(default_factory=list)
    former: list[FormerName] = field(default_factory=list)
    named_year: dict | None = None
    lanes: dict[str, int] = field(default_factory=dict)
    sub_former: list[dict] = field(default_factory=list)
    see: list[str] = field(default_factory=list)
    eponym_refs: list[str] = field(default_factory=list)
    confidence: float = 0.85
    issues: list[str] = field(default_factory=list)
    has_naming_text: bool = False
    defunct: bool = False

    def as_json(self) -> dict:
        d: dict = {"title": self.title, "kind": self.kind}
        for k in ("district_2008", "place", "route_from", "route_to", "between", "origin", "named_year"):
            v = getattr(self, k)
            if v:
                d[k] = v
        if self.former:
            d["former"] = [f.as_dict() for f in self.former]
        if self.lanes:
            d["lanes"] = self.lanes
        if self.sub_former:
            d["sub_former"] = self.sub_former
        if self.see:
            d["see"] = self.see
        if self.eponym_refs:
            d["eponym_refs"] = self.eponym_refs
        if self.defunct:
            d["defunct"] = True
        d["chain"] = [{"name": e.name, "type": e.etype, **({"time": e.time} if e.time else {}),
                       **({"current": True} if e.is_current else {})} for e in self.events]
        d["confidence"] = round(self.confidence, 2)
        if self.issues:
            d["issues"] = self.issues
        return d


def title_kind_word(title: str) -> str | None:
    words = title.replace("(", " ").replace(")", " ").split()
    for w in reversed(words):
        k = G.kind_of(w)
        if k:
            return k
    return None


def _same_name(a: str, b: str) -> bool:
    """Loose comparison of two Georgian street names (initials vs full given names allowed)."""
    def norm(s: str) -> list[str]:
        s = re.sub(r"\([^)]*\)", " ", s)
        s = s.replace("„", "").replace("“", "")
        return [w for w in s.split() if w not in ("ქ.",)]
    wa, wb = norm(a), norm(b)
    if not wa or not wb:
        return False
    if wa == wb:
        return True
    # compare kind words and the last non-kind word (surname), initials must agree with given names
    ka, kb = G.kind_of(wa[-1]), G.kind_of(wb[-1])
    if ka and kb and ka != kb:
        return False
    ca = [w for w in wa if not G.kind_of(w)]
    cb = [w for w in wb if not G.kind_of(w)]
    if not ca or not cb:
        return False
    def stem(w: str) -> str:
        return re.sub(r"(ისა|ის|ეს|ს|ა|ე|ი)$", "", w) or w
    if stem(ca[-1]) != stem(cb[-1]):
        return False
    ia = [w[0] for w in ca[:-1]]
    ib = [w[0] for w in cb[:-1]]
    if ia and ib and ia[0] != ib[0]:
        return False
    return True


def parse_article(title: str, body: str, bold: str | None = None) -> ArticleFacts:
    """Parse one street article. ``body`` is the text after the headword and the comma."""
    kw = title_kind_word(title)
    facts = ArticleFacts(title=title, kind=G.KIND_CODE.get(kw) if kw else None)
    text = body
    # the article refers to itself by initials: "ა. ქ.", "ა. ქ-ის"
    abbr = title_abbrev_regex(bold or title)
    if abbr is not None:
        text = abbr.sub("⟨CUR⟩", text)
    text = re.sub(r"\bმისი სახელი\b", "⟨CUR⟩ სახელი", text)
    _parse_header(facts, text)
    m_lanes = _LANES_RE.search(text)
    if m_lanes:
        _parse_lanes(facts, m_lanes.group(0), title)
        text = text[:m_lanes.start()] + " " * (m_lanes.end() - m_lanes.start()) + text[m_lanes.end():]
    facts.see = [s.strip(" .") for s in re.findall(r"იხ\.\s+([^.;)]+)", text)]
    facts.has_naming_text = bool(re.search(
        r"(?:^|\s)(?:" + "|".join(NAMING_VERBS + ("ერქვა", "ეწოდებოდა")) + r")(?:\s|$|\.|,)", text)) or bool(
        re.search(r"როგორც\s+(?!(?:" + "|".join(G.NOMINATIVE) + r")\b)\S+\s+\S+", text))
    if re.search(r"აღარ არსებობს|მდებარეობდა|გაუქმდა|აღარ არის", text):
        facts.defunct = True
    events = extract_events(text, kw or "ქუჩა", title)
    facts.events = events
    _build_former(facts, events)
    _score(facts, text)
    return facts


def _parse_header(facts: ArticleFacts, text: str) -> None:
    head = re.split(r"(?<=[;])\s|\.\s", text, maxsplit=2)
    first = head[0] if head else ""
    m = re.match(r"\s*(.{3,60}?)\s+რ-(?:ნი|ნები|ნებში|ნში)\s*(?:\(([^)]*)\))?", first)
    if m:
        d = m.group(1).strip(" ,")
        d = re.sub(r"(ისა|სა)\s+და\s+", lambda x: x.group(1) + " და ", d)
        facts.district_2008 = d
        if m.group(2):
            facts.place = m.group(2).strip()
    # route
    rest = text[m.end():] if m else text
    rest = rest.lstrip(" ;,")
    rest = re.sub(r"\b(ყოფ|ახლანდ|მდ)\.\s", r"\1 ", rest)
    sent = re.split(r"\.\s", rest, maxsplit=1)[0]
    mr = re.search(r"([^;.()]{3,80}?(?:იდან|ოდან|დან))\s+([^;.()]{3,80}?(?:ამდე|მდე))", sent)
    if mr:
        def _clean_route(x: str) -> str:
            x = re.sub(r"^(დასაწყისი|ყოფ|ახლანდ|მდ)\s+", "", x.strip())
            return G.to_nominative_phrase(x)
        facts.route_from = _clean_route(mr.group(1))
        facts.route_to = _clean_route(mr.group(2))
    else:
        mb = re.search(r"([^;.()]{3,120}?)\s+შორის", sent)
        if mb:
            facts.between = mb.group(1).strip()
        else:
            md = re.search(r"დასაწყისი\s+([^;.()]{3,80}?(?:იდან|დან))", sent)
            if md:
                facts.route_from = G.to_nominative_phrase(md.group(1).strip())
    for _, s in split_sentences(text):
        mo = re.search(r"(წარმოიქმნა|გაჩნდა|დაარსდა|გაშენდა|ჩამოყალიბდა|საფუძველი ჩაეყარა|დაგეგმარდა|"
                       r"არსებობს)\s+", s)
        if not mo:
            continue
        tail = s[mo.end():]
        times = G.find_times(tail, G.last_century(tail) or 20)
        if times and times[0].start < 40:
            t = times[0]
            facts.origin = {"text": tail[:t.end].strip()[:60], **t.as_dict()}
        elif re.match(r"\s*შუა საუკუნეებ|\s*ფეოდ", tail):
            facts.origin = {"text": tail[:30].strip(), "prec": "medieval"}
        break


def _parse_lanes(facts: ArticleFacts, s: str, title: str) -> None:
    abbr = {"შეს": "lane", "შესახვევი": "lane", "გას": "passage", "გასასვლელი": "passage",
            "ჩიხი": "dead_end", "ჩიხ": "dead_end", "აღმ": "ascent", "დაღმ": "descent", "ქუჩა": "street"}
    clean, groups = strip_parens(s)
    for n, w in re.findall(r"(\d+)\s+(შესახვევი|გასასვლელი|შეს|გას|ჩიხი|ჩიხ|აღმ|დაღმ|ქუჩა)", clean):
        code = abbr[w]
        facts.lanes[code] = facts.lanes.get(code, 0) + int(n)
    nom_of = {"შეს": "შესახვევი", "შესახვევი": "შესახვევი", "გას": "გასასვლელი", "გასასვლელი": "გასასვლელი",
              "ჩიხი": "ჩიხი", "ჩიხ": "ჩიხი"}
    # a single lane with its old name: "აქვს 1 შეს. (... დატანილია როგორც პირველი მაისის შეს.)"
    single: list[tuple[str, str]] = []
    for m in re.finditer(r"(\d+)\s+(შესახვევი|გასასვლელი|შეს|გას|ჩიხი|ჩიხ)\.?\s*\(([^()]*)\)", s):
        if m.group(1) == "1":
            mm = re.search(r"როგორც\s+([^,;()]+?)\s*$", m.group(3))
            if mm:
                single.append((nom_of[m.group(2)], mm.group(1)))
    # sub-lane former names: "(II ჩიხი – ყოფ. მრუდე ჩიხი, III ჩიხი – ყოფ. სწორი ჩიხი)"
    base = re.sub(r"\s*\([^)]*\)", "", title)
    bw = base.split()
    main_kind = G.kind_of(bw[-1]) if bw else None
    if main_kind:
        bw = bw[:-1]
    base_stem = " ".join(bw)
    if bw and main_kind and G.ROMAN_RE.match(bw[-1]):
        # lanes of "ახალარსენალის I ქუჩა" are "ახალარსენალის I ქუჩის ... შესახვევი"
        base_stem = f"{base_stem} {G.KIND_FORMS[main_kind][1]}"
    for kind_nom, old in single:
        ow = old.split()
        if ow and ow[-1] in G.KIND_ABBR:
            ow[-1] = G.KIND_ABBR[ow[-1]]
        elif ow and ow[-1] in ("შეს", "გას"):
            ow[-1] = G.KIND_ABBR[ow[-1] + "."]
        if ow and G.kind_of(ow[-1]) and len(ow) <= 7:
            facts.sub_former.append({"sub": f"{base_stem} {kind_nom}".strip(), "name_ka": " ".join(ow)})
    for _, g in groups:
        for part in re.split(r"[,;]\s*", g):
            mm = re.match(r"\s*((?:[IVX]+\s+)?(?:შეს|გას|ჩიხი|შესახვევი|გასასვლელი|ჩიხ)\.?)\s*[–-]\s*(.+)", part)
            if not mm:
                continue
            sub = _expand_abbr(mm.group(1).replace("ჩიხ.", "ჩიხი").strip())
            if sub.endswith((" შეს", "შეს")):
                sub = sub[:-3] + "შესახვევი"
            sub = sub.replace("შეს.", "შესახვევი").replace("გას.", "გასასვლელი")
            olds = re.sub(r"^ყოფ\.\s*", "", mm.group(2).strip())
            for old in re.split(r",\s*შემდეგ\s*[–-]?\s*|\s+შემდეგ\s+[–-]\s*", olds):
                old = re.sub(r"^ყოფ\.\s*", "", old.strip(" .)"))
                old = re.sub(r"\s*\(.*$", "", old)
                old_w = old.split()
                if old_w and old_w[-1] in ("შეს", "გას", "ქ"):
                    old_w[-1] += "."
                if not old_w or len(old_w) > 7:
                    continue
                old_w[-1] = G.KIND_ABBR.get(old_w[-1], old_w[-1])
                if not G.kind_of(old_w[-1]) or "1" <= old[:1] <= "9" and len(old_w) < 2:
                    continue
                facts.sub_former.append({"sub": f"{base_stem} {sub}".strip(), "name_ka": " ".join(old_w)})


def extract_events(text: str, kind_word: str, title: str) -> list[NameEvent]:
    """Find name mentions with their time and role, in text order."""
    events: list[NameEvent] = []
    last_year = 0
    for s_idx, (s_off, sent) in enumerate(split_sentences(text)):
        flat, parens = strip_parens(sent)
        c = G.last_century(text[:s_off + len(sent)])
        cent = c or 20
        verb_type = _sentence_verb(flat)
        formerly = bool(re.match(r"\s*(?:ყოფილი|ყოფ\.|ადრე)\s", flat))
        # a parenthetical with its own naming statement is processed as an extra sentence
        for p_off, ptxt in parens:
            if re.search(r"(?:^|\s)(?:ერქვა|ეწოდა|დაერქვა|ეწოდებოდა)(?:\s|$|\.)", ptxt) and \
                    not re.search(r"სახელწ(?:ოდება|\.)\s+(?:უკავშირ|დაკავშირ)", ptxt):
                sub_events = extract_events(ptxt, kind_word, title)
                for se in sub_events:
                    se.pos = s_off + p_off + se.pos / 1000.0
                    se.sent = s_idx
                events.extend(sub_events)
        clauses = []
        off = 0
        for part in re.split(r"(,\s+|;\s+)", flat):
            if part.startswith((",", ";")):
                off += len(part)
                continue
            clauses.append((off, part))
            off += len(part)
        ctypes = [_sentence_verb(cl) for _, cl in clauses]
        sent_events: list[NameEvent] = []
        for ci, (c_off, cl) in enumerate(clauses):
            if not cl.strip():
                continue
            ctype = ctypes[ci]
            if ctype is None:   # list items take the verb of the next clause that has one
                ctype = next((t for t in ctypes[ci + 1:] if t), None) or verb_type
            has_attest = bool(re.search("|".join(ATTEST_VERBS), cl))
            times = G.find_times(cl, cent)
            for t in times:
                if t.prec == "decade" and not t.explicit_century and t.year < last_year - 5 and t.year + 100 <= 2010:
                    t.year += 100
                if t.prec in ("year", "decade"):
                    last_year = max(last_year, t.year)
            found = _names_in_clause(cl, kind_word)
            for npos, name, how, epo, note in found:
                if how == "subj":
                    et = "was"   # the renamed object: its old name ends at the clause's time
                elif how == "as":
                    et = "attested" if (has_attest or ctype in (None, "attested")) else ctype
                elif how == "gen" and re.search(r"სახელით|სახელწოდებით", cl) and has_attest:
                    et = "attested"
                else:
                    et = ctype or ("was" if formerly else "mention")
                tm = _nearest_time(times, npos)
                if et == "was" and tm is not None and tm.rel == "at" and re.search(r"\bადრე\b", cl):
                    tm = None
                tdict = tm.as_dict() if tm else None
                if how == "subj" and tdict is not None and tdict["rel"] == "at":
                    tdict["rel"] = "until"
                ev = NameEvent(name=name, etype=et, time=tdict,
                               pos=s_off + c_off + npos, raw=cl.strip()[:120], eponym_gen=epo, sent=s_idx,
                               note=note)
                ev.is_current = name == "⟨CUR⟩" or _same_name(name, title)
                sent_events.append(ev)
            if not found and re.search(r"დაუბრუნდა|დაუბრუნეს", cl) and re.search(r"ძვ\.|ძველი|ადრინდ", cl):
                tm = times[0] if times else None
                sent_events.append(NameEvent(name="⟨RESTORED⟩", etype="restored", time=tm.as_dict() if tm else None,
                                             pos=s_off + c_off, raw=cl.strip()[:120], sent=s_idx))
        # attestation in parentheses applies to the previous event
        for p_off, ptxt in parens:
            if re.search("|".join(ATTEST_VERBS), ptxt):
                times = G.find_times(ptxt, cent)
                prev = [e for e in sent_events if e.pos <= s_off + p_off]
                if prev and times:
                    tgt = prev[-1]
                    events_attest = NameEvent(name=tgt.name, etype="attested", time=times[0].as_dict(),
                                              pos=tgt.pos + 1, raw=ptxt[:120], is_current=tgt.is_current,
                                              sent=s_idx)
                    sent_events.append(events_attest)
        events.extend(sent_events)
    events.sort(key=lambda e: e.pos)
    # restored: the current name returns
    for e in events:
        if e.name == "⟨RESTORED⟩":
            e.name = "⟨CUR⟩"
            e.is_current = True
            e.etype = "name"
    for e in events:
        if e.name == "⟨CUR⟩":
            e.name = title
            e.is_current = True
    return events


def _sentence_verb(s: str) -> str | None:
    if re.search(r"\b(?:" + "|".join(PRESENT_VERBS) + r")\b", s):
        return "present"
    if re.search(r"(?:^|\s)(?:" + "|".join(NAMING_VERBS) + r")", s):
        return "name"
    if re.search(r"(?:^|\s)(?:" + "|".join(WAS_VERBS) + r")(?:\s|$|\.)", s):
        return "was"
    if re.search("|".join(ATTEST_VERBS), s):
        return "attested"
    return None


def _nearest_time(times: list[G.TimeExpr], pos: int):
    if not times:
        return None
    before = [t for t in times if t.start <= pos]
    if before:
        return before[-1]
    return times[0]


def _names_in_clause(cl: str, kind_word: str) -> list[tuple[int, str, str, str | None, str | None]]:
    """Return [(pos, name, how, eponym_gen, note)] for one clause. how: as | plain | gen | list."""
    toks = _tokens(cl)
    out: list[tuple[int, str, str, str | None, str | None]] = []
    used: set[int] = set()
    words = [w for _, w in toks]
    for k, (pos, w) in enumerate(toks):
        if w.startswith("⟨CUR⟩"):
            out.append((pos, "⟨CUR⟩", "plain", None, None))
            used.add(k)
            continue
        nom = None
        note = None
        subj = False
        nxt = words[k + 1:k + 6]
        if w in G.NOMINATIVE or w in G.KIND_ABBR or (w.endswith(".") and w[:-1] in G.NOMINATIVE):
            nom = G.KIND_ABBR[w] if w in G.KIND_ABBR else G.kind_of(w.rstrip("."))
        elif w in G.GENITIVE and nxt and re.match(r"(მონაკვეთ|ნაწილ)", nxt[0]):
            nom = G.GENITIVE[w]          # "მცხეთის ქუჩის მონაკვეთს ... დაერქვა": part of a street
            note = "part"
            subj = True
        elif w in G.ADVERBIAL and w != "გზად":
            nom = G.ADVERBIAL[w]         # "1867 წ. გეგმაზე მოღნინის ქუჩად იწოდება"
            back_adv = _collect_back(toks, k)
            if back_adv:
                b0 = k - len(back_adv)
                out.append((toks[b0][0], _clean_name(back_adv + [nom]), "as", None, None))
                used.update(range(b0, k + 1))
            continue
        elif w in G.DATIVE:
            # dative subject of renaming: "ყოფ. დარიალის შესახვევს ... დაერქვა"
            if any(x in nxt for x in ("სახელი", "დაერქვა", "ეწოდა", "დაარქვეს")) or _back_has_yof(words, k):
                nom = G.DATIVE[w]
                subj = True
        if nom is None:
            continue
        # "ნავთლუღის I დასახლების V ქუჩა": a kind word inside a longer name
        if note is None and k + 2 < len(toks) and G.ROMAN_RE.match(words[k + 1]) and G.kind_of(words[k + 2]):
            continue
        back = _collect_back(toks, k)
        if not back:
            continue
        if len(back) == 1 and (G.INITIAL_RE.match(back[0]) or back[0].isdigit()):
            continue      # "ა. ქ." handled as ⟨CUR⟩; "2 მოედანი" is a count, not a name
        if all(G.ROMAN_RE.match(x) for x in back):
            continue      # "II შესახვევი": a lane of another street, not a name
        name = _clean_name(back + [nom])
        b0 = k - len(back)
        how = "as" if b0 - 1 >= 0 and words[b0 - 1] == "როგორც" else "subj" if subj else "plain"
        out.append((toks[b0][0], name, how, None, note))
        used.update(range(b0, k + 1))
    # "X-ის სახელი/სახელით"
    for k, (pos, w) in enumerate(toks):
        if (w if w == "სახ." else w.rstrip(".")) not in ("სახელი", "სახელით", "სახელისა", "სახ.", "სახელს",
                                                         "სახელწოდებით") or k in used:
            continue
        back = []
        j = k - 1
        while j >= 0 and len(back) < 5 and j not in used:
            x = words[j]
            if x == "ს." and back and not (j > 0 and G.ROMAN_RE.match(words[j - 1])):
                back.insert(0, x)     # initial "ს.", not "XX ს." (century)
                j -= 1
                continue
            if x in STOP or re.fullmatch(r"(1[5-9]\d\d|20[0-2]\d)\S*", x) or x in ("–", "-") or G.kind_of(x):
                break
            if not (_genitive_like(x) or G.ROMAN_RE.match(x)):
                break
            back.insert(0, x)
            j -= 1
        # "ვასილ ალექსის ძე ხომასურიძის": given name before a patronymic
        if back and j >= 2 and words[j] in ("ძე", "ასული") and _genitive_like(words[j - 1]) \
                and re.fullmatch(r"[ა-ჰ]+", words[j - 2]) and words[j - 2] not in STOP:
            back = [words[j - 2]] + back
            j -= 3
        # "გრიგოლ ხანძთელის სახელი": a known given name stays in the nominative
        if back and j >= 0 and words[j] in GIVEN_NAMES and not G.INITIAL_RE.match(back[0]):
            back = [words[j]] + back
            j -= 1
        if not back:
            continue
        if back[-1].startswith("⟨CUR⟩"):
            out.append((toks[j + 1][0], "⟨CUR⟩", "gen", None, None))
            continue
        gen = _clean_name(back)
        if gen.endswith("ისა"):
            gen = gen[:-1]
        out.append((toks[j + 1][0], f"{gen} {kind_word}", "gen", gen, None))
    # list items in naming sentences: "1923 – ს. მგალობლიშვილის", "შემდეგ – ვ. ულიანოვისა",
    # "XX ს. 70-იან წლებში დაერქვა ე. ახვლედიანის"
    if not out:
        item = _list_item(cl)
        if item is not None:
            pos, gen = item
            out.append((pos, f"{gen} {kind_word}", "list", gen, None))
    if not out and re.search(r"(?:^|\s)სახელი(?:\s|$)", cl) and re.search(
            r"(?:^|\s)(?:დაერქვა|ეწოდა|მიენიჭა|დაარქვეს)(?:\s|$|\.)", cl) and not re.search(r"ის\s+სახელი", cl):
        out.append((0, "⟨CUR⟩", "gen", None, None))   # "სახელი დაერქვა 70-იან წლებში"
    return out


_FILLER = {"შემდეგ", "შემდგომ", "კვლავ", "ისევ", "კი", "ადრე", "–", "-", "—", "დაერქვა", "ეწოდა", "ერქვა",
           "მიენიჭა", "დაარქვეს", "ქუჩას", "მას", "წ.", "წლიდან", "წელს", "ხოლო", "ერთხანს", "მერე",
           # attestation context: "თბილისის 1876 წ. გეგმაზე დატანილია ისარლოვის, 1923 წ. გეგმაზე – ..."
           "თბილისის", "ქალაქის", "გეგმაზე", "გეგმაზეც", "დატანილია", "შეტანილია", "სიაში", "ცნობარში",
           "შედგენილ", "ქუჩების", "გამგეობის", "მიერ", "წლის", "წლების", "ცნობათა", "წიგნში"}
_GEN_TAIL = re.compile(r"((?:[ა-ჰ]\.\s*){0,2}(?:[ა-ჰ][ა-ჰ\-]*\s+)?[ა-ჰ][ა-ჰ\-]*(?:ის|ისა|ს|სა|თა))\.?\s*$")


def _list_item(cl: str) -> tuple[int, str] | None:
    m = _GEN_TAIL.search(cl)
    if not m:
        return None
    prefix = cl[:m.start()]
    for t in G.find_times(prefix):
        prefix = prefix[:t.start] + " " * (t.end - t.start) + prefix[t.end:]
    rest = [w for w in prefix.split() if w]
    if any(w not in _FILLER for w in rest):
        return None
    gen = m.group(1).strip()
    gw = gen.split()
    if len(gw) > 1 and gw[0] in STOP and not G.INITIAL_RE.match(gw[0]):
        gw = gw[1:]
    if not gw or gw[-1] in STOP or G.kind_of(gw[-1]) or re.search(r"(წლის|წლების|გეგმის|ს-ის)$", gw[-1]):
        return None
    if len(gw) == 1 and G.INITIAL_RE.match(gw[0]):
        return None
    gen = " ".join(gw)
    gen = re.sub(r"ისა$", "ის", gen)
    gen = re.sub(r"ასა$", "ას", gen)
    return m.start(1), gen


def _back_has_yof(words: list[str], k: int) -> bool:
    for x in words[max(0, k - 6):k]:
        if x in ("ყოფ.", "ყოფილი", "ადრინდელი", "ადრინდ."):
            return True
    return False


def _build_former(facts: ArticleFacts, events: list[NameEvent]) -> None:
    """Turn the event chain into periods and former names."""
    periods: list[dict] = []
    for e in events:
        if e.etype == "mention" and not e.is_current:
            continue
        t = e.time
        if periods and _same_name(periods[-1]["name"], e.name):
            p = periods[-1]
        else:
            p = {"name": e.name, "current": e.is_current, "from": None, "to": None, "att": [], "prec": "year",
                 "epo": e.eponym_gen, "note": e.note, "until_explicit": False}
            periods.append(p)
        if t is None or t["prec"] == "century":
            continue
        if e.etype == "attested":
            p["att"].append(t["year"])
        elif t["rel"] == "until":
            p["to"] = t["year"]
            p["until_explicit"] = True
        elif e.etype in ("name", "present", "restored") or t["rel"] == "from":
            if p["from"] is None:
                p["from"] = t["year"]
                p["prec"] = t["prec"]
        else:
            p["att"].append(t["year"])
    for i, p in enumerate(periods):
        nxt = periods[i + 1] if i + 1 < len(periods) else None
        if p["to"] is None and nxt is not None and nxt["from"] is not None:
            p["to"] = nxt["from"]
        if p["from"] is None and i > 0 and periods[i - 1]["until_explicit"]:
            p["from"] = periods[i - 1]["to"]
    issues = facts.issues
    last_from = None
    for p in periods:
        f = p["from"]
        if f is not None and last_from is not None and f < last_from - 1:
            issues.append("order")
        if f is not None:
            last_from = f
        if p["from"] is not None and p["to"] is not None and p["to"] < p["from"]:
            p["to"] = None
            issues.append("interval")
    for p in periods:
        if p["current"]:
            continue
        name = p["name"]
        if name.startswith("⟨"):
            continue
        conf = 0.85
        if len(name.split()) > 6:
            conf -= 0.2
        fn = FormerName(name_ka=name, from_year=p["from"], to_year=p["to"], attested=p["att"], prec=p["prec"],
                        eponym_gen=p["epo"], note=p["note"], confidence=conf)
        if any(_same_name(x.name_ka, name) and x.from_year == fn.from_year for x in facts.former):
            continue
        facts.former.append(fn)
    cur = [p for p in periods if p["current"] and p["from"] is not None]
    if cur:
        facts.named_year = {"year": cur[-1]["from"], "prec": cur[-1]["prec"]}


def _score(facts: ArticleFacts, text: str) -> None:
    conf = 0.9
    sents = split_sentences(text)
    covered = {e.sent for e in facts.events}
    missing = 0
    for i, (_, s) in enumerate(sents):
        if i in covered:
            continue
        s2 = re.sub(r"როგორც\s+(?:" + "|".join(G.NOMINATIVE) + r")\b", " ", s)
        if re.search(r"(?:^|\s)(?:" + "|".join(NAMING_VERBS + ("ერქვა", "ეწოდებოდა", "როგორც")) + r")", s2) \
                and not re.search(r"სახელწ(?:ოდება|\.)\s+(?:უკავშირ|დაკავშირ)|სახელწოდებაც|სახელიც", s2):
            missing += 1
    if missing > 0:
        conf -= min(0.4, 0.2 * missing)
        facts.issues.append(f"uncovered:{missing}")
    if any(m in text for m in COMPLEX_MARKERS):
        conf -= 0.15
        facts.issues.append("complex")
    if "order" in facts.issues:
        conf -= 0.2
    if "interval" in facts.issues:
        conf -= 0.1
    if facts.has_naming_text and not facts.former and not facts.named_year:
        conf = min(conf, 0.4)
        facts.issues.append("no_events")
    for f in facts.former:
        f.confidence = round(max(0.3, min(f.confidence, conf + 0.05)), 2)
    facts.confidence = round(max(0.1, conf), 2)


# ----------------------------------------------------------------------------- xref / bio / gloss

def parse_xref(title: str, body: str, qualifier: str = "") -> dict:
    """'OLD NAME (place), იხ. CURRENT 1, CURRENT 2.' -> {"old", "place", "targets"}."""
    m = re.search(r"იხ\.\s+(.+?)\.?$", body.strip())
    targets: list[str] = []
    if m:
        raw = m.group(1)
        raw = re.split(r"\.\s", raw)[0]
        for t in re.split(r",\s+|\s+და\s+(?=[ა-ჰ][^,]*\s(?:ქუჩა|გამზირი|მოედანი|შესახვევი|ჩიხი|ხიდი))", raw):
            t = t.strip(" .")
            if not t:
                continue
            # "X ქუჩის II ჩიხი" -> lane of X: keep both forms (OSM names lanes "X II ჩიხი")
            targets.append(t)
    return {"old": title, "place": qualifier.strip("() ") or None, "targets": targets}


def lane_variants(name: str) -> list[str]:
    """'ანტონ კათალიკოსის ქუჩის II ჩიხი' -> [+ 'ანტონ კათალიკოსის II ჩიხი']."""
    out = [name]
    m = re.match(r"(.+?)\s+(ქუჩის|გამზირის|მოედნის)\s+((?:[IVX]+\s+)?(?:შესახვევი|ჩიხი|გასასვლელი|შესახვევები|ჩიხები))$", name)
    if m:
        out.append(f"{m.group(1)} {m.group(3)}")
    return out


def parse_bio(surname: str, body: str) -> dict:
    """'ბებელი' + 'ავგუსტ (1840–1913), გერმანიისა ...' -> facts (names, years, first occupation)."""
    d: dict = {"surname": surname.strip(" ,")}
    head = body[:240]
    m = re.search(r"\((?:დაიბ\.\s*)?(\d{3,4})(?:/\d{2})?\s*[–-]\s*(\d{2,4})?\)", head)
    if m:
        b = int(m.group(1))
        d["birth"] = b
        if m.group(2):
            t = m.group(2)
            dd = int(t) if len(t) == 4 else int(str(b)[: 4 - len(t)] + t)
            if dd < b:
                dd += 100
            d["death"] = dd
        names = head[:m.start()].strip(" ,")
    else:
        m2 = re.search(r"\(დაიბ\.\s*(\d{4})\)", head)
        if m2:
            d["birth"] = int(m2.group(1))
            names = head[:m2.start()].strip(" ,")
        else:
            names = re.split(r",", head, maxsplit=1)[0]
    names = re.sub(r"\(ნამდვ\.[^)]*\)", "", names).strip()
    pat = re.search(r"\b([ა-ჰ]+(?:ის|ს))\s+(ძე|ასული)\b", names)
    if pat:
        d["patronymic"] = f"{pat.group(1)} {pat.group(2)}"
        names = names[:pat.start()].strip()
    if names and len(names) <= 60:
        d["given"] = names
    if m:
        after = body[m.end():m.end() + 160].lstrip(" ,")
        occ = re.split(r"\.\s|\.$", after, maxsplit=1)[0]
        if len(occ) <= 120:
            d["occupation_ka"] = occ.strip()
    return d


def bio_genitives(surname: str) -> set[str]:
    """Possible genitive forms of a nominative surname (for linking 'ტ. ტაბიძის' to 'ტაბიძე')."""
    s = surname.strip()
    out = {s + "ს"}
    if s.endswith(("ი", "ე")):
        out.add(s[:-1] + "ის")
    if s.endswith(("ა", "ო", "უ")):
        out.add(s + "ს")
    for suf, rep in (("ელი", "ლის"), ("ალი", "ლის"), ("ერი", "რის"), ("ანი", "ნის"), ("ონი", "ნის"),
                     ("ელი", "ელის"), ("ები", "ების")):
        if s.endswith(suf):
            out.add(s[: -len(suf)] + rep)
    if s.endswith("ეები"):
        out.add(s[:-1] + "ის")
    return out


def parse_gloss(title: str, body: str) -> dict:
    first = re.split(r"\.\s|\.$", body.strip(), maxsplit=1)[0]
    return {"name_ka": title.strip(" ,"), "note_ka": first[:100].strip()}
