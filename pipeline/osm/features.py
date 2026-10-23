"""Features stage: streets, water, metro, squares, parks, lakes, bridges; families and parents.

* Highways: ways with the same ``name`` are merged by shared nodes, then node components closer
  than :data:`GAP_M` (single linkage). Remaining components of one name are separate features
  that share a ``homonym_group``. ``kind`` comes from :func:`pipeline.names.parse_name`.
* Waterways (river / stream / canal) are merged the same way; kind from the ``waterway`` tag.
* Metro stations: one feature per station name (nodes within 500 m are one station).
* Squares, parks / gardens, lakes, ``man_made=bridge``: same-name objects within 300 m are one
  feature; squares and bridges that duplicate a highway feature of the same name are attached to
  it (their OSM ids are added) instead of creating a second feature.
* Families: ``parse_name`` family keys resolved with :func:`pipeline.names.families.group_families`;
  ``eponym_id`` and enrichment ``kind_hint`` values (``"kind:sub"``) survive a rebuild by key.
* Parents of derived names (lanes, dead ends, passages): a street-like feature of the same family
  (respecting ``parent_kind`` / ``parent_ord``) -- sharing a node, else within 300 m, else nearest.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from pipeline.db import tx
from pipeline.log import get_logger, stage
from pipeline.names import KIND_GROUP, ParsedName, ka_to_ru, normalize_ka, normalize_ru_name, parse_name
from pipeline.names.families import group_families
from pipeline.osm.extract import poi_kind
from pipeline.osm.geo import (as_multiline, bbox_str, line_ll, representative_point, to_xy, wkt)
from pipeline.osm.model import Extract, OsmObj, OsmWay

log = get_logger(__name__)

GAP_M = 300.0             # same-name components closer than this are one street
POI_MERGE_M = 300.0       # same-name squares / parks / lakes / bridges closer than this are one feature
METRO_MERGE_M = 500.0     # station nodes of one name closer than this are one station
DEDUPE_M = 300.0          # place=square / man_made=bridge vs a highway feature of the same name
PARENT_NEAR_M = 300.0
EXCLUDED_HIGHWAYS = {"proposed", "platform", "bus_stop", "razed", "abandoned", "disused", "elevator"}
WATER_KINDS = {"river": "river", "stream": "stream", "canal": "canal"}
AREA_KINDS = {"district", "neighbourhood"}
STREET_GROUP = "street*"

_KA = re.compile(r"[ა-ჿᲐ-Ჿ]")
_CYR = re.compile(r"[А-Яа-яЁё]")
_LAT = re.compile(r"[A-Za-z]")


@dataclass
class Feat:
    kind: str
    ka: str
    ru: str | None
    en: str | None
    ru_generated: int
    parsed: ParsedName
    geom_ll: BaseGeometry
    geom_xy: BaseGeometry
    len_m: float
    source: str                                   # highway | waterway | metro | poi
    osm_ids: list[str] = field(default_factory=list)
    highway: str | None = None
    wikidata: str | None = None
    names: set[tuple[str, str, str, str]] = field(default_factory=set)  # (lang, name, kind, source)
    nodes: set[int] = field(default_factory=set)
    homonym_group: int | None = None
    family_key: str | None = None
    parent: int | None = None                     # index into the feature list
    parent_how: str | None = None


# ---------------------------------------------------------------------------- helpers
class UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, a: int) -> int:
        p = self.p
        while p[a] != a:
            p[a] = p[p[a]]
            a = p[a]
        return a

    def union(self, a: int, b: int) -> None:
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[a] = b

    def groups(self) -> list[list[int]]:
        g: dict[int, list[int]] = defaultdict(list)
        for i in range(len(self.p)):
            g[self.find(i)].append(i)
        return list(g.values())


def weighted_mode(pairs) -> str | None:
    """Most common non-empty value, weighted: ``pairs`` = iterable of (value, weight)."""
    c: Counter = Counter()
    for v, w in pairs:
        if v:
            c[v] += w
    return c.most_common(1)[0][0] if c else None


def script_lang(s: str, key_lang: str | None = None) -> str | None:
    """Language label for a name variant: ka | ru | en | lat (None = not stored)."""
    if _KA.search(s):
        return "ka"
    if _CYR.search(s):
        return "ru" if key_lang in (None, "ru") else None
    if _LAT.search(s):
        return "en" if key_lang == "en" else "lat"
    return None


_NAME_KEY = re.compile(r"^(name|alt_name|old_name|official_name|loc_name|short_name|int_name|nat_name|reg_name)(?::(.*))?$")
_SKIP_SUFFIX = ("etymology", "pronunciation", "signed", "left", "right", "wikipedia", "wikidata", "prefix", "suffix")


def name_variants(tags: dict[str, str]) -> list[tuple[str, str, str, str]]:
    """(lang, name, kind, source) for every name-like tag; kind is alt | old | official,
    source is ``osm:<tag key>`` (keeps date ranges such as ``old_name:ru:1923-1990``)."""
    out = []
    for k, v in tags.items():
        m = _NAME_KEY.match(k)
        if not m or not v:
            continue
        base, rest = m.group(1), m.group(2) or ""
        if any(s in rest for s in _SKIP_SUFFIX):
            continue
        parts = rest.split(":") if rest else []
        lang_tag = None
        for p in parts:
            if re.fullmatch(r"[a-z]{2,3}(-[A-Za-z]{4})?", p):
                lang_tag = p
                break
        if base == "name" and not parts:
            continue  # the primary name itself
        kind = "old" if base == "old_name" else "official" if base == "official_name" else "alt"
        if lang_tag == "ka-Latn":
            key_lang = "lat"
        elif lang_tag in (None, "ka", "ru", "en"):
            key_lang = lang_tag
        else:
            key_lang = "other"
        for vv in v.split(";"):
            vv = vv.strip()
            if not vv:
                continue
            lang = script_lang(vv, None if key_lang in (None, "ka") else ("ru" if key_lang == "ru" else
                                                                         "en" if key_lang == "en" else "x"))
            if lang is None:
                continue
            if key_lang == "lat" and lang in ("en", "lat"):
                lang = "lat"
            out.append((lang, vv, kind, "osm:" + k))
    return out


def generated_ru(ka: str) -> str:
    """Russian name generated from Georgian when OSM has none (``ru_generated = 1``)."""
    t = ka_to_ru(normalize_ka(ka))
    t = " ".join(w[:1].upper() + w[1:] if w[:1].isalpha() else w for w in t.split(" "))
    return normalize_ru_name(t)


def multiline_xy(ways: list[OsmWay]) -> MultiLineString:
    return MultiLineString([to_xy(line_ll(w.coords)) for w in ways if len(w.coords) >= 2])


def merge_components(ways: list[OsmWay], gap: float = GAP_M) -> list[list[OsmWay]]:
    """Split same-name ways into components: shared node, then single linkage at ``gap`` metres.

    Components are returned longest first.
    """
    n = len(ways)
    uf = UnionFind(n)
    owner: dict[int, int] = {}
    for i, w in enumerate(ways):
        for nd in w.nodes:
            j = owner.setdefault(nd, i)
            if j != i:
                uf.union(i, j)
    groups = uf.groups()
    if len(groups) > 1 and gap > 0:
        geoms = [multiline_xy([ways[i] for i in g]) for g in groups]
        tree = shapely.STRtree(geoms)
        a, b = tree.query(geoms, predicate="dwithin", distance=gap)
        uf2 = UnionFind(len(groups))
        for i, j in zip(a.tolist(), b.tolist()):
            if i != j:
                uf2.union(i, j)
        groups = [[k for gi in gg for k in groups[gi]] for gg in uf2.groups()]
    comps = [[ways[i] for i in sorted(g)] for g in groups]
    lens = [sum(to_xy(line_ll(w.coords)).length for w in c if len(w.coords) >= 2) for c in comps]
    order = sorted(range(len(comps)), key=lambda k: (-lens[k], comps[k][0].id))
    return [comps[k] for k in order]


def _line_feature(ways: list[OsmWay], kind_fn, source: str) -> Feat:
    lines_ll = [line_ll(w.coords) for w in ways if len(w.coords) >= 2]
    lens = [float(to_xy(l).length) for l in lines_ll]
    wl = list(zip(ways, lens))
    ka = ways[0].tags["name"].strip()
    ru_osm = weighted_mode((w.tags.get("name:ru"), L) for w, L in wl)
    en = weighted_mode((w.tags.get("name:en"), L) for w, L in wl)
    merged = shapely.line_merge(MultiLineString(lines_ll))
    geom_ll = as_multiline(merged)
    parsed = parse_name(ka, ru_osm, en)
    if ru_osm:
        ru, gen = normalize_ru_name(ru_osm), 0
    else:
        ru, gen = generated_ru(ka), 1
    f = Feat(
        kind=kind_fn(parsed, wl), ka=ka, ru=ru, en=en, ru_generated=gen, parsed=parsed,
        geom_ll=geom_ll, geom_xy=to_xy(geom_ll), len_m=round(sum(lens), 1), source=source,
        osm_ids=sorted(w.ref for w in ways),
        wikidata=weighted_mode((w.tags.get("name:etymology:wikidata"), L) for w, L in wl),
    )
    if source == "highway":
        f.highway = weighted_mode((w.tags.get("highway"), L) for w, L in wl)
    else:
        f.highway = None
    for w in ways:
        f.nodes.update(w.nodes)
        f.names.update(name_variants(w.tags))
    return f


def _highway_kind(p: ParsedName, _wl) -> str:
    k = p.kind or "other"
    if k in AREA_KINDS:
        return "other"  # a road named after a settlement is not an area feature
    return k


def _water_kind(_p: ParsedName, wl) -> str:
    return WATER_KINDS.get(weighted_mode((w.tags.get("waterway"), L) for w, L in wl) or "", "other")


def _clean_names(f: Feat) -> None:
    """Drop variants equal to a primary name (ka, OSM ru, en); Russian ones after normalization."""
    primary = {f.ka, f.en} | ({f.ru} if not f.ru_generated else set())
    f.names = {t for t in f.names if t[1] and t[1] not in primary
               and not (t[0] == "ru" and not f.ru_generated and normalize_ru_name(t[1]) == f.ru)}


# ---------------------------------------------------------------------------- builders
def build_streets(ex: Extract) -> tuple[list[Feat], dict]:
    by_name: dict[str, list[OsmWay]] = defaultdict(list)
    skipped = Counter()
    for w in ex.highways:
        hw = w.tags.get("highway")
        nm = (w.tags.get("name") or "").strip()
        if not nm or hw in EXCLUDED_HIGHWAYS:
            skipped[hw] += 1
            continue
        by_name[nm].append(w)
    feats: list[Feat] = []
    homonym_names = 0
    hg = 0
    for nm in sorted(by_name):
        comps = merge_components(by_name[nm])
        group = None
        if len(comps) > 1:
            homonym_names += 1
            hg += 1
            group = hg
        for comp in comps:
            f = _line_feature(comp, _highway_kind, "highway")
            f.homonym_group = group
            feats.append(f)
    stats = {"ways": sum(len(v) for v in by_name.values()), "names": len(by_name), "streets": len(feats),
             "homonym_names": homonym_names, "homonym_extra": len(feats) - len(by_name),
             "skipped_highway": dict(skipped)}
    return feats, stats


def build_waterways(ex: Extract) -> tuple[list[Feat], dict]:
    by_name: dict[str, list[OsmWay]] = defaultdict(list)
    for w in ex.waterways:
        if w.tags.get("waterway") in WATER_KINDS and (w.tags.get("name") or "").strip():
            by_name[w.tags["name"].strip()].append(w)
    feats = []
    hg_names = 0
    for nm in sorted(by_name):
        comps = merge_components(by_name[nm])
        if len(comps) > 1:
            hg_names += 1
        for comp in comps:
            feats.append(_line_feature(comp, _water_kind, "waterway"))
    return feats, {"ways": sum(len(v) for v in by_name.values()), "names": len(by_name),
                   "features": len(feats), "split_names": hg_names}


def _poi_geom_union(objs: list[OsmObj]) -> BaseGeometry:
    polys = [o.geom for o in objs if isinstance(o.geom, (Polygon, MultiPolygon))]
    if polys:
        g = shapely.union_all(polys)
        return g if isinstance(g, (Polygon, MultiPolygon)) else shapely.make_valid(g)
    lines = [o.geom for o in objs if isinstance(o.geom, (LineString, MultiLineString))]
    if lines:
        return as_multiline(shapely.line_merge(shapely.union_all(lines)))
    pts = [o.geom for o in objs if isinstance(o.geom, Point)]
    if len(pts) == 1:
        return pts[0]
    c = shapely.MultiPoint(pts).centroid
    return Point(c.x, c.y)


def _cluster(objs: list[OsmObj], dist_m: float) -> list[list[OsmObj]]:
    if len(objs) == 1:
        return [objs]
    geoms = [to_xy(o.geom) for o in objs]
    tree = shapely.STRtree(geoms)
    a, b = tree.query(geoms, predicate="dwithin", distance=dist_m)
    uf = UnionFind(len(objs))
    for i, j in zip(a.tolist(), b.tolist()):
        uf.union(i, j)
    return [[objs[i] for i in sorted(g)] for g in uf.groups()]


def _poi_feature(objs: list[OsmObj], kind: str, source: str) -> Feat:
    t0 = objs[0].tags
    ka = t0["name"].strip()
    ru_osm = weighted_mode((o.tags.get("name:ru"), 1) for o in objs)
    en = weighted_mode((o.tags.get("name:en"), 1) for o in objs)
    geom_ll = _poi_geom_union(objs)
    parsed = parse_name(ka, ru_osm, en)
    ru, gen = (normalize_ru_name(ru_osm), 0) if ru_osm else (generated_ru(ka), 1)
    gxy = to_xy(geom_ll)
    if isinstance(geom_ll, (LineString, MultiLineString)):
        length = float(gxy.length)
    elif kind == "bridge" and isinstance(geom_ll, (Polygon, MultiPolygon)):
        rect = shapely.oriented_envelope(gxy)
        xs, ys = rect.exterior.coords.xy if isinstance(rect, Polygon) else ([0], [0])
        sides = [float(np.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i])) for i in range(len(xs) - 1)]
        length = max(sides) if sides else 0.0
    else:
        length = 0.0
    f = Feat(kind=kind, ka=ka, ru=ru, en=en, ru_generated=gen, parsed=parsed, geom_ll=geom_ll, geom_xy=gxy,
             len_m=round(length, 1), source=source, osm_ids=sorted(o.ref for o in objs),
             wikidata=weighted_mode((o.tags.get("name:etymology:wikidata"), 1) for o in objs))
    for o in objs:
        f.names.update(name_variants(o.tags))
    return f


def _poi_final_kind(tag_kind: str, ka: str) -> str:
    if tag_kind in ("park", "garden"):
        pk = parse_name(ka).kind
        return pk if pk in ("park", "garden") else tag_kind
    return tag_kind


def build_pois(ex: Extract, line_feats: list[Feat]) -> tuple[list[Feat], dict]:
    """Metro, squares, parks/gardens, lakes, bridges; dedupe against highway features."""
    groups: dict[tuple[str, str], list[OsmObj]] = defaultdict(list)
    for o in ex.pois:
        tk = poi_kind(o.tags)
        if tk is None:
            continue
        ka = o.tags["name"].strip()
        key = normalize_ka(ka) if tk == "metro" else ka
        groups[(_poi_final_kind(tk, ka), key)].append(o)
    by_ka: dict[str, list[int]] = defaultdict(list)
    for i, f in enumerate(line_feats):
        if f.source == "highway":
            by_ka[normalize_ka(f.ka)].append(i)
    feats: list[Feat] = []
    st = Counter()
    for (kind, key) in sorted(groups):
        objs = groups[(kind, key)]
        for cl in _cluster(objs, METRO_MERGE_M if kind == "metro" else POI_MERGE_M):
            st["clusters_" + kind] += 1
            if kind in ("square", "bridge"):
                g_xy = to_xy(_poi_geom_union(cl))
                hit = None
                for i in by_ka.get(normalize_ka(cl[0].tags["name"]), []):
                    if line_feats[i].geom_xy.distance(g_xy) <= DEDUPE_M:
                        hit = i
                        break
                if hit is not None:
                    f = line_feats[hit]
                    f.osm_ids = sorted(set(f.osm_ids) | {o.ref for o in cl})
                    for o in cl:
                        f.names.update(name_variants(o.tags))
                    if not f.wikidata:
                        f.wikidata = weighted_mode((o.tags.get("name:etymology:wikidata"), 1) for o in cl)
                    st["attached_" + kind] += 1
                    continue
            feats.append(_poi_feature(cl, kind, "metro" if kind == "metro" else "poi"))
            st["features_" + kind] += 1
    return feats, dict(st)


# ---------------------------------------------------------------------------- families, parents
def assign_families(feats: list[Feat]) -> dict[str, dict]:
    """Set ``f.family_key`` (canonical) and return family rows keyed by canonical key."""
    uniq: dict[str, ParsedName] = {}
    weights: Counter = Counter()
    for f in feats:
        if f.kind in AREA_KINDS:
            continue
        uniq.setdefault(f.parsed.raw, f.parsed)
        weights[f.parsed.family_key] += 1
    canon = group_families(list(uniq.values()), weights=dict(weights)) if uniq else {}
    fams: dict[str, list[Feat]] = defaultdict(list)
    for f in feats:
        if f.kind in AREA_KINDS or not f.parsed.family_key:
            continue
        f.family_key = canon.get(f.parsed.family_key, f.parsed.family_key)
        fams[f.family_key].append(f)
    rows: dict[str, dict] = {}
    for key, members in fams.items():
        prim = sorted(members, key=lambda f: (f.parsed.is_derived, f.parsed.ord is not None,
                                              KIND_GROUP.get(f.kind) != STREET_GROUP, -f.len_m, f.ka))[0]
        p = prim.parsed
        rows[key] = {
            "key": key, "base_ka": p.base_ka or None,
            "base_ru": p.base_ru or weighted_mode((m.parsed.base_ru, 1) for m in members),
            "base_en": p.base_en or weighted_mode((m.parsed.base_en, 1) for m in members),
            "eponym_nom_ka": p.eponym_nom_ka, "kind_hint": parser_hint(p),
        }
    return rows


def parser_hint(p: ParsedName) -> str | None:
    """Parser hint for family.kind_hint (no ':' -- enrichment overwrites with 'kind:sub')."""
    if "route" in p.flags:
        return "route"
    if "non_ka" in p.flags:
        return "non_ka"
    if p.family_key.startswith("#"):
        return "numbered" if re.search(r"\d|[IVXLC]{1,4}\b", p.raw) else "pseudo"
    return None


def assign_parents(feats: list[Feat]) -> dict:
    """Parent street of every derived feature (index into ``feats``)."""
    by_family: dict[str, list[int]] = defaultdict(list)
    for i, f in enumerate(feats):
        if f.family_key:
            by_family[f.family_key].append(i)
    st = Counter()
    for i, f in enumerate(feats):
        p = f.parsed
        if not p.is_derived or not f.family_key:
            continue
        st["derived"] += 1
        members = [j for j in by_family.get(f.family_key, []) if j != i]
        if p.parent_kind:
            cands = [j for j in members if feats[j].kind == p.parent_kind
                     and (p.parent_ord is None or feats[j].parsed.ord == p.parent_ord)]
            if not cands:
                cands = [j for j in members if feats[j].kind == p.parent_kind]
        else:
            cands = [j for j in members if KIND_GROUP.get(feats[j].kind) == STREET_GROUP
                     and not feats[j].parsed.is_derived]
        if not cands:
            cands = [j for j in members if not feats[j].parsed.is_derived and feats[j].kind not in AREA_KINDS]
            if cands:
                st["fallback_kind"] += 1
        if not cands:
            st["no_parent"] += 1
            continue
        shared = [j for j in cands if f.nodes and f.nodes & feats[j].nodes]
        if shared:
            pick = max(shared, key=lambda j: feats[j].len_m)
            how = "node"
        else:
            d = {j: float(f.geom_xy.distance(feats[j].geom_xy)) for j in cands}
            pick = min(d, key=d.get)
            how = "near" if d[pick] <= PARENT_NEAR_M else "far"
        f.parent, f.parent_how = pick, how
        st["parent_" + how] += 1
    return dict(st)


# ---------------------------------------------------------------------------- stage
def build_features(ex: Extract) -> tuple[list[Feat], dict]:
    streets, s1 = build_streets(ex)
    water, s2 = build_waterways(ex)
    pois, s3 = build_pois(ex, streets)
    feats = streets + water + pois
    for f in feats:
        _clean_names(f)
    fam_rows = assign_families(feats)
    s4 = assign_parents(feats)
    stats = {"streets": s1, "water": s2, "pois": s3, "families": len(fam_rows), "parents": s4,
             "kinds": dict(Counter(f.kind for f in feats))}
    return feats, {"stats": stats, "families": fam_rows}


def clear_features(con: sqlite3.Connection) -> None:
    """Delete non-area features (cascades to names, shares, crossings) and families."""
    con.execute("UPDATE house SET feature_id = NULL WHERE feature_id IS NOT NULL")
    con.execute("UPDATE feature SET parent_id = NULL")
    con.execute("DELETE FROM feature WHERE kind NOT IN ('district', 'neighbourhood')")
    con.execute("UPDATE feature SET family_id = NULL")
    con.execute("DELETE FROM family")


def write_features(con: sqlite3.Connection, feats: list[Feat], fam_rows: dict[str, dict]) -> dict:
    prev = {r["key"]: (r["eponym_id"], r["kind_hint"]) for r in
            con.execute("SELECT key, eponym_id, kind_hint FROM family")}
    kept_eponyms = 0
    with tx(con):
        clear_features(con)
        fam_id: dict[str, int] = {}
        for key in sorted(fam_rows):
            r = fam_rows[key]
            ep, hint = None, r["kind_hint"]
            if key in prev:
                ep = prev[key][0]
                if prev[key][1] and ":" in prev[key][1]:
                    hint = prev[key][1]
                if ep is not None:
                    if con.execute("SELECT 1 FROM eponym WHERE id=?", (ep,)).fetchone() is None:
                        ep = None
                    else:
                        kept_eponyms += 1
            cur = con.execute(
                "INSERT INTO family(key, base_ka, base_ru, base_en, eponym_nom_ka, kind_hint, eponym_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (key, r["base_ka"], r["base_ru"], r["base_en"], r["eponym_nom_ka"], hint, ep))
            fam_id[key] = cur.lastrowid
        ids: list[int] = []
        for f in feats:
            lat, lon = representative_point(f.geom_ll)
            cur = con.execute(
                "INSERT INTO feature(kind, ka, ru, en, ru_generated, ord, regnal, family_id, homonym_group, len_m, "
                "lat, lon, bbox, geom_wkt, highway, osm_ids, wikidata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f.kind, f.ka, f.ru, f.en, f.ru_generated, f.parsed.ord, f.parsed.regnal,
                 fam_id.get(f.family_key) if f.family_key else None, f.homonym_group, f.len_m,
                 round(lat, 7), round(lon, 7), bbox_str(f.geom_ll), wkt(f.geom_ll), f.highway,
                 json.dumps(f.osm_ids), f.wikidata))
            ids.append(cur.lastrowid)
        con.executemany("UPDATE feature SET parent_id=? WHERE id=?",
                        [(ids[f.parent], ids[i]) for i, f in enumerate(feats) if f.parent is not None])
        rows = [(ids[i], lang, nm, k, src) for i, f in enumerate(feats) for lang, nm, k, src in sorted(f.names)]
        con.executemany("INSERT OR IGNORE INTO feature_name(feature_id, lang, name, kind, source) VALUES (?,?,?,?,?)",
                        rows)
    return {"features": len(ids), "families": len(fam_id), "feature_names": len(rows),
            "families_kept_eponym": kept_eponyms}


def run(con: sqlite3.Connection, ex: Extract) -> dict:
    with stage(log, "osm.features", highways=len(ex.highways), waterways=len(ex.waterways), pois=len(ex.pois)) as out:
        feats, res = build_features(ex)
        out.update(res["stats"])
        out.update(write_features(con, feats, res["families"]))
        return out
