"""Split the extracted encyclopedia lines into entries (articles).

The book has three alphabetical parts, separated by Latin-font section titles:

* street articles (pages ~16-215): headword = optional given names in regular type + surname
  and kind word in bold Mtavruli (``gBM``), then ``, district (place); from ... to ...``;
  short glossary notes in regular Mtavruli (``gM``) follow and explain the name
  (``ატენი, ქალაქი ფეოდ. საქართველოში``); group headings end with ``:`` and are followed by
  sub-entries ``I ქუჩა, იხ. ...``;
* "saxelSecvlili obieqtebi" (renamed objects, pages ~218-247): ``OLD NAME, იხ. CURRENT NAME.``;
* "biografiuli statiebi" (biographies, pages ~250-360): ``SURNAME given names (years), ...``.

Line-end hyphens are resolved with a vocabulary of the whole book: a hyphen is kept only when
the hyphenated form is seen more often inside lines than the joined one.
"""
from __future__ import annotations

import collections
import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable

from pipeline.history.enc_pdf import Line
from pipeline.log import get_logger

log = get_logger(__name__)

BODY_MIN_SIZE, BODY_MAX_SIZE = 8.5, 10.6
SECTION_MARKERS = {
    "saxelSecvlili": "renamed",
    "biografiuli": "bio",
}
ROMAN_RX = r"(?:X{0,3})(?:IX|IV|V?I{0,3})"
_WORD_RX = re.compile(r"[\w„“\-]+", re.U)


@dataclass
class Entry:
    section: str              # street | renamed | bio
    kind: str                 # article | xref | gloss | group | sub | bio
    title: str                # headword as printed (given names + bold part), no trailing comma
    bold: str                 # bold (Mtavruli) part of the headword
    prefix: str               # regular-type given names before the bold part
    qualifier: str            # parenthetical right after the headword: "(ელია)"
    page: int
    body: str = ""            # text after the headword (joined, de-hyphenated)
    parent: int | None = None  # gloss -> index of the article it explains; sub -> group index
    lines: list[str] = field(default_factory=list)

    @property
    def text_hash(self) -> str:
        return hashlib.sha1((self.title + "\n" + self.body).encode("utf-8")).hexdigest()[:16]


def body_lines(lines: Iterable[Line]) -> list[tuple[str, Line]]:
    """Keep body-size lines and tag them with the current section."""
    section = "front"
    out = []
    for ln in lines:
        txt = ln.text.strip()
        if ln.size > 30 and not any("g" in f for _, f in ln.runs):
            for marker, sec in SECTION_MARKERS.items():
                if marker in txt:
                    section = sec
            continue
        if section == "front" and ln.size > 30:
            continue
        if not (BODY_MIN_SIZE <= ln.size <= BODY_MAX_SIZE):
            continue
        if section == "front":
            # street articles start with the first bold headword on a body-size line
            if any("B" in f for _, f in ln.runs) and ln.page >= 10:
                section = "street"
            else:
                continue
        out.append((section, ln))
    return out


# ---------------------------------------------------------------- hyphenation

def build_vocab(lines: Iterable[Line]) -> collections.Counter:
    """Count word forms that occur inside lines (not at the line end)."""
    vocab: collections.Counter = collections.Counter()
    for ln in lines:
        words = _WORD_RX.findall(ln.text)
        for w in words[:-1] if words else []:
            vocab[w.strip("„“").lower()] += 1
    return vocab


def join_hyphen(left: str, right: str, vocab: collections.Counter) -> str:
    """Join ``left`` ending with '-' and ``right`` (next line). Returns the joined string."""
    lw = re.search(r"([\w\-]+)-$", left)
    rw = re.match(r"([\w\-]+)", right)
    if not lw or not rw:
        return left + right
    a, b = lw.group(1), rw.group(1)
    if re.fullmatch(r"\d+|[IVXLC]+", a):
        return left + right            # 70-იან, XIX-ის: hyphen is part of the word
    hyph, merged = f"{a}-{b}".lower(), f"{a}{b}".lower()
    if vocab.get(hyph, 0) > vocab.get(merged, 0):
        return left + right
    return left[:-1] + right


def join_lines(texts: list[str], vocab: collections.Counter) -> str:
    out = ""
    for t in texts:
        t = t.strip()
        if not t:
            continue
        if not out:
            out = t
        elif out.endswith("\xad"):
            out = out[:-1] + t
        elif out.endswith("-") and not out.endswith(" -") and t[:1].isalnum():
            out = join_hyphen(out, t, vocab)
        elif out.endswith("–") or out.endswith("_"):
            out = out + " " + t
        else:
            out = out + " " + t
    out = out.replace("\xad", "")
    out = re.sub(r"\s+", " ", out)
    return out.strip()


# ---------------------------------------------------------------- headings

_PREFIX_RX = re.compile(r"^[\w\-]+(?: \([\w\- ]+\))?(?: [\w\-]+(?: \([\w\- ]+\))?){0,3} $", re.U)


