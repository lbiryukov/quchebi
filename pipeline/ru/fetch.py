"""Fetch the reference sources of a Russian-language district into ``data/<city>/interim/ref.json``.

1. Wikidata items with coordinates inside the city bbox (SPARQL box search) and their details:
   types, "named after" (P138), inception, architect, heritage status, address.
2. Streets: every street-like feature of the database is matched to its Wikidata item / Russian
   Wikipedia article (OSM ``wikidata`` / ``wikipedia`` tags, exact label match, then the title
   patterns ``<name> (Москва)`` and ``<name>``); the wikitext of the article is cached.
3. Facts about the eponyms (the P138 targets and the OSM ``name:etymology:wikidata`` values).
4. Wikivoyage lists of cultural heritage of the Central okrug: monuments inside the boundary.

Nothing here is written to the database; :mod:`pipeline.ru.batches` and :mod:`pipeline.ru.merge`
read ``ref.json``.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict

import shapely
from shapely.geometry import Point

from pipeline.config import CITY, INTERIM
from pipeline.log import get_logger, stage
from pipeline.osm.extract import EXTRACT_CACHE
from pipeline.osm.geo import to_ll, to_xy
from pipeline.osm.model import Extract
from pipeline.ru import sources as S

log = get_logger(__name__)

REF_PATH = INTERIM / "ref.json"
RUWIKI = "ru.wikipedia.org"
WIKIVOYAGE = "ru.wikivoyage.org"
WV_INDEX = "Культурное наследие России/Москва"
WV_PREFIX = "Культурное наследие России/Москва/Центральный округ/"
CITY_SUFFIX = " (Москва)"
#: Kinds whose features are looked up as "streets" (an article about the street itself).
STREET_KINDS = ("street", "avenue", "highway", "road", "embankment", "boulevard", "alley", "lane", "dead_end",
                "passage", "square", "bridge", "tunnel", "garden", "park")
SKIP_HIGHWAYS = {"footway", "path", "steps", "cycleway", "service", "pedestrian", "track"}
CHUNK = 150


def norm(s: str) -> str:
    s = (s or "").lower().replace("ё", "е").replace("«", "").replace("»", "").replace('"', "")
    s = re.sub(r"\s*\((?:москва|хамовники)\)\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _point(wkt: str) -> tuple[float, float] | None:
    m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", wkt or "")
    return (float(m.group(2)), float(m.group(1))) if m else None


# ---------------------------------------------------------------------------- Wikidata
def wd_bbox_items(offline: bool) -> dict[str, dict]:
    lon0, lat0, lon1, lat1 = CITY.bbox
    rows = S.sparql(f'''SELECT ?item ?itemLabel ?ruTitle ?coord WHERE {{
  SERVICE wikibase:box {{ ?item wdt:P625 ?coord . bd:serviceParam wikibase:cornerSouthWest "Point({lon0} {lat0})"^^geo:wktLiteral ; wikibase:cornerNorthEast "Point({lon1} {lat1})"^^geo:wktLiteral . }}
  OPTIONAL {{ ?art schema:about ?item ; schema:isPartOf <https://ru.wikipedia.org/> ; schema:name ?ruTitle }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "ru,en". }}
}}''', offline=offline)
    items: dict[str, dict] = {}
    for r in rows:
        q = S.qid_of(r.get("item", ""))
        p = _point(r.get("coord", ""))
        if not q or p is None:
            continue
        it = items.setdefault(q, {"qid": q, "label": r.get("itemLabel", ""), "lat": p[0], "lon": p[1]})
        if r.get("ruTitle"):
            it["ruwiki"] = r["ruTitle"]
    return items


def wd_details(qids: list[str], offline: bool) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(qids), CHUNK):
        vals = " ".join("wd:" + q for q in qids[i:i + CHUNK])
        rows = S.sparql(f'''SELECT ?item (GROUP_CONCAT(DISTINCT ?p31l; separator="|") AS ?types)
 (GROUP_CONCAT(DISTINCT ?p31; separator="|") AS ?typeIds) (GROUP_CONCAT(DISTINCT ?named; separator="|") AS ?namedIds)
 (MIN(?inc) AS ?inception) (GROUP_CONCAT(DISTINCT ?archl; separator="|") AS ?architects)
 (GROUP_CONCAT(DISTINCT ?herl; separator="|") AS ?heritage) (SAMPLE(?streetl) AS ?street) (SAMPLE(?no) AS ?house)
 (SAMPLE(?addr) AS ?address) (SAMPLE(?desc) AS ?descr) (SAMPLE(?ruTitle) AS ?ruwiki) (SAMPLE(?lbl) AS ?label)
WHERE {{ VALUES ?item {{ {vals} }}
  OPTIONAL {{ ?item wdt:P31 ?p31 . ?p31 rdfs:label ?p31l FILTER(LANG(?p31l)="ru") }}
  OPTIONAL {{ ?item wdt:P138 ?named }}
  OPTIONAL {{ ?item wdt:P571 ?inc }}
  OPTIONAL {{ ?item wdt:P84 ?arch . ?arch rdfs:label ?archl FILTER(LANG(?archl)="ru") }}
  OPTIONAL {{ ?item wdt:P1435 ?her . ?her rdfs:label ?herl FILTER(LANG(?herl)="ru") }}
  OPTIONAL {{ ?item p:P669 ?st . ?st ps:P669 ?streetItem . ?streetItem rdfs:label ?streetl FILTER(LANG(?streetl)="ru") OPTIONAL {{ ?st pq:P670 ?no }} }}
  OPTIONAL {{ ?item wdt:P6375 ?addr FILTER(LANG(?addr)="ru") }}
  OPTIONAL {{ ?item schema:description ?desc FILTER(LANG(?desc)="ru") }}
  OPTIONAL {{ ?art schema:about ?item ; schema:isPartOf <https://ru.wikipedia.org/> ; schema:name ?ruTitle }}
  OPTIONAL {{ ?item rdfs:label ?lbl FILTER(LANG(?lbl)="ru") }}
}} GROUP BY ?item''', offline=offline)
        for r in rows:
            q = S.qid_of(r.get("item", ""))
            d: dict = {}
            for k in ("types", "architects", "heritage"):
                if r.get(k):
                    d[k] = [x for x in r[k].split("|") if x]
            if r.get("typeIds"):
                d["type_ids"] = [S.qid_of(x) for x in r["typeIds"].split("|") if x]
            if r.get("namedIds"):
                d["named_after"] = [S.qid_of(x) for x in r["namedIds"].split("|") if x.startswith("http")]
            m = re.match(r"(-?\d{1,4})-", r.get("inception", ""))
            if m:
                d["year"] = int(m.group(1))
            for k_src, k in (("street", "street"), ("house", "house"), ("address", "address"), ("descr", "desc"),
                             ("ruwiki", "ruwiki"), ("label", "label")):
                if r.get(k_src):
                    d[k] = r[k_src]
            out[q] = d
        log.info("wikidata details: %d/%d", min(i + CHUNK, len(qids)), len(qids))
    return out


def wd_people(qids: list[str], offline: bool) -> dict[str, dict]:
    """Facts about eponym candidates (humans and anything else a street is named after)."""
    out: dict[str, dict] = {}
    for i in range(0, len(qids), CHUNK):
        vals = " ".join("wd:" + q for q in qids[i:i + CHUNK])
        rows = S.sparql(f'''SELECT ?item (SAMPLE(?ru) AS ?label) (SAMPLE(?en) AS ?labelEn) (SAMPLE(?desc) AS ?descr)
 (GROUP_CONCAT(DISTINCT ?p31; separator="|") AS ?typeIds) (GROUP_CONCAT(DISTINCT ?p31l; separator="|") AS ?types)
 (SAMPLE(?g) AS ?gender) (MIN(?b) AS ?birth) (MIN(?d) AS ?death)
 (GROUP_CONCAT(DISTINCT ?occl; separator="|") AS ?occ) (GROUP_CONCAT(DISTINCT ?cit; separator="|") AS ?citizen)
 (SAMPLE(?ruTitle) AS ?ruwiki)
WHERE {{ VALUES ?item {{ {vals} }}
  OPTIONAL {{ ?item rdfs:label ?ru FILTER(LANG(?ru)="ru") }}
  OPTIONAL {{ ?item rdfs:label ?en FILTER(LANG(?en)="en") }}
  OPTIONAL {{ ?item schema:description ?desc FILTER(LANG(?desc)="ru") }}
  OPTIONAL {{ ?item wdt:P31 ?p31 . OPTIONAL {{ ?p31 rdfs:label ?p31l FILTER(LANG(?p31l)="ru") }} }}
  OPTIONAL {{ ?item wdt:P21 ?g }}
  OPTIONAL {{ ?item wdt:P569 ?b }}
  OPTIONAL {{ ?item wdt:P570 ?d }}
  OPTIONAL {{ ?item wdt:P106 ?oc . ?oc rdfs:label ?occl FILTER(LANG(?occl)="ru") }}
  OPTIONAL {{ ?item wdt:P27 ?cit }}
  OPTIONAL {{ ?art schema:about ?item ; schema:isPartOf <https://ru.wikipedia.org/> ; schema:name ?ruTitle }}
}} GROUP BY ?item''', offline=offline)
        for r in rows:
            q = S.qid_of(r.get("item", ""))
            d: dict = {"qid": q}
            for k_src, k in (("label", "ru"), ("labelEn", "en"), ("descr", "desc"), ("ruwiki", "ruwiki")):
                if r.get(k_src):
                    d[k] = r[k_src]
            d["type_ids"] = [S.qid_of(x) for x in r.get("typeIds", "").split("|") if x]
            d["types"] = [x for x in r.get("types", "").split("|") if x]
            g = S.qid_of(r.get("gender", ""))
            d["gender"] = {"Q6581097": "m", "Q2449503": "m", "Q6581072": "f", "Q1052281": "f"}.get(g)
            for k_src, k in (("birth", "birth_year"), ("death", "death_year")):
                m = re.match(r"(-?\d{1,4})-", r.get(k_src, ""))
                if m:
                    d[k] = int(m.group(1))
            d["occupations"] = [x for x in r.get("occ", "").split("|") if x][:8]
            d["citizen"] = [S.qid_of(x) for x in r.get("citizen", "").split("|") if x]
            out[q] = d
    return out


# ---------------------------------------------------------------------------- streets
def osm_wiki_tags() -> dict[str, dict[str, str]]:
    """OSM object ref (``w123``) -> its own wikidata / wikipedia tags."""
    out: dict[str, dict[str, str]] = {}
    if not EXTRACT_CACHE.exists():
        log.warning("no extract cache %s: OSM wiki tags are not used", EXTRACT_CACHE)
        return out
    ex = Extract.load(EXTRACT_CACHE)
    for o in [*ex.highways, *ex.pois, *ex.landmarks, *ex.waterways]:
        t = {k: o.tags[k] for k in ("wikidata", "wikipedia", "name:etymology:wikidata", "subject:wikidata") if o.tags.get(k)}
        if t:
            out[o.ref] = t
    return out


def street_targets(con: sqlite3.Connection) -> list[sqlite3.Row]:
    ph = ",".join("?" * len(STREET_KINDS))
    rows = con.execute(f"SELECT id, kind, ka, en, highway, family_id, osm_ids, wikidata, lat, lon, len_m FROM feature "
                       f"WHERE kind IN ({ph}) ORDER BY ka, id", STREET_KINDS).fetchall()
    return [r for r in rows if (r["highway"] or "") not in SKIP_HIGHWAYS]


def _title_candidates(name: str) -> list[str]:
    cap = name[:1].upper() + name[1:]
    return [cap + CITY_SUFFIX, cap]


_STREET_HINT = re.compile(r"улиц|переул|проспект|набережн|проезд|бульвар|площад|мост|шоссе|тупик|парк|сквер|сад", re.I)


def looks_like_street_article(text: str) -> bool:
    head = text[:6000]
    return bool(re.search(r"Москв", head)) and bool(_STREET_HINT.search(head)) and "неоднозначност" not in head[:400].lower() \
        and "{{disambig" not in head.lower() and "{{неоднозначность" not in text.lower()


def resolve_streets(con: sqlite3.Connection, items: dict[str, dict], offline: bool) -> dict[int, dict]:
    """feature id -> {"qid", "title", "how"} for every street-like feature that has an article or item."""
    tags = osm_wiki_tags()
    by_label: dict[str, list[str]] = defaultdict(list)
    for q, it in items.items():
        for nm in (it.get("label"), it.get("ruwiki")):
            if nm:
                by_label[norm(nm)].append(q)
    out: dict[int, dict] = {}
    for r in street_targets(con):
        own_q, own_title = None, None
        for ref in json.loads(r["osm_ids"] or "[]"):
            t = tags.get(ref, {})
            own_q = own_q or (t.get("wikidata") if re.fullmatch(r"Q\d+", t.get("wikidata", "")) else None)
            wp = t.get("wikipedia", "")
            if wp.startswith("ru:"):
                own_title = own_title or wp[3:].replace("_", " ")
        rec: dict = {}
        if own_q:
            rec = {"qid": own_q, "how": "osm_wikidata"}
            if items.get(own_q, {}).get("ruwiki"):
                rec["title"] = items[own_q]["ruwiki"]
        if own_title and "title" not in rec:
            rec.update(title=own_title, how=rec.get("how", "osm_wikipedia"))
        if not rec:
            cands = sorted(set(by_label.get(norm(r["ka"]), [])))
            if len(cands) > 1:  # the nearest item of that label
                cands.sort(key=lambda q: (items[q]["lat"] - r["lat"]) ** 2 + ((items[q]["lon"] - r["lon"]) * 0.56) ** 2)
            if cands:
                rec = {"qid": cands[0], "how": "label"}
                if items[cands[0]].get("ruwiki"):
                    rec["title"] = items[cands[0]]["ruwiki"]
        if "title" not in rec and r["kind"] not in ("alley", "garden", "park"):
            for t in _title_candidates(r["ka"]):
                try:
                    text, final = S.wikitext(RUWIKI, t, offline=offline)
                except S.Offline:
                    text, final = None, t
                if text and looks_like_street_article(text):
                    rec.update(title=final, how=rec.get("how", "title"))
                    break
        if rec:
            out[r["id"]] = rec
    return out


# ---------------------------------------------------------------------------- Wikivoyage heritage lists
_TPL = re.compile(r"\{\{\s*monument\s*(\|.*?)\n\}\}", re.DOTALL | re.IGNORECASE)
# several "key= value" pairs may share a line; a value ends at the next "| key=" (links keep their "|")
_FIELD = re.compile(r"\|\s*([a-z][a-z_0-9-]*)\s*=\s*(.*?)(?=\s*\|\s*[a-z][a-z_0-9-]*\s*=|\Z)", re.DOTALL)


def clean_wiki(s: str) -> str:
    """Wikitext fragment -> plain text (links, templates, refs and markup removed)."""
    s = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", s, flags=re.DOTALL)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.DOTALL)
    for _ in range(3):
        s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\]", "", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("'''", "").replace("''", "").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", s).strip(" ,;")


def parse_monuments(text: str) -> list[dict]:
    out = []
    for m in _TPL.finditer(text):
        f = {k: v.strip() for k, v in _FIELD.findall(m.group(1))}
        try:
            lat, lon = float(f.get("lat", "")), float(f.get("long", ""))
        except ValueError:
            continue
        name = clean_wiki(f.get("name", ""))
        if not name:
            continue
        rec = {"name": name, "lat": lat, "lon": lon, "type": f.get("type", ""), "address": clean_wiki(f.get("address", "")),
               "year": clean_wiki(f.get("year", "")), "author": clean_wiki(f.get("author", "")),
               "desc": clean_wiki(f.get("description", ""))[:400], "knid": f.get("knid", ""),
               "complex": f.get("complex", ""), "protection": f.get("protection", ""), "wdid": f.get("wdid", ""),
               "status": f.get("status", ""), "style": clean_wiki(f.get("style", "")),
               "wiki": clean_wiki(f.get("wiki", "")), "munid": f.get("munid", "")}
        out.append({k: v for k, v in rec.items() if v not in ("", None)})
    return out


def wikivoyage_monuments(boundary, offline: bool) -> tuple[list[dict], dict]:
    index, _ = S.wikitext(WIKIVOYAGE, WV_INDEX, offline=offline)
    pages = sorted(set(re.findall(r"\[\[(" + re.escape(WV_PREFIX) + r"[^\]|#]+)", index or "")))
    st = {"pages": len(pages), "monuments": 0, "inside": 0}
    shapely.prepare(boundary)
    out = []
    for title in pages:
        try:
            text, _ = S.wikitext(WIKIVOYAGE, title, offline=offline)
        except S.Offline:
            continue
        if not text:
            continue
        mons = parse_monuments(text)
        st["monuments"] += len(mons)
        for mrec in mons:
            if shapely.contains(boundary, Point(mrec["lon"], mrec["lat"])):
                mrec["page"] = title[len(WV_PREFIX):]
                out.append(mrec)
    st["inside"] = len(out)
    return out, st


# ---------------------------------------------------------------------------- entry point
def city_boundary(con: sqlite3.Connection):
    """District polygon(s) of the database plus the boundary buffer (WGS84)."""
    geoms = [shapely.from_wkt(r[0]) for r in con.execute("SELECT geom_wkt FROM area WHERE kind='district' AND geom_wkt IS NOT NULL")]
    g = shapely.union_all(geoms)
    return to_ll(to_xy(g).buffer(max(CITY.boundary_buffer_m, 1.0)))


def run(con: sqlite3.Connection, offline: bool = False) -> dict:
    with stage(log, "ru.fetch", offline=offline) as out:
        boundary = city_boundary(con)
        items = wd_bbox_items(offline)
        shapely.prepare(boundary)
        inside = {q: it for q, it in items.items() if shapely.contains(boundary, Point(it["lon"], it["lat"]))}
        out.update(wd_bbox=len(items), wd_inside=len(inside))
        streets = resolve_streets(con, items, offline)
        street_qids = sorted({v["qid"] for v in streets.values() if v.get("qid")})
        details = wd_details(sorted(set(inside) | set(street_qids)), offline)
        for q, d in details.items():
            if q in items:
                d.setdefault("lat", items[q]["lat"])
                d.setdefault("lon", items[q]["lon"])
                d.setdefault("label", items[q]["label"])
        # articles of the streets
        n_text = 0
        for fid, rec in streets.items():
            if not rec.get("title") and rec.get("qid") and details.get(rec["qid"], {}).get("ruwiki"):
                rec["title"] = details[rec["qid"]]["ruwiki"]
            if rec.get("title"):
                try:
                    text, final = S.wikitext(RUWIKI, rec["title"], offline=offline)
                except S.Offline:
                    text, final = None, rec["title"]
                if text:
                    rec["title"] = final
                    rec["chars"] = len(text)
                    n_text += 1
                else:
                    rec.pop("title", None)
        # eponym candidates: P138 of the street items + OSM etymology tags
        ep = set()
        for rec in streets.values():
            ep.update(details.get(rec.get("qid") or "", {}).get("named_after", []))
        for r in con.execute("SELECT wikidata FROM feature WHERE wikidata IS NOT NULL"):
            ep.update(q for q in re.findall(r"Q\d+", r[0] or ""))
        people = wd_people(sorted(ep), offline)
        mons, wv = wikivoyage_monuments(boundary, offline)
        ref = {"city": CITY.id, "streets": {str(k): v for k, v in streets.items()}, "items": details,
               "inside": sorted(inside), "people": people, "monuments": mons}
        REF_PATH.parent.mkdir(parents=True, exist_ok=True)
        REF_PATH.write_text(json.dumps(ref, ensure_ascii=False), encoding="utf-8")
        out.update(streets=len(streets), street_articles=n_text, street_items=len(street_qids), details=len(details),
                   eponym_items=len(people), wikivoyage=wv)
        return out


def load_ref() -> dict:
    return json.loads(REF_PATH.read_text(encoding="utf-8"))
