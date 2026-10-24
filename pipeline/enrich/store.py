"""Writing eponyms to SQLite: upsert by QID or stable key, categories, family links, cleanup."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from pipeline.log import get_logger

log = get_logger(__name__)

EPONYM_FIELDS = ("kind", "ka", "ru", "en", "gender", "birth_year", "death_year", "origin", "short_ru",
                 "occupations_ru", "confidence", "source", "needs_review")


def upsert_eponym(con: sqlite3.Connection, rec: dict[str, Any]) -> int:
    """Insert or update an eponym identified by ``qid`` (preferred) or ``key``. Returns its id."""
    qid, key = rec.get("qid"), rec.get("key")
    if not qid and not key:
        raise ValueError("eponym needs a qid or a key")
    row = None
    if qid:
        row = con.execute("SELECT id FROM eponym WHERE qid=?", (qid,)).fetchone()
    if row is None and key:
        row = con.execute("SELECT id FROM eponym WHERE key=?", (key,)).fetchone()
    vals = {f: rec.get(f) for f in EPONYM_FIELDS}
    if isinstance(vals["occupations_ru"], (list, tuple)):
        vals["occupations_ru"] = json.dumps(list(vals["occupations_ru"]), ensure_ascii=False) if vals["occupations_ru"] else None
    vals["needs_review"] = 1 if vals.get("needs_review") else 0
    if not vals.get("kind"):
        vals["kind"] = "unknown"
    if not vals.get("source"):
        raise ValueError("eponym needs a source")
    if row is not None:
        eid = row[0]
        sets = ", ".join(f"{f}=?" for f in EPONYM_FIELDS)
        con.execute(f"UPDATE eponym SET {sets}, qid=COALESCE(?, qid), key=? WHERE id=?",
                    [vals[f] for f in EPONYM_FIELDS] + [qid, None if qid else key, eid])
        return eid
    cols = ", ".join(("qid", "key") + EPONYM_FIELDS)
    cur = con.execute(f"INSERT INTO eponym({cols}) VALUES ({', '.join('?' * (2 + len(EPONYM_FIELDS)))})",
                      [qid, None if qid else key] + [vals[f] for f in EPONYM_FIELDS])
    return int(cur.lastrowid)


def set_categories(con: sqlite3.Connection, eponym_id: int, cats: Iterable[str], source: str) -> None:
    con.execute("DELETE FROM eponym_category WHERE eponym_id=?", (eponym_id,))
    seen = []
    for c in cats:
        if c and c not in seen:
            seen.append(c)
    con.executemany("INSERT INTO eponym_category(eponym_id, category, rank, source) VALUES (?,?,?,?)",
                    [(eponym_id, c, i, source) for i, c in enumerate(seen)])


def link_family(con: sqlite3.Connection, family_key: str, eponym_id: int | None) -> None:
    con.execute("UPDATE family SET eponym_id=? WHERE key=?", (eponym_id, family_key))


def eponym_source_of_family(con: sqlite3.Connection) -> dict[str, tuple[int, str]]:
    return {k: (eid, src) for k, eid, src in con.execute(
        "SELECT f.key, e.id, e.source FROM family f JOIN eponym e ON e.id=f.eponym_id")}


def unlink_stale(con: sqlite3.Connection, keep: set[str], sources: Iterable[str]) -> int:
    """Unlink families whose eponym came from ``sources`` but which are not in ``keep`` any more."""
    src = set(sources)
    stale = [k for k, (_, s) in eponym_source_of_family(con).items() if s in src and k not in keep]
    con.executemany("UPDATE family SET eponym_id=NULL WHERE key=?", [(k,) for k in stale])
    if stale:
        log.info("unlinked %d stale family eponyms (%s)", len(stale), ",".join(sorted(src)))
    return len(stale)


def delete_orphans(con: sqlite3.Connection) -> int:
    """Delete eponyms referenced neither by a family nor by a former name."""
    cur = con.execute(
        "DELETE FROM eponym WHERE id NOT IN (SELECT eponym_id FROM family WHERE eponym_id IS NOT NULL) "
        "AND id NOT IN (SELECT eponym_id FROM name_history WHERE eponym_id IS NOT NULL)")
    if cur.rowcount:
        log.info("deleted %d orphan eponyms", cur.rowcount)
    return cur.rowcount


def eponym_by_id(con: sqlite3.Connection, eid: int) -> dict | None:
    row = con.execute("SELECT * FROM eponym WHERE id=?", (eid,)).fetchone()
    if row is None:
        return None
    d = dict(zip([c[0] for c in con.execute("SELECT * FROM eponym LIMIT 0").description], row))
    d["categories"] = [c for (c,) in con.execute(
        "SELECT category FROM eponym_category WHERE eponym_id=? ORDER BY rank", (eid,))]
    return d
