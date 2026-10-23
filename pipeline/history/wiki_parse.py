"""Facts from ka/ru Wikipedia street articles: infobox fields, former names, "named after".

ka: template ``ინფოდაფა ქუჩა`` (``წინა სახელები``, ``რაიონი``, ``ისტორიული რაიონი``, ``უბანი``,
``მიკრორაიონი``); the lead is usually the 2008 encyclopedia text, so the same event parser
(:mod:`pipeline.history.enc_parse`) extracts former names with years. ``[[X]]ის სახელი ეწოდა``
gives the eponym as a link target (full name).

ru: template ``Улица`` / ``Площадь`` (``прежние названия``), ``названа в честь [[X]]``.
"""
from __future__ import annotations

import hashlib
import re

from pipeline.history import enc_parse as P
from pipeline.log import get_logger

log = get_logger(__name__)

KA_INFOBOX = ("ინფოდაფა ქუჩა", "ინფოდაფა მოედანი", "ინფოდაფა")
RU_INFOBOX = ("Улица", "Площадь", "Проспект", "Карточка улицы")
KA_FIELDS = {"სახელწოდება": "name", "რაიონი": "district", "ისტორიული რაიონი": "historic_district",
             "უბანი": "quarter", "მიკრორაიონი": "microdistrict", "წინა სახელები": "former", "სტატუსი": "status"}
RU_FIELDS = {"название": "name", "оригинальное название": "name_orig", "район": "district",
             "исторический район": "historic_district", "микрорайон": "microdistrict",
             "прежние названия": "former", "протяжённость": "length"}
STOP_SECTIONS = re.compile(r"^==\s*(ლიტერატურა|რესურსები|სქოლიო|იხილეთ|წყარო|ლიტ|შენიშვნ|Литература|Ссылки|"
                           r"Примечания|См\. также|Источники)", re.M)


# ------------------------------------------------------------------------------ wikitext

def _find_template(text: str, names: tuple[str, ...]) -> tuple[int, int] | None:
    for m in re.finditer(r"\{\{\s*([^|{}\n]+)", text):
        nm = m.group(1).strip()
        if any(nm == n or nm.lower() == n.lower() for n in names):
            depth = 0
            i = m.start()
            while i < len(text) - 1:
                if text.startswith("{{", i):
                    depth += 1
                    i += 2
                    continue
                if text.startswith("}}", i):
                    depth -= 1
                    i += 2
                    if depth == 0:
                        return m.start(), i
                    continue
                i += 1
            return m.start(), len(text)
    return None


def _split_params(body: str) -> list[str]:
    parts, depth, cur = [], 0, []
    i = 0
    while i < len(body):
        if body.startswith("{{", i) or body.startswith("[[", i):
            depth += 1
            cur.append(body[i:i + 2])
            i += 2
            continue
        if body.startswith("}}", i) or body.startswith("]]", i):
            depth -= 1
            cur.append(body[i:i + 2])
            i += 2
            continue
        ch = body[i]
        if ch == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


