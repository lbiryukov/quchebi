"""Stage enc2008: the 2008 encyclopedia "თბილისი. ქუჩები, გამზირები, მოედნები".

fetch   -> data/raw/enc2008/12.pdf (60.7 MB, resumable download)
extract -> data/raw/enc2008/lines.jsonl (text with font flags; full text stays in data/raw)
parse   -> data/interim/history/enc2008.json (facts only) + history_article rows

Records (one per street article, lane sub-entry, orphan cross-reference target and biography):

    {"source": "enc2008", "title", "page", "text_hash", "entry": "article|sub|xref_target|sub_lane",
     "facts": {...parsed_json...}, "needs_llm": bool}
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

from pipeline.config import INTERIM, RAW
from pipeline.history import enc_parse as P
from pipeline.history import georgian as G
from pipeline.history.enc_pdf import dump_lines, load_lines
from pipeline.history.enc_split import Entry, split_entries
from pipeline.history.http import download
from pipeline.log import get_logger

log = get_logger(__name__)

PDF_URL = "https://georgianencyclopedia.ge/pdf/12.pdf"
PDF_SIZE = 60663296
ENC_DIR = RAW / "enc2008"
PDF_PATH = ENC_DIR / "12.pdf"
LINES_PATH = ENC_DIR / "lines.jsonl"
OUT_DIR = INTERIM / "history"
OUT_PATH = OUT_DIR / "enc2008.json"
LLM_THRESHOLD = 0.65


def fetch() -> Path:
    return download(PDF_URL, PDF_PATH, expected_size=PDF_SIZE)


def extract(force: bool = False) -> Path:
    if LINES_PATH.exists() and not force and LINES_PATH.stat().st_mtime >= PDF_PATH.stat().st_mtime:
        log.info("enc2008 extract: cached %s", LINES_PATH)
        return LINES_PATH
    n = dump_lines(PDF_PATH, LINES_PATH)
    log.info("enc2008 extract: %d lines -> %s", n, LINES_PATH)
    return LINES_PATH


def load_entries(lines_path: Path = LINES_PATH) -> list[Entry]:
    return split_entries(load_lines(lines_path))


# ------------------------------------------------------------------------------ biographies

class BioIndex:
    """Biographies indexed by possible genitive forms of the surname."""

    def __init__(self, bios: list[dict]):
        self.bios = bios
        self.by_gen: dict[str, list[dict]] = collections.defaultdict(list)
        for b in bios:
            for g in P.bio_genitives(b["surname"]):
                self.by_gen[g].append(b)

    def find(self, gen_phrase: str) -> dict | None:
        """'ვ. ლენინის' / 'გალაკტიონ ტაბიძის' / 'ტაბიძის' -> the unique matching biography."""
        words = [w for w in re.sub(r"\([^)]*\)", " ", gen_phrase).split() if w]
        if not words:
            return None
        cands = self.by_gen.get(words[-1], [])
        if not cands:
            return None
        given = words[0] if len(words) > 1 else None
        if given:
            g0 = given.rstrip(".")
            if given.endswith("."):
                cands = [b for b in cands if (b.get("given") or "").startswith(g0)]
            else:
                cands = [b for b in cands if (b.get("given") or "").split(" ")[0] == g0
                         or g0 in (b.get("given") or "")]
        uniq = {(b["surname"], b.get("given"), b.get("birth")) for b in cands}
        if len(uniq) == 1:
            return cands[0]
        return None


def bio_record(b: dict) -> dict:
    given = (b.get("given") or "").strip()
    first_given = re.sub(r"\s*\([^)]*\)", "", given).strip()
    d = {"name_ka": f"{first_given} {b['surname']}".strip()}
    for k in ("birth", "death", "occupation_ka"):
        if b.get(k):
            d[k] = b[k]
    d["src"] = "enc2008_bio"
    return d


def expand_initial(gen_phrase: str, bio: dict) -> str | None:
    """'ვ. ლენინის' + bio(ლენინი, ვლადიმერ) -> 'ვლადიმერ ლენინის'."""
    words = gen_phrase.split()
    if len(words) >= 2 and G.INITIAL_RE.match(words[0]) and bio.get("given"):
        first = re.sub(r"\s*\([^)]*\)", "", bio["given"]).split()
        if first and first[0].startswith(words[0][0]):
            return " ".join([first[0]] + words[1:])
    return None


# ------------------------------------------------------------------------------ build

def _norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", t.replace("„", "").replace("“", "")).strip()


def build(entries: list[Entry]) -> list[dict]:
    """Parse entries into fact records."""
    bios = []
    for e in entries:
        if e.kind == "bio":
            b = P.parse_bio(e.title, e.body)
            b["page"] = e.page
            bios.append(b)
    P.register_given_names({(b.get("given") or "").split(" ")[0] for b in bios if b.get("given")})
    bidx = BioIndex(bios)
    gloss_by_parent: dict[int, list[Entry]] = collections.defaultdict(list)
    for e in entries:
        if e.kind == "gloss" and e.parent is not None:
            gloss_by_parent[e.parent].append(e)

    records: list[dict] = []
    by_title: dict[str, list[dict]] = collections.defaultdict(list)
    for i, e in enumerate(entries):
        if e.kind not in ("article", "sub"):
            continue
        facts = P.parse_article(e.title, e.body, e.bold)
        fj = facts.as_json()
        fj.pop("chain", None)
        if e.qualifier:
            fj["qualifier"] = e.qualifier
        _resolve_eponyms(fj, facts, e, bidx, gloss_by_parent.get(i, []))
        rec = {"source": "enc2008", "title": e.title, "page": str(e.page), "text_hash": e.text_hash,
               "entry": e.kind, "facts": fj,
               "needs_llm": bool(facts.has_naming_text and facts.confidence < LLM_THRESHOLD)}
        records.append(rec)
        by_title[_norm_title(e.title)].append(rec)
        for sf in facts.sub_former:
            sub = {"source": "enc2008", "title": sf["sub"], "page": str(e.page), "text_hash": e.text_hash,
                   "entry": "sub_lane", "parent_title": e.title,
                   "facts": {"title": sf["sub"], "kind": G.KIND_CODE.get(P.title_kind_word(sf["sub"]) or ""),
                             "district_2008": fj.get("district_2008"), "place": fj.get("place"),
                             "former": [{"name_ka": sf["name_ka"], "confidence": 0.8, "note": "lane"}],
                             "confidence": 0.8},
                   "needs_llm": False}
            records.append(sub)
            by_title[_norm_title(sf["sub"])].append(sub)

    # cross-references "OLD, იხ. CURRENT": add old names to the current article
    n_xref = n_xref_new = n_orphan = 0
    for e in entries:
        if e.kind not in ("xref", "sub_xref"):
            continue
        x = P.parse_xref(e.title, e.body, e.qualifier)
        for tgt in x["targets"]:
            n_xref += 1
            recs = _find_records(by_title, tgt)
            if not recs:
                n_orphan += 1
                rec = {"source": "enc2008", "title": tgt, "page": str(e.page), "text_hash": e.text_hash,
                       "entry": "xref_target",
                       "facts": {"title": tgt, "kind": G.KIND_CODE.get(P.title_kind_word(tgt) or ""),
                                 "former": [], "confidence": 0.8, "xref_only": True},
                       "needs_llm": False}
                records.append(rec)
                by_title[_norm_title(tgt)].append(rec)
                recs = [rec]
            for rec in recs:
                if _add_xref_name(rec, x["old"], x["place"]):
                    n_xref_new += 1
    # expand initials of former names through the biographies
    n_exp = 0
    for rec in records:
        for f in rec["facts"].get("former", []):
            epo = f.get("eponym_gen")
            if not epo:
                continue
            b = bidx.find(epo)
            if b is None:
                continue
            full = expand_initial(epo, b)
            if full and f["name_ka"].startswith(epo):
                f["short_ka"] = f["name_ka"]
                f["name_ka"] = full + f["name_ka"][len(epo):]
                n_exp += 1
            f["eponym"] = bio_record(b)
    for b in bios:
        records.append({"source": "enc2008_bio", "title": b["surname"], "page": str(b.pop("page")),
                        "text_hash": None, "entry": "bio", "facts": {"kind": "bio", **b}, "needs_llm": False})
    n_art = sum(1 for r in records if r["entry"] in ("article", "sub"))
    n_former = sum(len(r["facts"].get("former", [])) for r in records)
    log.info("enc2008 build: %d article records (%d needs_llm), %d sub-lane, %d xref-only targets, %d bios; "
             "xrefs %d (%d new names, %d orphan targets); former names %d (%d initials expanded)",
             n_art, sum(r["needs_llm"] for r in records),
             sum(1 for r in records if r["entry"] == "sub_lane"), n_orphan, len(bios), n_xref, n_xref_new,
             n_orphan, n_former, n_exp)
    return records


def _resolve_eponyms(fj: dict, facts: P.ArticleFacts, e: Entry, bidx: BioIndex, glosses: list[Entry]) -> None:
    """named_after: biography of the person in the title, or the glossary note of a toponym."""
    from pipeline.history.names_adapter import parse_name
    pn = parse_name(e.title)
    base = re.sub(r"\s*\([^)]*\)", "", pn.base_ka or "").strip()
    if base:
        b = bidx.find(base)
        if b is not None:
            fj["named_after"] = bio_record(b)
            return
    for g in glosses:
        gt = g.title.strip(" ,")
        if base and gt[:4] and base.split()[-1].startswith(gt[: max(3, min(len(gt) - 1, 5))]):
            fj["named_after"] = {**P.parse_gloss(gt, g.body), "src": "enc2008_gloss"}
            return
    if glosses:
        g = glosses[0]
        fj["named_after"] = {**P.parse_gloss(g.title, g.body), "src": "enc2008_gloss", "weak": True}


def _find_records(by_title: dict[str, list[dict]], target: str) -> list[dict]:
    for t in P.lane_variants(target):
        recs = by_title.get(_norm_title(t))
        if recs:
            return [r for r in recs if r["entry"] in ("article", "sub", "sub_lane", "xref_target")] or recs
    return []


def _add_xref_name(rec: dict, old: str, place: str | None) -> bool:
    former = rec["facts"].setdefault("former", [])
    for f in former:
        if P._same_name(f["name_ka"], old):
            if len(old) > len(f["name_ka"]) and re.search(r"[ა-ჰ]\.", f["name_ka"]) and not re.search(r"[ა-ჰ]\.", old):
                f.setdefault("short_ka", f["name_ka"])
                f["name_ka"] = old
            f["xref"] = True
            return False
    d = {"name_ka": old, "confidence": 0.85, "xref": True}
    if place:
        d["place"] = place
    former.append(d)
    return True


def parse(lines_path: Path = LINES_PATH, out_path: Path = OUT_PATH) -> list[dict]:
    entries = load_entries(lines_path)
    records = build(entries)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(records, ensure_ascii=False, indent=0), encoding="utf-8")
    log.info("enc2008 parse: %d records -> %s", len(records), out_path)
    return records


def article_texts(lines_path: Path = LINES_PATH) -> dict:
    """(title, text_hash) and title -> article text (for LLM batches only; read from the raw cache)."""
    out: dict = {}
    if not lines_path.exists():
        return out
    for e in load_entries(lines_path):
        if e.kind in ("article", "sub"):
            txt = f"{e.title}, {e.body}"
            out[(e.title, e.text_hash)] = txt
            out.setdefault(e.title, txt)
    return out
