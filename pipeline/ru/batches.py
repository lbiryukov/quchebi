"""LLM batches of the Russian reference stage.

``researcher`` (Opus, extraction): one dossier per Wikipedia article of a street -- the infobox
fields and the cleaned text of the sections about the name, the history and the notable buildings.
Batches are Markdown files with short lines (the Read tool truncates long lines), written to
``data/<city>/enrich/batches/researcher/rNNN.md``.

``classifier`` (Haiku, classification only): one dossier per name family -- what the researcher
(or Wikidata) says the name honours plus the Wikidata facts of that entity; the agent picks taxonomy
categories. Batches are JSON arrays in ``data/<city>/enrich/batches/classifier/cNNN.json``.

Results are read by :mod:`pipeline.ru.merge` from ``data/<city>/enrich/results/<role>/``.
"""
from __future__ import annotations

import json
import re
import sqlite3
import textwrap
from collections import defaultdict
from pathlib import Path

from pipeline.config import CITY, ENRICH
from pipeline.log import get_logger, stage
from pipeline.ru import sources as S
from pipeline.ru.fetch import RUWIKI, clean_wiki, load_ref

log = get_logger(__name__)

BATCH_DIR = ENRICH / "batches"
RESULT_DIR = ENRICH / "results"
ARTICLE_MAX = 30000        # characters of cleaned text per article
SECTION_MAX = 14000
BATCH_CHARS = 34000        # characters of text per researcher batch
CLASSIFIER_BATCH = 25
DROP_SECTIONS = re.compile(r"^(примечани|литератур|ссылк|см\.? также|источник|галере|фото|транспорт|общественный транспорт|"
                           r"в культуре|в литературе|в кино|карт)", re.IGNORECASE)
