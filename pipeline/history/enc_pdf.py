"""Text extraction from the 2008 encyclopedia PDF with font information.

pdfminer.six gives every glyph with its font and position. We rebuild each text line:

* phantom spaces: the typesetter left a space glyph at every hyphenation point and then moved
  the next glyph back over it (next.x0 ~= space.x0). Such spaces are dropped; a real space
  advances the pen by roughly its width.
* gaps between glyphs wider than ~0.3 em without a space glyph become a space.
* runs in legacy Georgian fonts are converted AcadNusx -> Unicode, other fonts kept as is.

Output: one JSON object per line ``{"p", "col", "x", "y", "size", "runs": [[text, flags]]}``
where flags contain ``g`` (Georgian font), ``B`` (bold), ``M`` (Mtavruli/capital display font).
The full text stays in ``data/raw/enc2008/`` (copyrighted, never committed or exported).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from pipeline.history import acadnusx
from pipeline.log import get_logger

log = get_logger(__name__)

PHANTOM_SPACE_RATIO = 0.35   # (next.x0 - space.x0) / space.width below this -> phantom space
GAP_SPACE_EM = 0.30          # gap between glyphs (in font sizes) that counts as a space


@dataclass
class Glyph:
    text: str
    x0: float
    x1: float
    width: float
    size: float
    font: str


@dataclass
class Line:
    page: int                 # 1-based PDF page
    col: int                  # 0 = left column, 1 = right column
    x: float
    y: float                  # baseline-ish (y0), PDF coordinates (bottom-up)
    size: float
    runs: list[list[str]] = field(default_factory=list)   # [[text, flags], ...]

    @property
    def text(self) -> str:
        return "".join(r[0] for r in self.runs)

    def to_json(self) -> dict:
        return {"p": self.page, "col": self.col, "x": round(self.x, 1), "y": round(self.y, 1),
                "size": round(self.size, 1), "runs": self.runs}

    @classmethod
    def from_json(cls, d: dict) -> "Line":
        return cls(page=d["p"], col=d["col"], x=d["x"], y=d["y"], size=d["size"], runs=d["runs"])


def font_flags(fontname: str) -> str:
    f = ""
    if acadnusx.is_georgian_font(fontname):
        f += "g"
    if acadnusx.is_bold_font(fontname):
        f += "B"
    if acadnusx.is_mtavruli_font(fontname):
        f += "M"
    return f


def assemble(glyphs: list[Glyph]) -> list[list[str]]:
    """Rebuild a line from glyphs: drop phantom spaces, add gap spaces, group by font flags.

    Returns runs ``[[converted_text, flags], ...]``; spaces carry the flags of the preceding run.
    """
    runs: list[list[str]] = []

    def emit(ch: str, flags: str) -> None:
        if ch == " ":
            if not runs or runs[-1][0].endswith(" "):
                return
            runs[-1][0] += " "
            return
        if runs and runs[-1][1] == flags:
            runs[-1][0] += ch
        else:
            runs.append([ch, flags])

    prev: Glyph | None = None
    n = len(glyphs)
    for i, g in enumerate(glyphs):
        flags = font_flags(g.font)
        if g.text == "\xad":
            continue  # soft hyphen: invisible unless at a line end
        if g.text == " " or g.text == "\xa0":
            nxt = next((h for h in glyphs[i + 1:] if h.text not in (" ", "\xa0")), None)
            if nxt is None:
                continue  # trailing space
            w = g.width if g.width > 0.1 else 0.25 * g.size
            if (nxt.x0 - g.x0) / w < PHANTOM_SPACE_RATIO:
                continue  # hyphenation point, not a word break
            emit(" ", flags)
            prev = None
            continue
        if prev is not None and g.x0 - prev.x1 > GAP_SPACE_EM * max(g.size, 1.0):
            emit(" ", flags)
        text = acadnusx.convert(g.text) if "g" in flags else g.text
        for ch in text:
            emit(ch, flags)
        prev = g
    # strip trailing spaces of the last run
    if runs:
        runs[-1][0] = runs[-1][0].rstrip()
        if not runs[-1][0]:
            runs.pop()
    _ = n
    return runs


def extract_lines(pdf_path: Path, pages: Iterable[int] | None = None) -> Iterator[Line]:
    """Yield lines in reading order (page, column, top-to-bottom). ``pages`` are 0-based."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams, LTChar, LTTextContainer, LTTextLine

    laparams = LAParams(char_margin=2.0, line_margin=0.3, word_margin=0.1)
    page_list = list(pages) if pages is not None else None
    for idx, layout in enumerate(extract_pages(str(pdf_path), page_numbers=page_list, laparams=laparams)):
        pno = (page_list[idx] if page_list is not None else idx) + 1
        mid = layout.width / 2
        lines: list[Line] = []
        for el in layout:
            if not isinstance(el, LTTextContainer):
                continue
            for tl in el:
                if not isinstance(tl, LTTextLine):
                    continue
                glyphs = [Glyph(c.get_text(), c.x0, c.x1, c.width, c.size, c.fontname)
                          for c in tl if isinstance(c, LTChar)]
                if not glyphs:
                    continue
                runs = assemble(glyphs)
                if not runs:
                    continue
                sizes = sorted(g.size for g in glyphs)
                lines.append(Line(page=pno, col=0 if tl.x0 < mid - 20 else 1, x=tl.x0, y=tl.y0,
                                  size=sizes[len(sizes) // 2], runs=runs))
        yield from merge_same_baseline(lines)


def merge_same_baseline(lines: list[Line]) -> list[Line]:
    """Sort lines in reading order and merge pieces that share a baseline in one column.

    pdfminer sometimes splits one printed line into several boxes (e.g. headword and the rest);
    pieces whose y differs by less than 0.4 of the font size are joined left to right.
    """
    out: list[Line] = []
    by_page: dict[int, list[Line]] = {}
    for ln in lines:
        by_page.setdefault(ln.page, []).append(ln)
    for page in sorted(by_page):
        items = sorted(by_page[page], key=lambda ln: (ln.col, -ln.y, ln.x))
        merged: list[Line] = []
        for ln in items:
            last = merged[-1] if merged else None
            if last is not None and last.col == ln.col and abs(last.y - ln.y) < 0.4 * max(ln.size, 1.0)                     and abs(last.size - ln.size) < 1.5:
                parts = sorted([last, ln], key=lambda x: x.x)
                runs = [list(r) for r in parts[0].runs]
                if runs and not runs[-1][0].endswith(" "):
                    runs[-1][0] += " "
                runs.extend(list(r) for r in parts[1].runs)
                merged[-1] = Line(page=page, col=ln.col, x=parts[0].x, y=max(last.y, ln.y),
                                  size=max(last.size, ln.size), runs=runs)
            else:
                merged.append(ln)
        out.extend(merged)
    return out


def dump_lines(pdf_path: Path, out_path: Path, pages: Iterable[int] | None = None) -> int:
    """Extract all lines to a JSONL file (raw cache). Returns the number of lines."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    n = 0
    last_page = 0
    with open(tmp, "w", encoding="utf-8") as fh:
        for ln in extract_lines(pdf_path, pages):
            fh.write(json.dumps(ln.to_json(), ensure_ascii=False) + "\n")
            n += 1
            if ln.page != last_page and ln.page % 50 == 0:
                log.info("enc2008 extract: page %d, %d lines", ln.page, n)
            last_page = ln.page
    tmp.replace(out_path)
    return n


def load_lines(path: Path) -> list[Line]:
    with open(path, encoding="utf-8") as fh:
        return [Line.from_json(json.loads(s)) for s in fh if s.strip()]
