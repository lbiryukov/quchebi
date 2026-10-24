"""Lexicons for the rule classifier: Georgian given names and place names (Wikidata SPARQL dumps).

Files live in ``data/raw/wikidata/gazetteers/<name>.json`` (raw SPARQL bindings). A missing file is
taken from the research prototype cache when present (same queries), otherwise downloaded.
"""
from __future__ import annotations

import collections
import json
import shutil
import sqlite3
from dataclasses import dataclass, field

from pipeline.config import RAW
from pipeline.enrich import ka as K
from pipeline.enrich.common import table_exists
from pipeline.log import get_logger
from pipeline.wikidata.client import PROTOTYPE_CACHE, WikidataClient
from pipeline.wikidata.http import CacheMiss, HttpError

log = get_logger(__name__)

GAZ_DIR = RAW / "wikidata" / "gazetteers"

QUERIES = {
    "given_names": """
SELECT ?item ?ka ?cls WHERE {
  VALUES ?cls { wd:Q12308941 wd:Q11879590 wd:Q3409032 wd:Q202444 }
  ?item wdt:P31 ?cls; rdfs:label ?ka. FILTER(lang(?ka)="ka")
}""",
    "geo_georgia": """
SELECT ?item ?ka ?cls WHERE {
  VALUES ?c { wd:Q230 wd:Q23334 wd:Q23427 }
  ?item wdt:P17 ?c; wdt:P625 []; rdfs:label ?ka. FILTER(lang(?ka)="ka")
  OPTIONAL { ?item wdt:P31 ?cls }
}""",
    "geo_world_kawiki": """
SELECT ?item ?ka ?cls ?pop WHERE {
  ?art schema:about ?item; schema:isPartOf <https://ka.wikipedia.org/>.
  ?item wdt:P1082 ?pop. FILTER(?pop > 150000)
  ?item rdfs:label ?ka. FILTER(lang(?ka)="ka")
  OPTIONAL { ?item wdt:P31 ?cls }
}""",
    "countries": """
SELECT ?item ?ka WHERE {
  VALUES ?cls { wd:Q6256 wd:Q3624078 wd:Q3024240 }
  ?item wdt:P31 ?cls; rdfs:label ?ka. FILTER(lang(?ka)="ka")
}""",
    "geo_neighbours_kawiki": """
SELECT ?item ?ka ?cls WHERE {
  VALUES ?c { wd:Q43 wd:Q399 wd:Q227 wd:Q159 wd:Q794 }
  ?art schema:about ?item; schema:isPartOf <https://ka.wikipedia.org/>.
  ?item wdt:P17 ?c; wdt:P625 []; rdfs:label ?ka. FILTER(lang(?ka)="ka")
  OPTIONAL { ?item wdt:P31 ?cls }
}""",
}

G_SETTLE = {"Q486972", "Q532", "Q5084", "Q515", "Q1549591", "Q7930989", "Q3957", "Q4313794", "Q15008703", "Q1529096",
            "Q28328984", "Q2023000", "Q20724701", "Q106389302", "Q56557504", "Q200250", "Q174844", "Q2264924",
            "Q51929311", "Q902814", "Q1187811", "Q707813", "Q108178728", "Q5119", "Q1093829", "Q494721", "Q29045252",
            "Q3199141", "Q748149", "Q15661340", "Q42744322", "Q2074737", "Q29946056", "Q62049", "Q17051044"}
G_REGION = {"Q2655841", "Q141223669", "Q15284", "Q192287", "Q43263", "Q48336", "Q835714", "Q13417250", "Q56061",
            "Q41162", "Q1147395", "Q2610712", "Q82794", "Q1620908", "Q3502482", "Q2198484", "Q2716259", "Q6465",
            "Q15089", "Q35657", "Q1344695", "Q24746", "Q50337", "Q12443800", "Q1149652", "Q204910", "Q845820",
            "Q162620", "Q1776764", "Q5098", "Q158683", "Q465842", "Q50198", "Q509686", "Q15149663", "Q240601",
            "Q1952852", "Q3184121", "Q2555896", "Q11774097", "Q20738811", "Q192611", "Q106517174"}