_HEADING = re.compile(r"^(={2,4})\s*(.*?)\s*\1\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------- wikitext -> dossier text
def infobox(text: str) -> dict[str, str]:
    """Fields of the first infobox-like template at the top of the article."""
    return _infobox(text)[0]


_BOX = re.compile(r"\{\{\s*(?:Улица|Площадь|Мост|Набережная|Парк|Сквер|Бульвар|Достопримечательность|Карточка)[^|{}\n]*\s*\n?\s*\|",
                  re.IGNORECASE)


def _infobox(text: str) -> tuple[dict[str, str], str]:
    """(fields of the infobox, the text without the infobox template)."""
    m = _BOX.search(text[:4000])
    if not m:
        return {}, text
    depth, i, start = 0, m.start(), m.start()
    while i < len(text) - 1:
        two = text[i:i + 2]
        if two == "{{":
            depth += 1
            i += 2
        elif two == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                break
        else:
            i += 1
    body = text[start:i]
    out = {}
    for k, v in re.findall(r"\n\s*\|\s*([^=|\n]{2,40}?)\s*=\s*([^\n]*)", body):
        v = clean_wiki(re.sub(r"\{\{(?:nobr|s|нп\d?|не переведено[^|]*)\|([^{}|]*)[^{}]*\}\}", r"\1", v))
        if v and not re.search(r"\.(jpg|png|svg|jpeg)\b", v, re.I) and not v.startswith("http") and len(v) < 400:
            out[k.strip().lower()] = v
    return out, text[:start] + text[i:]


def keep_templates(s: str) -> str:
    """Unwrap templates whose content is ordinary text (nobr, dates, non-translated links)."""
    s = re.sub(r"\{\{\s*(?:nobr|s|comment|abbr|нп\d?|не переведено[^|{}]*|iw|год|lang-[a-z]+)\s*\|([^{}|]*)[^{}]*\}\}", r"\1", s, flags=re.I)
    s = re.sub(r"\{\{\s*(?:МЛО|maplink|coord)[^{}]*\}\}", "", s, flags=re.I)
    return s


def article_text(wikitext: str) -> tuple[dict[str, str], str]:
    """(infobox fields, cleaned text with ``## heading`` lines) of an article."""
    box, rest = _infobox(wikitext)
    body = keep_templates(rest)
    body = re.sub(r"<gallery.*?</gallery>", "", body, flags=re.DOTALL | re.I)
    body = re.sub(r"\{\|.*?\|\}", "", body, flags=re.DOTALL)          # tables
    body = re.sub(r"\[\[(?:Файл|File|Изображение|Image|Категория|Category):[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]", "", body, flags=re.I)
    parts: list[tuple[str, str]] = []
    pos, head = 0, ""
    for m in _HEADING.finditer(body):
        parts.append((head, body[pos:m.start()]))
        head, pos = m.group(2), m.end()
    parts.append((head, body[pos:]))
    out, total, skip_level = [], 0, False
    for head, chunk in parts:
        if DROP_SECTIONS.match(clean_wiki(head)):
            continue
        lines = []
        for ln in chunk.split("\n"):
            bullet = re.match(r"^\s*([*#:;]+)\s*", ln)
            t = clean_wiki(ln[bullet.end():] if bullet else ln)
            if t:
                lines.append(("- " if bullet else "") + t)
        txt = "\n".join(lines).strip()
        if not txt:
            continue
        if len(txt) > SECTION_MAX:
            txt = txt[:SECTION_MAX].rsplit("\n", 1)[0] + "\n[...раздел сокращён...]"
        if total + len(txt) > ARTICLE_MAX:
            txt = txt[:max(0, ARTICLE_MAX - total)].rsplit("\n", 1)[0] + "\n[...статья сокращена...]"
        out.append((f"## {clean_wiki(head)}\n" if head else "") + txt)
        total += len(txt)
        if total >= ARTICLE_MAX:
            break
    return box, "\n\n".join(out)


def wrap(text: str, width: int = 170) -> str:
    return "\n".join(w for ln in text.split("\n") for w in (textwrap.wrap(ln, width, break_long_words=False) or [""]))


# ---------------------------------------------------------------------------- researcher
def researcher_dossiers(con: sqlite3.Connection, ref: dict) -> list[dict]:
    feats = {r["id"]: r for r in con.execute(
        "SELECT f.id, f.kind, f.ka, f.len_m, f.wikidata, fam.key AS fkey FROM feature f "
        "LEFT JOIN family fam ON fam.id = f.family_id")}
    old = defaultdict(list)
    for r in con.execute("SELECT feature_id, name, source FROM feature_name WHERE kind = 'old' AND lang = 'ru'"):
        old[r["feature_id"]].append(r["name"])
    by_title: dict[str, list[int]] = defaultdict(list)
    for fid, rec in ref["streets"].items():
        if rec.get("title") and int(fid) in feats:
            by_title[rec["title"]].append(int(fid))
    dossiers = []
    for title in sorted(by_title):
        fids = sorted(by_title[title], key=lambda i: (-(feats[i]["len_m"] or 0), feats[i]["ka"]))
        try:
            text, _ = S.wikitext(RUWIKI, title, offline=True)
        except S.Offline:
            continue
        if not text:
            continue
        box, body = article_text(text)
        if len(body) < 200:
            continue
        qid = next((ref["streets"][str(i)].get("qid") for i in fids if ref["streets"][str(i)].get("qid")), None)
        named = []
        qs = list(ref["items"].get(qid or "", {}).get("named_after", []))
        for i in fids:
            qs += re.findall(r"Q\d+", feats[i]["wikidata"] or "")
        for q in dict.fromkeys(qs):
            p = ref["people"].get(q, {})
            years = "–".join(str(p[k]) if p.get(k) else "?" for k in ("birth_year", "death_year")) if (
                p.get("birth_year") or p.get("death_year")) else ""
            named.append({"qid": q, "ru": p.get("ru", ""), "desc": p.get("desc", ""), "years": years})
        dossiers.append({
            "title": title, "features": [feats[i]["ka"] for i in fids],
            "families": sorted({feats[i]["fkey"] for i in fids if feats[i]["fkey"]}),
            "street_qid": qid, "named_after": named, "osm_old_names": sorted({n for i in fids for n in old[i]}),
            "infobox": box, "text": body,
        })
    return dossiers


def write_researcher(dossiers: list[dict]) -> dict:
    out_dir = BATCH_DIR / "researcher"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("r*.md"):
        old.unlink()
    batches: list[list[dict]] = [[]]
    size = 0
    for d in sorted(dossiers, key=lambda d: d["title"]):
        n = len(d["text"]) + 600
        if batches[-1] and size + n > BATCH_CHARS:
            batches.append([])
            size = 0
        batches[-1].append(d)
        size += n
    index = {}
    num = 0
    for bi, batch in enumerate(batches, 1):
        name = f"r{bi:03d}"
        lines = [f"# BATCH {name} ({CITY.title})", ""]
        for d in batch:
            num += 1
            did = f"s{num:03d}"
            index[did] = {"batch": name, "title": d["title"], "features": d["features"], "families": d["families"],
                          "street_qid": d["street_qid"], "named_after": [x["qid"] for x in d["named_after"]]}
            lines += [f"## DOSSIER {did}", f"article: {d['title']}", "features: " + "; ".join(d["features"])]
            if d["named_after"]:
                lines.append("wikidata_named_after: " + "; ".join(
                    f"{x['qid']} = {x['ru']}" + (f" ({x['desc']}" + (f", {x['years']}" if x["years"] else "") + ")"
                                                 if x["desc"] or x["years"] else "") for x in d["named_after"]))
            else:
                lines.append("wikidata_named_after: (none)")
            if d["osm_old_names"]:
                lines.append("osm_old_names: " + "; ".join(d["osm_old_names"]))
            for k, v in d["infobox"].items():
                if re.search(r"назван|прежн|честь|дата|год|протяж|район|метро|имя", k):
                    lines.append(f"infobox.{k}: {v}")
            lines += ["### TEXT", wrap(d["text"]), "### END", ""]
        (out_dir / f"{name}.md").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"researcher_dossiers": len(dossiers), "researcher_batches": len(batches),
            "researcher_chars": sum(len(d["text"]) for d in dossiers)}


