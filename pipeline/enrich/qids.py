"""Stage ``qids``: known eponym QIDs of families.

1. OSM ``name:etymology:wikidata`` of member features (the whole family inherits it). Several
   QIDs in one tag ("Q1;Q2") = several eponyms; different single QIDs on different members =
   conflict (majority wins, flagged).
2. Wikidata street items in Georgia with P138 (named after), linked to a family by OSM way /
   relation id, by Georgian label (exact member name or eponym key) or by kawiki title.

Output ``data/enrich/stages/qids.json``: {family.key: {"qids", "source", "multi", "conflict", "refs"}}.
"""
from __future__ import annotations

import collections
import json
import re
import shutil
import sqlite3

from pipeline.config import RAW
from pipeline.enrich import ka as K
from pipeline.enrich.common import (Family, add_provenance, clear_provenance, split_qids, stage_path, write_json)
from pipeline.log import get_logger, stage
from pipeline.wikidata.client import PROTOTYPE_CACHE, WikidataClient
from pipeline.wikidata.http import CacheMiss, HttpError

log = get_logger(__name__)

STREETS_PATH = RAW / "wikidata" / "streets" / "wd_streets_georgia.json"
STREET_CLASSES = "wd:Q79007 wd:Q174782 wd:Q1251403 wd:Q207934 wd:Q54114 wd:Q12280 wd:Q34442"
STREETS_QUERY = """SELECT ?street ?ka ?named ?adm ?way ?rel ?kawiki WHERE {
  VALUES ?cls { %s }
  ?street wdt:P31 ?cls; wdt:P17 wd:Q230.
  OPTIONAL { ?street rdfs:label ?ka. FILTER(lang(?ka)="ka") }
  OPTIONAL { ?street wdt:P138 ?named }
  OPTIONAL { ?street wdt:P131 ?adm }
  OPTIONAL { ?street wdt:P10689 ?way }
  OPTIONAL { ?street wdt:P402 ?rel }
  OPTIONAL { ?kw schema:about ?street; schema:isPartOf <https://ka.wikipedia.org/>; schema:name ?kawiki }
}""" % STREET_CLASSES


def osm_qids(f: Family) -> dict:
    """QIDs from member features' name:etymology:wikidata."""
    per_feature = [split_qids(m.wikidata) for m in f.members if m.wikidata]
    per_feature = [q for q in per_feature if q]
    if not per_feature:
        return {}
    tags = collections.Counter(tuple(q) for q in per_feature)
    best, n = tags.most_common(1)[0]
    conflict = len(tags) > 1 and not all(set(t) <= set(best) or set(best) <= set(t) for t in tags)
    return {"qids": list(best), "source": "osm", "multi": len(best) > 1, "conflict": conflict,
            "refs": [f"osm:{m.osm_ids[0]}" for m in f.members if m.wikidata and m.osm_ids][:5],
            "tag_variants": {";".join(t): c for t, c in tags.items()} if len(tags) > 1 else {}}


def load_streets(client: WikidataClient | None, refresh: bool = False) -> list[dict]:
    if STREETS_PATH.exists() and not refresh:
        return json.loads(STREETS_PATH.read_text(encoding="utf-8"))
    rows = None
    if client is not None and not client.offline:
        try:
            rows = client.sparql(STREETS_QUERY, refresh=refresh)
        except (CacheMiss, HttpError) as e:
            log.warning("street items SPARQL failed (%s); trying the prototype dump", e)
    if rows is None:
        proto = PROTOTYPE_CACHE / "wikidata_streets" / "wd_streets_georgia.json"
        if proto.exists():
            STREETS_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(proto, STREETS_PATH)
            log.info("street items: using the prototype dump %s", proto)
            return json.loads(STREETS_PATH.read_text(encoding="utf-8"))
        return []
    STREETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STREETS_PATH.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def _qid(b: dict | None) -> str | None:
    if not b:
        return None
    return b["value"].rsplit("/", 1)[-1]


def street_items(rows: list[dict]) -> dict[str, dict]:
    items: dict[str, dict] = collections.defaultdict(
        lambda: {"ka": set(), "named": set(), "adm": set(), "way": set(), "rel": set(), "kawiki": set()})
    for r in rows:
        it = _qid(r.get("street"))
        if not it:
            continue
        d = items[it]
        if "ka" in r:
            d["ka"].add(r["ka"]["value"])
        for fld, conv in (("named", _qid), ("adm", _qid)):
            v = conv(r.get(fld))
            if v:
                d[fld].add(v)
        if "way" in r:
            d["way"].add("w" + r["way"]["value"])
        if "rel" in r:
            d["rel"].add("r" + r["rel"]["value"])
        if "kawiki" in r:
            d["kawiki"].add(r["kawiki"]["value"])
    return items