G_COUNTRY = {"Q6256", "Q3624078", "Q3024240", "Q7270", "Q123480", "Q112099"}
G_HOOD = {"Q123705", "Q2983893", "Q15195406"}
G_WATER = {"Q4022", "Q47521", "Q23397", "Q131681", "Q39594"}
G_RELIEF = {"Q8502", "Q46831", "Q133056", "Q35666", "Q8072", "Q160091", "Q35509", "Q34763", "Q23442"}
G_BATTLE = {"Q178561", "Q188055"}
G_ETHNOS = {"Q41710"}
GROUPS = [("battle", G_BATTLE), ("country", G_COUNTRY), ("region", G_REGION), ("neighbourhood", G_HOOD),
          ("settlement", G_SETTLE), ("water", G_WATER), ("relief", G_RELIEF), ("ethnos", G_ETHNOS)]


def ensure(name: str, client: WikidataClient | None = None, refresh: bool = False) -> list[dict]:
    dst = GAZ_DIR / f"{name}.json"
    if dst.exists() and not refresh:
        return json.loads(dst.read_text(encoding="utf-8"))
    proto = PROTOTYPE_CACHE / "wikidata_gazetteers" / f"{name}.json"
    if proto.exists() and not refresh:
        GAZ_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(proto, dst)
        log.info("gazetteer %s: copied from the prototype cache", name)
        return json.loads(dst.read_text(encoding="utf-8"))
    if client is None:
        log.warning("gazetteer %s missing and no client to download it", name)
        return []
    try:
        rows = client.sparql(QUERIES[name], refresh=refresh)
    except (CacheMiss, HttpError) as e:
        log.error("gazetteer %s download failed: %s", name, e)
        return []
    GAZ_DIR.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    log.info("gazetteer %s: downloaded %d rows", name, len(rows))
    return rows


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


@dataclass
class Gazetteer:
    forms: dict[str, set[tuple[str, str, str, str]]] = field(default_factory=lambda: collections.defaultdict(set))
    given_names: set[str] = field(default_factory=set)

    def lookup(self, core: str) -> set[tuple[str, str, str, str]]:
        hits: set[tuple[str, str, str, str]] = set()
        for c in {core, K.nominative(core)}:
            hits |= self.forms.get(c, set())
        return hits

    def add_place(self, label: str, group: str, scope: str, ref: str) -> None:
        for form in K.gen_forms(label):
            self.forms[form].add((group, scope, ref, label))


def build(con: sqlite3.Connection | None = None, client: WikidataClient | None = None,
          rows_by_name: dict[str, list[dict]] | None = None) -> Gazetteer:
    """Assemble the gazetteer from SPARQL dumps (or ``rows_by_name`` in tests) and the DB areas."""
    g = Gazetteer()
    get = (lambda n: rows_by_name.get(n, [])) if rows_by_name is not None else (lambda n: ensure(n, client))
    for fn, scope in (("geo_georgia", "georgia"), ("geo_neighbours_kawiki", "abroad"),
                      ("geo_world_kawiki", "abroad"), ("countries", "abroad")):
        by_item: dict[str, dict] = collections.defaultdict(lambda: {"cls": set(), "ka": set()})
        for r in get(fn):
            try:
                it = _qid(r["item"]["value"])
                by_item[it]["ka"].add(r["ka"]["value"])
                if "cls" in r:
                    by_item[it]["cls"].add(_qid(r["cls"]["value"]))
            except (KeyError, TypeError):
                continue
        n = 0
        for it, v in by_item.items():
            grp = next((gname for gname, s in GROUPS if v["cls"] & s), None)
            if grp is None:
                if fn == "countries":
                    grp = "country"
                elif fn == "geo_world_kawiki" and not v["cls"] & {"Q1288568", "Q33742", "Q34770"}:
                    grp = "settlement"
                else:
                    continue
            for lab in v["ka"]:
                g.add_place(lab, grp, scope, it)
                n += 1
        log.debug("gazetteer %s: %d items, %d labels", fn, len(by_item), n)
    if con is not None and table_exists(con, "area"):
        for (name,) in con.execute("SELECT ka FROM area WHERE ka IS NOT NULL"):
            g.add_place(K.clean(name), "neighbourhood", "tbilisi", "area")
        for (name,) in con.execute("SELECT ka FROM feature WHERE kind IN ('district','neighbourhood') AND ka IS NOT NULL"):
            g.add_place(K.clean(name), "neighbourhood", "tbilisi", "area")
    for r in get("given_names"):
        try:
            lab = K.strip_parens(r["ka"]["value"])
        except (KeyError, TypeError):
            continue
        for part in lab.split("/"):
            part = part.strip()
            if K.has_ka(part):
                g.given_names.add(part)
                if part.endswith("ი"):
                    g.given_names.add(part[:-1])
    log.info("gazetteer: %d genitive forms, %d given names", len(g.forms), len(g.given_names))
    return g
