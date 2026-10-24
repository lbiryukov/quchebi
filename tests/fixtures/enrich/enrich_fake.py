"""Test helpers for the enrichment stages: fixture database and an offline fake Wikidata client.

The fixture (``families.json``, ``entities.json``, ``gazetteer.json``) was cut from the research
prototype outputs: 54 real Tbilisi families (keys from ``pipeline.names``), trimmed Wikidata
entities and a gazetteer subset.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pipeline.db import connect, migrate

HERE = Path(__file__).resolve().parent


def fixture(name: str):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def make_db(families: list[dict] | None = None):
    """In-memory DB with the fixture families and features."""
    con = connect(":memory:")
    migrate(con)
    fams = families if families is not None else fixture("families.json")
    fid = 0
    for i, f in enumerate(fams, 1):
        con.execute("INSERT INTO family(id, key, base_ka, base_ru, base_en, eponym_nom_ka) VALUES (?,?,?,?,?,?)",
                    (i, f["key"], f.get("base_ka"), f.get("base_ru"), f.get("base_en"), f.get("nom_ka")))
        head_id = None
        for m in f["members"]:
            fid += 1
            parent = head_id if m.get("derived") else None
            con.execute("INSERT INTO feature(id, kind, ka, ru, en, family_id, parent_id, wikidata, osm_ids, len_m) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (fid, m.get("kind") or "street", m["ka"], m.get("ru"), m.get("en"), i, parent,
                         m.get("wikidata"), json.dumps(m.get("osm_ids", [])), m.get("len_m", 100.0)))
            if head_id is None and not m.get("derived"):
                head_id = fid
    con.commit()
    return con


def ent(qid: str, ka: str = "", ru: str = "", en: str = "", desc_ru: str = "", desc_en: str = "",
        claims: dict | None = None, born: int | None = None, died: int | None = None,
        sitelinks: dict | None = None, aliases: dict | None = None) -> dict:
    """Hand-made Wikidata entity in wbgetentities format."""
    def lab(d):
        return {k: {"language": k, "value": v} for k, v in d.items() if v}
    e = {"type": "item", "id": qid, "labels": lab({"ka": ka, "ru": ru, "en": en}),
         "descriptions": lab({"ru": desc_ru, "en": desc_en}), "claims": {}, "sitelinks": {}}
    for p, vals in (claims or {}).items():
        e["claims"][p] = [{"mainsnak": {"snaktype": "value", "property": p, "datavalue": {
            "type": "wikibase-entityid", "value": {"id": v}}}, "rank": "normal"} for v in vals]
    for p, y in (("P569", born), ("P570", died)):
        if y is not None:
            sign = "-" if y < 0 else "+"
            e["claims"][p] = [{"mainsnak": {"snaktype": "value", "property": p, "datavalue": {
                "type": "time", "value": {"time": f"{sign}{abs(y):04d}-01-01T00:00:00Z", "precision": 9}}},
                "rank": "normal"}]
    for site, title in (sitelinks or {}).items():
        e["sitelinks"][site] = {"site": site, "title": title}
    if aliases:
        e["aliases"] = {lang: [{"language": lang, "value": v} for v in vals] for lang, vals in aliases.items()}
    return e


class FakeFetcher:
    stats = {"hits": 0, "misses": 0}

    class cache:  # noqa: N801 - mimic HttpCache.count()
        @staticmethod
        def count():
            return {}


class FakeClient:
    """Duck-typed stand-in for :class:`pipeline.wikidata.client.WikidataClient` (no network)."""

    offline = True

    def __init__(self, entities: dict | None = None, search_map: dict | None = None,
                 extracts_map: dict | None = None):
        self.ents = dict(fixture("entities.json"))
        self.ents.update(entities or {})
        self.search_map = search_map or {}
        self.extracts_map = extracts_map or {}
        self.sparql_calls = 0
        self.fetcher = FakeFetcher()
        self.cache = FakeFetcher.cache

    def entities(self, ids, props="", langs=""):
        return {q: self.ents[q] for q in ids if q in self.ents}

    def entity(self, qid, props=""):
        return self.ents.get(qid)

    def sparql(self, query: str, refresh: bool = False):
        """Supports the label-match query of pipeline.enrich.match only."""
        self.sparql_calls += 1
        pairs = re.findall(r'\("(F[0-9a-f]+)" "((?:[^"\\]|\\.)*)"@(\w+)\)', query)
        rows = []
        for k, lab, lang in pairs:
            lab = json.loads('"' + lab + '"')
            for q, e in self.ents.items():
                p31 = [c["mainsnak"]["datavalue"]["value"]["id"] for c in e.get("claims", {}).get("P31", [])]
                if "Q5" not in p31:
                    continue
                labs = [v["value"] for l, v in e.get("labels", {}).items() if l == lang]
                labs += [a["value"] for a in e.get("aliases", {}).get(lang, [])]
                if lab in labs:
                    rows.append({"k": {"value": k}, "item": {"value": "http://www.wikidata.org/entity/" + q}})
        return rows

    def humans_by_label(self, labels, chunk: int = 100):
        self.sparql_calls += 1
        out = {}
        for lab, lang in set(labels):
            res = set()
            for q, e in self.ents.items():
                p31 = [c["mainsnak"]["datavalue"]["value"]["id"] for c in e.get("claims", {}).get("P31", [])]
                if "Q5" not in p31:
                    continue
                labs = [v["value"] for l, v in e.get("labels", {}).items() if l == lang]
                labs += [a["value"] for a in e.get("aliases", {}).get(lang, [])]
                if lab in labs:
                    res.add(q)
            out[(lab, lang)] = res
        return out

    def titles_to_qids(self, wiki: str, titles):
        out = {}
        for q, e in self.ents.items():
            t = (e.get("sitelinks") or {}).get(wiki, {}).get("title")
            if t and t in set(titles):
                out[t] = q
        return out

    def search(self, text: str, human: bool = False, limit: int = 8):
        return list(self.search_map.get(text, []))[:limit]

    def extracts(self, lang: str, titles, chars: int = 600):
        return {t: self.extracts_map[(lang, t)][:chars] for t in titles if (lang, t) in self.extracts_map}

    def seed_from_prototype(self):
        return 0


def prepare(tmp, client: FakeClient | None = None, con=None, families: list[dict] | None = None,
            with_batches: bool = True) -> dict:
    """Run the enrichment stages up to ``batches`` on the fixture; artefacts go to ``tmp``."""
    from pipeline.enrich import batches as B
    from pipeline.enrich import dossiers as D
    from pipeline.enrich import facts as FA
    from pipeline.enrich import gazetteer as G
    from pipeline.enrich import match as M
    from pipeline.enrich import qids as Q
    from pipeline.enrich import rules as R
    from pipeline.enrich.common import load_families, paths

    paths.set(Path(tmp) / "enrich", Path(tmp) / "build")
    con = con or make_db(families)
    client = client or FakeClient()
    fams = load_families(con)
    gaz = G.build(con, None, rows_by_name=fixture("gazetteer.json"))
    rules = R.run(con, fams, gaz)
    qids = Q.run(con, fams, None, street_rows=[])
    match = M.run(con, fams, rules, qids, client)
    state = FA.run(con, load_families(con), rules, qids, match, client)
    rows = D.run(con, load_families(con), rules, qids, match, state, client)
    out = {"con": con, "client": client, "gaz": gaz, "rules": rules, "qids": qids, "match": match,
           "facts": state, "dossiers": rows, "paths": paths}
    if with_batches:
        out["manifest"] = B.run(con, rows, client)
    return out


def dossier_of(rows: list[dict], family_key: str, kind: str = "queue") -> dict:
    for r in rows:
        if r["meta"].get("family_key") == family_key and r["meta"]["kind"] == kind:
            return r["dossier"]
    raise KeyError(family_key)


def answer(family_id: str, kind: str = "person", qid: str | None = None, conf: float = 0.9,
           qconf: float | None = None, cats: list[str] | None = None, name_ru: str = "Тест",
           evidence: list[dict] | None = None, needs_review: bool = False, review_reason: str | None = None,
           eponyms: list[dict] | None = None, toponym: dict | None = None) -> dict:
    """A schema-valid classifier answer."""
    if eponyms is None:
        eponyms = [{"name_ka": "ტესტი", "name_ru": name_ru, "wikidata_qid": qid,
                    "qid_choice": "picked_among_candidates" if qid else "no_candidates", "gender": None,
                    "birth_year": None, "death_year": None, "origin": "georgian", "short_ru": "тест"}]
    return {"family_id": family_id, "eponym_kind": kind, "eponyms": eponyms,
            "categories": cats or ["none.unknown"], "toponym": toponym, "confidence": conf,
            "qid_confidence": qconf if qconf is not None else (0.9 if qid else None),
            "evidence": evidence if evidence is not None else [{"source": "wikidata", "quote": "test"}],
            "needs_review": needs_review, "review_reason": review_reason}


def write_results(paths, role: str, batch: str, results: list[dict], raw: str | None = None) -> Path:
    p = paths.results / role / f"{batch}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw if raw is not None else json.dumps({"batch": batch, "role": role, "results": results},
                                                        ensure_ascii=False), encoding="utf-8")
    return p