def _label_base(label: str) -> str:
    lab = re.sub(r",.*$", "", K.clean(label))
    lab = K.strip_parens(lab)
    return K.split_ka_name(lab)["base"]


HOW_RANK = {"osm_id": 0, "exact_name": 1, "eponym_key": 2}


def link_streets(fams: list[Family], items: dict[str, dict]) -> dict[str, dict]:
    """family.key -> {"qids", "refs", "how", "conflict"} from street items with P138.

    Items linked by OSM id beat exact labels, which beat eponym-key matches; items that disagree
    on P138 at the best link level make the family a conflict (the most frequent set wins).
    """
    by_name: dict[str, set[str]] = collections.defaultdict(set)
    by_osm: dict[str, set[str]] = collections.defaultdict(set)
    by_norm: dict[str, set[str]] = collections.defaultdict(set)
    for f in fams:
        if f.base_ka:
            by_norm[K.norm_key(f.base_ka)].add(f.key)
        for m in f.members:
            by_name[K.clean(m.ka)].add(f.key)
            for o in m.osm_ids:
                by_osm[o].add(f.key)
    per_family: dict[str, list[tuple[int, str, tuple[str, ...]]]] = collections.defaultdict(list)
    for it, v in items.items():
        if not v["named"]:
            continue
        found: dict[str, str] = {}
        for o in sorted(v["way"] | v["rel"]):
            for k in by_osm.get(o, ()):
                found.setdefault(k, "osm_id")
        for lab in sorted(v["ka"] | v["kawiki"]):
            lab_c = K.strip_parens(re.sub(r",.*$", "", K.clean(lab)))
            for k in by_name.get(lab_c, ()):
                if HOW_RANK["exact_name"] < HOW_RANK[found.get(k, "eponym_key")]:
                    found[k] = "exact_name"
            base = _label_base(lab)
            if base:
                for k in by_norm.get(K.norm_key(base), ()):
                    found.setdefault(k, "eponym_key")
        for k, how in found.items():
            per_family[k].append((HOW_RANK[how], it, tuple(sorted(v["named"]))))
    links: dict[str, dict] = {}
    for k, lst in per_family.items():
        best_rank = min(r for r, _, _ in lst)
        top = [(it, named) for r, it, named in lst if r == best_rank]
        sets = collections.Counter(named for _, named in top)
        named, _ = sets.most_common(1)[0]
        how = next(h for h, r in HOW_RANK.items() if r == best_rank)
        links[k] = {"qids": list(named), "refs": sorted(f"wd:{it}" for it, n in top if n == named),
                    "how": how, "conflict": len(sets) > 1}
    return links


def run(con: sqlite3.Connection, fams: list[Family], client: WikidataClient | None, out_dir=None,
        refresh: bool = False, street_rows: list[dict] | None = None) -> dict[str, dict]:
    with stage(log, "enrich.qids", families=len(fams)) as st:
        out: dict[str, dict] = {}
        for f in fams:
            o = osm_qids(f)
            if o:
                out[f.key] = o
        rows = load_streets(client, refresh=refresh) if street_rows is None else street_rows
        items = street_items(rows)
        links = link_streets(fams, items)
        n_new = n_agree = n_disagree = 0
        for k, l in links.items():
            if k in out:
                if set(l["qids"]) & set(out[k]["qids"]):
                    n_agree += 1
                    out[k].setdefault("refs", []).extend(l["refs"][:3])
                else:
                    n_disagree += 1
                    out[k]["p138_disagree"] = l["qids"]
                    out[k]["conflict"] = True
                continue
            if len(l["qids"]) > 3:          # an item named after many things is not a usable signal
                continue
            out[k] = {"qids": l["qids"], "source": "wd_p138", "multi": len(l["qids"]) > 1,
                      "conflict": l["conflict"], "refs": l["refs"][:5], "how": l["how"]}
            n_new += 1
        with con:
            clear_provenance(con, ["qid"], ["osm", "wd_p138"])
            for k, d in out.items():
                for q in d["qids"]:
                    add_provenance(con, "family", k, "qid", q, d["source"], ";".join(d.get("refs", [])[:3]),
                                   0.6 if d.get("conflict") else (0.98 if d["source"] == "osm" else 0.95))
        write_json(stage_path("qids", out_dir), out)
        st.update(osm=sum(1 for d in out.values() if d["source"] == "osm"), p138_new=n_new, p138_agree=n_agree,
                  p138_disagree=n_disagree, street_items=len(items),
                  multi=sum(1 for d in out.values() if d["multi"]),
                  conflicts=sum(1 for d in out.values() if d.get("conflict")))
    return out
