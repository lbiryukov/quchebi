"""Stage batches: queue of hard encyclopedia articles for the ``history-extractor`` subagent.

Writes ``data/enrich/batches/history/hNNN.jsonl`` (about 15 articles per file). One line:

    {"id": "<article key>", "title": "...", "text": "<abridged article text, <= 1500 chars>",
     "candidates": [{"feature_id": 12, "ka": "...", "ru": "...", "areas": ["..."]}]}

The subagent (``.claude/agents/history-extractor.md``) answers in
``data/enrich/results/history/hNNN.json`` following ``pipeline/history/schema.json``; the
``merge`` stage validates and stores the answers. The article text comes from the raw cache
(``data/raw/enc2008``) and is written only under ``data/enrich`` (gitignored), shortened.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from pipeline.config import ENRICH
from pipeline.history import enc_parse as P
from pipeline.history.link import FeatureIndex, _ka_variants, article_key
from pipeline.history.names_adapter import loose_key, name_key
from pipeline.history.store import load_articles, table_exists
from pipeline.log import get_logger, stage

log = get_logger(__name__)

BATCH_DIR = ENRICH / "batches" / "history"
RESULT_DIR = ENRICH / "results" / "history"
BATCH_SIZE = 15
MAX_TEXT = 1500
_KEEP_RX = re.compile(r"\d{4}|\d0-იან|ერქვა|ეწოდა|დაერქვა|დაარქვეს|როგორც|ყოფ\.|ჰქვია|სახელ|მიენიჭა|ეწოდებოდა|"
                      r"შეტანილია|დატანილია|მონაკვეთ")


def abridge(text: str, limit: int = MAX_TEXT) -> str:
    """Keep the header sentence and the sentences about names/years, up to ``limit`` characters."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    sents = [s for _, s in P.split_sentences(text)]
    out: list[str] = []
    total = 0
    for i, s in enumerate(sents):
        if i == 0 or _KEEP_RX.search(s):
            add = len(s) + (3 if out else 0)
            if total + add > limit:
                if i == 0:
                    out.append(s[: limit - 1] + "…")
                break
            out.append(s)
            total += add
    res = " … ".join(out)
    return res[:limit]


def candidates(index: FeatureIndex | None, title: str, linked: int | None, limit: int = 5) -> list[dict]:
    if index is None:
        return []
    fids: list[int] = []
    if linked:
        fids.append(linked)
    for v in _ka_variants(title):
        for f in sorted(index.exact.get(name_key(v), set()) | index.old.get(name_key(v), set())):
            if f not in fids:
                fids.append(f)
    lk = loose_key(title)
    if lk[0]:
        for f in sorted(index.loose.get(lk, set())):
            if f not in fids:
                fids.append(f)
    out = []
    for f in fids[:limit]:
        ft = index.feats[f]
        d = {"feature_id": ft.id, "ka": ft.ka}
        if ft.ru:
            d["ru"] = ft.ru
        out.append(d)
    return out


def run(con: sqlite3.Connection, texts: dict, out_dir: Path = BATCH_DIR,
        batch_size: int = BATCH_SIZE) -> dict:
    with stage(log, "history.batches") as st:
        arts = [a for a in load_articles(con, ("enc2008",)) if a["facts"].get("needs_llm")]
        index = None
        if table_exists(con, "feature") and con.execute("SELECT COUNT(*) FROM feature").fetchone()[0]:
            index = FeatureIndex(con)
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("h*.jsonl"):
            old.unlink()
        items = []
        missing = 0
        for a in arts:
            text = texts.get((a["title"], a["text_hash"])) or texts.get(a["title"])
            if not text:
                missing += 1
                continue
            items.append({"id": article_key(a), "title": a["title"], "text": abridge(text),
                          "candidates": candidates(index, a["title"], a["feature_id"])})
        n_files = 0
        for i in range(0, len(items), batch_size):
            n_files += 1
            path = out_dir / f"h{n_files:03d}.jsonl"
            with open(path, "w", encoding="utf-8") as fh:
                for it in items[i:i + batch_size]:
                    fh.write(json.dumps(it, ensure_ascii=False) + "\n")
        st.update({"queued": len(items), "files": n_files, "missing_text": missing,
                   "with_candidates": sum(1 for it in items if it["candidates"])})
        log.info("history batches: %d articles in %d files -> %s", len(items), n_files, out_dir)
        return st