def infobox(text: str, names: tuple[str, ...]) -> dict[str, str]:
    span = _find_template(text, names)
    if span is None:
        return {}
    inner = text[span[0] + 2:span[1] - 2]
    out: dict[str, str] = {}
    for p in _split_params(inner)[1:]:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def strip_markup(s: str, keep_breaks: bool = False) -> str:
    """Wikitext -> plain text (links -> labels, templates/refs/files removed)."""
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<ref[^>/]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    if keep_breaks:
        s = re.sub(r"<br\s*/?>", " ¦ ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    # templates (nested)
    for _ in range(5):
        s2 = re.sub(r"\{\{[^{}]*\}\}", "", s)
        if s2 == s:
            break
        s = s2
    s = re.sub(r"\[\[(?:ფაილი|სურათი|File|Image|Файл|კატეგორია|Категория|Category):[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]",
               "", s, flags=re.I)
    s = re.sub(r"\[\[([^\]|]*)\|([^\]]*)\]\]", r"\2", s)
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", s)
    s = re.sub(r"'{2,}", "", s)
    s = s.replace("&nbsp;", " ").replace("&ndash;", "–").replace("&mdash;", "—").replace("\xad", "")
    s = s.replace("&shy;", "")
    return s


def lead_text(text: str) -> str:
    """Body text before the reference sections, without the infobox, as plain text."""
    span = _find_template(text, KA_INFOBOX + RU_INFOBOX)
    if span:
        text = text[:span[0]] + text[span[1]:]
    m = STOP_SECTIONS.search(text)
    if m:
        text = text[:m.start()]
    text = re.sub(r"^=+[^=\n]+=+\s*$", " ", text, flags=re.M)
    t = strip_markup(text)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def split_names(value: str) -> list[str]:
    """'A, B<br/>C (D)' -> names; parenthetical notes without a kind word are dropped."""
    v = strip_markup(value, keep_breaks=True)
    out: list[str] = []
    depth = 0
    cur = []
    for ch in v:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if depth == 0 and ch in ",;¦\n":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    names: list[str] = []
    for n in out:
        n = re.sub(r"\s+", " ", n).strip(" .,;–—-")
        if not n or n.lower() in ("нет", "არა", "—"):
            continue
        names.append(n)
    return names


# ------------------------------------------------------------------------------ ka

_KA_EPONYM_RX = re.compile(
    r"\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]([ა-ჰ]*)\s+(?:\(\d{4}[^)]*\)\s+)?(?:სახელი|სახელს)\s+"
    r"(?:ეწოდა|დაერქვა|მიენიჭა|ატარებს|უწოდეს|დაარქვეს)")
_KA_EPONYM_RX2 = re.compile(r"\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]\w*\s+(?:პატივსაცემად|სახელობის)")


def parse_ka(title: str, page: dict) -> dict:
    """Facts of one ka.wikipedia article (``page`` = {"text", "qid", "pageid", "categories"})."""
    text = page.get("text", "")
    ib = infobox(text, KA_INFOBOX)
    fields = {}
    for k, v in ib.items():
        key = KA_FIELDS.get(k.strip())
        if key:
            val = re.sub(r"\s+", " ", strip_markup(v)).strip()
            if key == "former":
                fields[key] = split_names(v)
            elif val:
                fields[key] = val
    body = lead_text(text)
    # "'''X''' — ქუჩა თბილისში, ..." -> text after the dash
    m = re.search(r"\s[—–-]\s", body[:200])
    rest = body[m.end():] if m else body
    rest = re.sub(r"^(ქუჩა|გამზირი|მოედანი|შესახვევი|ჩიხი|გასასვლელი|სანაპირო|აღმართი|დაღმართი|ხეივანი|ხიდი|"
                  r"გზატკეცილი|ხევი|ბაღი|სკვერი|პარკი)\s+(თბილისში,?\s*)?", "", rest)
    facts = P.parse_article(title, rest)
    fj = facts.as_json()
    fj.pop("chain", None)
    former = list(fj.get("former", []))
    # infobox former names (no years) merged with the parsed ones
    for nm in fields.get("former", []):
        variants = _ka_variants(nm)
        if not variants:
            continue
        if any(P._same_name(f["name_ka"], variants[0]) for f in former):
            continue
        d = {"name_ka": variants[0], "confidence": 0.8, "infobox": True}
        note = re.search(r"\(([^)]*)\)\s*$", nm)
        if note and not P.title_kind_word(note.group(1)):
            d["note"] = note.group(1)[:40]
        former.append(d)
        for extra in variants[1:]:
            if not any(P._same_name(f["name_ka"], extra) for f in former):
                former.append({"name_ka": extra, "confidence": 0.7, "infobox": True})
    if former:
        fj["former"] = former
    epo = _ka_named_after(text)
    if epo:
        fj["named_after"] = epo
    fj.update({k: v for k, v in fields.items() if k != "former"})
    if page.get("qid"):
        fj["qid"] = page["qid"]
    cats = [c.split(":", 1)[-1] for c in page.get("categories", [])]
    if cats:
        fj["categories"] = cats
    if any("ყოფილი" in c for c in cats):
        fj["defunct"] = True
    return {"source": "kawiki", "title": title, "page": str(page.get("pageid") or ""),
            "text_hash": hashlib.sha1(text.encode("utf-8")).hexdigest()[:16], "entry": "wiki", "facts": fj,
            "needs_llm": False}


def _ka_variants(nm: str) -> list[str]:
    """'ბაღის ქუჩა (ზემო ბაღის ქუჩა)' -> ['ბაღის ქუჩა', 'ზემო ბაღის ქუჩა']; drop '(ერთ მონაკვეთს)'."""
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", nm)
    out = []
    if m:
        main, par = m.group(1).strip(), m.group(2).strip()
        if main:
            out.append(main)
        if P.title_kind_word(par) and len(par.split()) <= 6:
            out.append(par)
    else:
        out.append(nm.strip())
    out = [re.sub(r"\s+", " ", o).strip(" .") for o in out if o and re.search(r"[ა-ჰ]", o)]
    # "პირვანდელი სახელია აფთიაქის ქუჩა" -> "აფთიაქის ქუჩა"
    cleaned = []
    for o in out:
        toks = P._tokens(o)
        if len(toks) > 1 and P.title_kind_word(toks[-1][1]) and toks[-1][1] in P.G.NOMINATIVE:
            back = P._collect_back(toks, len(toks) - 1)
            if back:
                o = " ".join(back + [toks[-1][1]])
        cleaned.append(o)
    out = cleaned
    # bare genitive without a kind word ("ეროსი მანჯგალაძის") keeps as is
    return [o for o in out if len(o.split()) <= 8]


def _ka_named_after(text: str) -> dict | None:
    found = list(_KA_EPONYM_RX.finditer(text)) or list(_KA_EPONYM_RX2.finditer(text))
    if not found:
        return None
    m = found[-1]
    target = re.sub(r"\s*\([^)]*\)$", "", m.group(1).strip())
    if re.match(r"(?:ფაილი|კატეგორია|თბილისი)\b", target):
        return None
    d = {"name_ka": target, "src": "kawiki_link"}
    ym = re.search(r"(\d{4})\s*(?:წელს|წ\.)?[^.]{0,80}$", text[max(0, m.start() - 120):m.start()])
    if ym:
        d["year"] = int(ym.group(1))
    return d


# ------------------------------------------------------------------------------ ru

_RU_YEAR = re.compile(r"(?:(до|с|по)\s+)?(1[6-9]\d\d|20[0-2]\d)(?:\s*[-–—]\s*(1[6-9]\d\d|20[0-2]\d))?\s*(?:г\.|года|год|гг\.)?")
_RU_EPONYM = re.compile(r"(?:назван[аоы]?|получил[аи]?\s+(?:своё|свое|современное)?\s*название)\s+в\s+честь\s+"
                        r"\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]", re.I)


def parse_ru(title: str, page: dict) -> dict:
    text = page.get("text", "")
    ib = infobox(text, RU_INFOBOX)
    fields: dict = {}
    for k, v in ib.items():
        key = RU_FIELDS.get(k.strip().lower())
        if not key:
            continue
        if key == "former":
            fields[key] = v
        else:
            val = re.sub(r"\s+", " ", strip_markup(v)).strip()
            if val:
                fields[key] = val
    former = []
    if fields.get("former"):
        for nm in split_names(fields["former"]):
            d = _ru_name_with_years(nm)
            if d:
                former.append(d)
    fj: dict = {"title": title}
    if former:
        fj["former"] = former
    m = _RU_EPONYM.search(text)
    if m:
        fj["named_after"] = {"name_ru": re.sub(r"\s*\([^)]*\)$", "", m.group(1).strip()), "src": "ruwiki_link"}
    fj.update({k: v for k, v in fields.items() if k != "former"})
    if page.get("qid"):
        fj["qid"] = page["qid"]
    cats = [c.split(":", 1)[-1] for c in page.get("categories", [])]
    if cats:
        fj["categories"] = cats
    return {"source": "ruwiki", "title": title, "page": str(page.get("pageid") or ""),
            "text_hash": hashlib.sha1(text.encode("utf-8")).hexdigest()[:16], "entry": "wiki", "facts": fj,
            "needs_llm": False}


def _ru_name_with_years(nm: str) -> dict | None:
    s = nm
    y_from = y_to = None
    for m in _RU_YEAR.finditer(s):
        pre, y1, y2 = m.group(1), int(m.group(2)), m.group(3)
        if y2:
            y_from, y_to = y1, int(y2)
        elif pre == "до" or pre == "по":
            y_to = y1
        elif pre == "с":
            y_from = y1
        else:
            y_from = y_from or y1
    s = _RU_YEAR.sub(" ", s)
    s = re.sub(r"\((?:\s|частично|ранее|до|с|—|–|-)*\)", " ", s)
    s = re.sub(r"\b(частично|ранее)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .,;:—–-()")
    if not s or not re.search(r"[А-Яа-яЁё]", s):
        return None
    d: dict = {"name_ru": s, "confidence": 0.8}
    if y_from:
        d["from_year"] = y_from
    if y_to:
        d["to_year"] = y_to
    return d
