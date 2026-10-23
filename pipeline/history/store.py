"""DB access for the history stages: ``history_article`` and ``name_history``.

``history_article`` holds one row per source article with ``parsed_json`` = facts only (names,
years, district, counts, eponym facts) -- never the article text. ``name_history`` is derived
from the articles plus their links to features (see :mod:`pipeline.history.link`), so it is
rebuilt whenever ``link`` or ``merge`` runs.
"""
from __future__ import annotations

import json
import sqlite3

from pipeline.db import tx
from pipeline.log import get_logger

log = get_logger(__name__)

HISTORY_SOURCES = ("enc2008", "enc2008_bio", "kawiki", "ruwiki")
NAME_SOURCES = ("enc2008", "enc2008_llm", "kawiki", "ruwiki", "osm_old_name", "addr")


def replace_articles(con: sqlite3.Connection, records: list[dict], sources: tuple[str, ...]) -> int:
    """Replace all ``history_article`` rows of ``sources`` with ``records``."""
    with tx(con):
        q = ",".join("?" * len(sources))
        con.execute(f"DELETE FROM history_article WHERE source IN ({q})", sources)
        rows = []
        for r in records:
            facts = dict(r["facts"])
            facts["entry"] = r.get("entry")
            if r.get("needs_llm"):
                facts["needs_llm"] = True
            if r.get("parent_title"):
                facts["parent_title"] = r["parent_title"]
            rows.append((r["source"], r["title"], r.get("page"), r.get("text_hash"),
                         json.dumps(facts, ensure_ascii=False, separators=(",", ":"))))
        con.executemany("INSERT INTO history_article(source, title, page, text_hash, parsed_json) VALUES (?,?,?,?,?)",
                        rows)
    log.info("history_article: %d rows written for sources %s", len(records), ",".join(sources))
    return len(records)


def load_articles(con: sqlite3.Connection, sources: tuple[str, ...] = HISTORY_SOURCES) -> list[dict]:
    q = ",".join("?" * len(sources))
    out = []
    for row in con.execute(f"SELECT id, source, title, page, feature_id, family_id, text_hash, parsed_json "
                           f"FROM history_article WHERE source IN ({q}) ORDER BY id", sources):
        try:
            facts = json.loads(row["parsed_json"] or "{}")
        except ValueError:
            facts = {}
        out.append({"id": row["id"], "source": row["source"], "title": row["title"], "page": row["page"],
                    "feature_id": row["feature_id"], "family_id": row["family_id"], "text_hash": row["text_hash"],
                    "facts": facts})
    return out


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def table_count(con: sqlite3.Connection, name: str) -> int:
    if not table_exists(con, name):
        return 0
    return con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
