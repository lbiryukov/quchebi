"""Facts from ``history_article`` (Tbilisi encyclopedia 2008, ka/ru Wikipedia) for the eponym stages.

Only structured facts written by the history stages are used (``parsed_json``): who a street is
named after (``named_after``: name, years, occupation, wiki link), former names with their
eponyms, years of origin/naming; plus the encyclopedia's biographies (``enc2008_bio``) indexed by
surname for people without a Wikidata item.
"""
from __future__ import annotations

import collections
import json
import sqlite3

from pipeline.enrich import ka as K
from pipeline.enrich.common import table_exists
from pipeline.log import get_logger

log = get_logger(__name__)

FORMER_FIELDS = ("name_ka", "name_ru", "from_year", "to_year", "attested")


def _compact_former(x: dict) -> dict:
    d = {k: x[k] for k in FORMER_FIELDS if x.get(k) not in (None, "", [])}
    ep = x.get("eponym")
    if isinstance(ep, dict):
        d["eponym"] = {k: ep[k] for k in ("name_ka", "name_ru", "birth", "death", "occupation_ka") if ep.get(k)}
    return d


def article_facts(con: sqlite3.Connection) -> dict[int, list[dict]]:
    """family_id -> compact facts of the articles about that family's streets."""
    out: dict[int, list[dict]] = collections.defaultdict(list)
    if not table_exists(con, "history_article"):
        return out
    q = ("SELECT COALESCE(a.family_id, f.family_id), a.source, a.title, a.parsed_json FROM history_article a "
         "LEFT JOIN feature f ON f.id = a.feature_id WHERE a.source != 'enc2008_bio'")
    for fam, src, title, pj in con.execute(q):
        if fam is None or not pj:
            continue
        try:
            d = json.loads(pj)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        fact: dict = {"source": src, "title": title}
        na = d.get("named_after")
        if isinstance(na, dict):
            fact["named_after"] = {k: v for k, v in na.items() if k in ("name_ka", "name_ru", "birth", "death",
                                                                       "occupation_ka", "note_ka", "src", "weak",
                                                                       "year") and v not in (None, "")}
        former = [_compact_former(x) for x in d.get("former") or [] if isinstance(x, dict)]
        if former:
            fact["former"] = former[:6]
        for k in ("origin", "named_year"):
            v = d.get(k)
            if isinstance(v, dict) and v.get("year"):
                fact[k + "_year"] = v["year"]
        if len(fact) > 2:
            out[fam].append(fact)
    for v in out.values():
        v.sort(key=lambda f: ({"enc2008": 0, "kawiki": 1, "ruwiki": 2}.get(f["source"], 3), f["title"] or ""))
    return out


def named_after(facts: list[dict]) -> list[dict]:
    return [f["named_after"] for f in facts if f.get("named_after")]


def consistent_name(base_gen: str, name_nom: str) -> bool:
    """Does an encyclopedia/wiki "named after" name fit the current eponym (shares its last word)?"""
    if not base_gen or not name_nom or not K.has_ka(name_nom):
        return False
    last = K.key_tok(K.strip_parens(base_gen).split()[-1])
    return last in {K.key_tok(t) for t in K.strip_parens(name_nom).replace("-", " ").split()}


class BioIndex:
    """Encyclopedia biographies (``enc2008_bio``) by nominative surname."""

    def __init__(self, con: sqlite3.Connection):
        self.by_surname: dict[str, list[dict]] = collections.defaultdict(list)
        if not table_exists(con, "history_article"):
            return
        for title, pj in con.execute("SELECT title, parsed_json FROM history_article WHERE source='enc2008_bio'"):
            try:
                d = json.loads(pj or "{}")
            except ValueError:
                continue
            sur = d.get("surname") or title
            if not sur:
                continue
            rec = {k: d[k] for k in ("given", "surname", "patronymic", "birth", "death", "occupation_ka") if d.get(k)}
            self.by_surname[K.key_tok(sur)].append(rec)
        log.debug("bio index: %d surnames", len(self.by_surname))

    def lookup(self, base_gen: str, limit: int = 3) -> list[dict]:
        toks = K.strip_parens(K.clean(base_gen)).split()
        if not toks:
            return []
        hits = self.by_surname.get(K.key_tok(toks[-1]), [])
        if len(toks) > 1:
            given = {K.key_tok(t) for t in toks[:-1]}
            exact = [h for h in hits if given & {K.key_tok(g) for g in (h.get("given") or "").replace("(", " ").replace(")", " ").split()}]
            if exact:
                return exact[:limit]
            return []                    # surname matches but the given name differs: not this person
        return hits[:limit]