# ---------------------------------------------------------------------------- classifier
def load_results(role: str) -> list[dict]:
    out = []
    for f in sorted((RESULT_DIR / role).glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as exc:
            log.error("result %s is not JSON: %s", f, exc)
            continue
        for r in data.get("results", []) if isinstance(data, dict) else []:
            if isinstance(r, dict):
                r["_batch"] = f.stem
                out.append(r)
    return out


def family_origins(con: sqlite3.Connection, ref: dict) -> dict[str, dict]:
    """Family key -> best researcher "origin" (with the dossier's Wikidata candidates)."""
    idx_path = BATCH_DIR / "researcher" / "index.json"
    index = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {}
    best: dict[str, dict] = {}
    for r in load_results("researcher"):
        meta = index.get(str(r.get("id")))
        org = r.get("origin")
        if not meta or not isinstance(org, dict) or not org.get("label_ru"):
            continue
        for fam in meta["families"]:
            if len(meta["families"]) > 1 and len(meta["features"]) > 1:
                pass  # an article about several streets of one name: every family gets the origin
            cand = dict(org, article=meta["title"], allowed_qids=meta["named_after"])
            cur = best.get(fam)
            if cur is None or float(cand.get("confidence") or 0) > float(cur.get("confidence") or 0):
                best[fam] = cand
    return best


def classifier_dossiers(con: sqlite3.Connection, ref: dict) -> list[dict]:
    origins = family_origins(con, ref)
    fams = con.execute(
        "SELECT fam.id, fam.key, fam.base_ka, GROUP_CONCAT(f.ka, '; ') AS members, GROUP_CONCAT(f.wikidata, ';') AS wd, "
        "GROUP_CONCAT(DISTINCT f.kind) AS kinds FROM family fam JOIN feature f ON f.family_id = fam.id "
        "GROUP BY fam.id ORDER BY fam.key").fetchall()
    out = []
    for r in fams:
        org = origins.get(r["key"])
        qids = list(dict.fromkeys(re.findall(r"Q\d+", r["wd"] or "")))
        if org and org.get("wikidata_qid") and org["wikidata_qid"] in org.get("allowed_qids", []):
            qids = [org["wikidata_qid"]] + [q for q in qids if q != org["wikidata_qid"]]
        if not org and not qids:
            continue  # nothing to classify: no article and no Wikidata statement
        d = {"family_key": r["key"], "base": r["base_ka"], "members": r["members"], "kinds": r["kinds"]}
        if org:
            d["researcher"] = {k: org.get(k) for k in ("kind", "label_ru", "who_ru", "named_year", "confidence", "quote")
                               if org.get(k) not in (None, "")}
            d["article"] = org.get("article")
        cands = []
        for q in qids[:3]:
            p = ref["people"].get(q)
            if p:
                cands.append({k: p.get(k) for k in ("qid", "ru", "desc", "types", "gender", "birth_year", "death_year",
                                                    "occupations") if p.get(k) not in (None, "", [])}
                             | {"local": bool(set(p.get("citizen", [])) & set(CITY.local_countries))})
        if cands:
            d["wikidata"] = cands
        out.append(d)
    return out


def write_classifier(dossiers: list[dict]) -> dict:
    out_dir = BATCH_DIR / "classifier"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("c*.json"):
        old.unlink()
    n = 0
    for i in range(0, len(dossiers), CLASSIFIER_BATCH):
        n += 1
        (out_dir / f"c{n:03d}.json").write_text(
            json.dumps(dossiers[i:i + CLASSIFIER_BATCH], ensure_ascii=False, indent=1), encoding="utf-8")
    return {"classifier_dossiers": len(dossiers), "classifier_batches": n}


def run(con: sqlite3.Connection, roles: list[str] | None = None) -> dict:
    with stage(log, "ru.batches", roles=roles) as out:
        ref = load_ref()
        roles = roles or ["researcher"] + (["classifier"] if any((RESULT_DIR / "researcher").glob("*.json")) else [])
        if "researcher" in roles:
            out.update(write_researcher(researcher_dossiers(con, ref)))
        if "classifier" in roles:
            out.update(write_classifier(classifier_dossiers(con, ref)))
        return out


__all__ = ["run", "article_text", "infobox", "load_results", "family_origins", "BATCH_DIR", "RESULT_DIR"]