def _heading_split(runs: list[list[str]], section: str) -> tuple[str, str, str, int] | None:
    """If the line starts a headword, return (prefix, bold, kind, index of first non-title run)."""
    if not runs:
        return None
    first_b = next((i for i, (_, f) in enumerate(runs) if "B" in f), None)
    if first_b is not None:
        prefix = "".join(t for t, _ in runs[:first_b])
        if prefix and not _PREFIX_RX.match(prefix):
            first_b = None
        else:
            j = first_b
            while j < len(runs) and ("B" in runs[j][1] or (
                    runs[j][0].strip() in {"-", "–", ""} and j + 1 < len(runs) and "B" in runs[j + 1][1])):
                j += 1
            bold = "".join(t for t, _ in runs[first_b:j])
            if len(bold.strip(" .,:")) < 2 or bold.strip() in {".", ",", "-"}:
                return None
            rest = "".join(t for t, _ in runs[j:])
            if bold.rstrip().endswith(":"):
                return prefix, bold, "group", j
            if bold.rstrip().endswith(".") and not rest:
                return None          # bold caption line ("ვაკის პარკი.")
            if not rest or rest.lstrip()[:1] in {",", "(", ";"} or bold.rstrip().endswith(","):
                return prefix, bold, "head", j
            if section == "bio" and not prefix:
                return prefix, bold, "head", j   # "აბაშიძე გრიგოლ გრიგოლის ძე (1914–94), ..."
            return None
    # glossary note: regular Mtavruli headword at line start
    t0, f0 = runs[0]
    if "M" in f0 and "B" not in f0 and len(t0.strip(" ,.")) >= 2 and not re.match(r"^\w\.", t0):
        j = 0
        while j < len(runs) and ("M" in runs[j][1] or re.fullmatch(r"[\d\-– ]+", runs[j][0])):
            if j > 0 and runs[j][0].lstrip().startswith(","):
                break
            j += 1
        title = "".join(t for t, _ in runs[:j])
        rest = "".join(t for t, _ in runs[j:])
        if title.rstrip().endswith(",") or rest.lstrip()[:1] in {",", "("}:
            return "", title, "gloss", j
    return None


def _clean_title(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s.rstrip(" ,;:")


def split_entries(lines: list[Line]) -> list[Entry]:
    """Group lines into entries."""
    from pipeline.history.enc_pdf import merge_same_baseline

    tagged = body_lines(merge_same_baseline(lines))
    vocab = build_vocab(ln for _, ln in tagged)
    entries: list[Entry] = []
    cur: Entry | None = None
    prev_text_end = "."
    group: Entry | None = None
    last_article: int | None = None
    for section, ln in tagged:
        runs = ln.runs
        can_start = cur is None or section != cur.section or prev_text_end.endswith((".", ":", "“", ")", ";"))
        h = _heading_split(runs, section) if can_start else None
        if h is not None:
            prefix, bold, hkind, j = h
            rest = "".join(t for t, _ in runs[j:])
            qualifier = ""
            mq = re.match(r"\s*(\([^)]*\))\s*,?", rest)
            if hkind in ("head", "group") and mq:
                qualifier = mq.group(1)
            if section == "bio":
                kind = "bio"
            elif hkind == "group":
                kind = "group"
            elif hkind == "gloss":
                kind = "gloss"
            else:
                kind = "article"
            title = _clean_title(prefix + bold)
            # sub-entries of a group heading: "I ქუჩა, იხ. ..."
            parent = None
            if kind == "article" and group is not None and not prefix \
                    and re.match(r"^(?:[IVX]+|\d+(?:-ე)?)\s", title):
                kind = "sub"
                parent = entries.index(group)
            elif kind != "gloss":
                group = None
            if kind == "group":
                group = None
            e = Entry(section=section, kind=kind, title=title, bold=_clean_title(bold), prefix=prefix.strip(),
                      qualifier=qualifier, page=ln.page, parent=parent)
            e.lines.append(rest)
            if kind == "gloss":
                e.parent = last_article
            entries.append(e)
            if kind == "group":
                group = e
            if kind in ("article", "sub"):
                last_article = len(entries) - 1
            cur = e
        elif cur is not None:
            cur.lines.append(ln.text)
        prev_text_end = ln.text.rstrip()
    for e in entries:
        body = join_lines(e.lines, vocab)
        e.body = re.sub(r"^\s*(\([^)]*\))?\s*,\s*", "", body) if not e.qualifier else \
            re.sub(r"^\s*\([^)]*\)\s*,?\s*", "", body)
        e.body = e.body.strip()
        # multi-line bold titles: the continuation stays in body; nothing to do here
    _finish_groups(entries)
    _classify_xrefs(entries)
    counts = collections.Counter((e.section, e.kind) for e in entries)
    log.info("enc2008 split: %d entries %s", len(entries), dict(counts))
    return entries


def _finish_groups(entries: list[Entry]) -> None:
    """Expand sub-entry titles with the group stem: 'ნავთლუღის I დასახლების' + 'I ქუჩა'."""
    for e in entries:
        if e.kind != "sub" or e.parent is None:
            continue
        g = entries[e.parent]
        stem = g.title.rstrip(":").strip()
        stem = re.sub(r"\s+(ყოფილი\s+)?\S+ები$", "", stem) if stem.endswith("ები") else stem
        stem = re.sub(r"\s+ყოფილი$", "", stem)
        e.title = f"{stem} {e.title}".strip()
        if "ყოფილი" in g.title:
            e.qualifier = (e.qualifier + " ყოფილი").strip()


def _classify_xrefs(entries: list[Entry]) -> None:
    for e in entries:
        if e.kind in ("article", "sub") and re.match(r"^იხ\.\s", e.body):
            e.kind = "xref" if e.kind == "article" else "sub_xref"
        if e.section == "renamed" and e.kind == "article":
            e.kind = "xref"
