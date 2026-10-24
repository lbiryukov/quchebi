"""Landmarks stage: museums, theatres, churches, monuments, malls, stadiums ... (kinds of group ``place*``).

Runs after :mod:`pipeline.osm.features` (which wipes all non-area features, landmarks included) and
before ``feature_area`` / ``houses``. Input: ``Extract.landmarks`` -- named nodes, ways and
multipolygons accepted by :func:`pipeline.osm.extract.is_landmark_candidate`.

1. Kind: :func:`pipeline.osm.extract.landmark_kind` with the real polygon area (sports centres and
   residential complexes depend on it), refined by the name for generic kinds (:func:`refine_kind`:
   a ``tourism=attraction`` named "... სასახლე" is a palace). Names that are only a type word
   ("ეკლესია", "Monument") are dropped.
2. Merge (:data:`MERGE_M`): objects with the same name (skeleton of ka, OSM ru or en) and compatible
   kinds (equal, or one of them generic ``landmark``) within 100 m are one feature -- a node inside its
   building, a church node and its ``building=church`` way. The specific kind wins.
3. Duplicates of existing features (park, square, bridge, metro, street of the same name within
   :data:`EXISTING_DEDUPE_M`) and, for generic / residential kinds, of an area of the same name
   within :data:`AREA_DEDUPE_M` are dropped.
4. Geometry: polygons of at least :data:`POLYGON_MIN_M2` keep their outline, everything else is a
   point (representative point of the merged geometry).
5. Parent street (``feature.parent_id``): the street of ``addr:street`` matched with the house
   matching cascade (:func:`pipeline.osm.houses.match_street`, within :data:`ADDR_MAX_M`), else the
   nearest street-like feature within :data:`PARENT_NEAR_M`. The house number itself stays a
   ``house`` row of that street (houses stage; flagged ``landmark:<kind>``).

Names: ka = ``name``; ru = ``name:ru`` (else generated, ``ru_generated = 1``); en = ``name:en``;
other variants (alt / old / official / name:*) go to ``feature_name``. Landmarks have no family.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import shapely
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from pipeline.db import tx
from pipeline.log import get_logger, stage
from pipeline.names import KIND_GROUP, normalize_ka, normalize_ru_name, skeleton
from pipeline.names.parse import KIND_INDEX, KIND_TABLE, LANDMARK_KINDS
from pipeline.osm.extract import landmark_kind
from pipeline.osm.features import UnionFind, generated_ru, name_variants, weighted_mode
from pipeline.osm.geo import bbox_str, representative_point, to_xy, wkt
from pipeline.osm.model import Extract, OsmObj

log = get_logger(__name__)

MERGE_M = 100.0              # same name + compatible kind closer than this: one landmark
EXISTING_DEDUPE_M = 200.0    # same name as a park / square / bridge / metro / street feature
AREA_DEDUPE_M = 1500.0       # same name as a district / neighbourhood (generic and residential kinds)
POLYGON_MIN_M2 = 3000.0      # smaller outlines are shipped as points
RESIDENTIAL_MAX_M2 = 300000.0  # a bigger named landuse=residential is a neighbourhood, not a complex
PARENT_NEAR_M = 50.0
ADDR_MAX_M = 300.0
GENERIC = "landmark"
STREET_GROUPS = ("street*", "lane*")
ADDR_TAGS = ("addr:street", "addr:street:ka", "addr:street:en", "addr:street:ru")

#: Name-based refinement of generic kinds: (pattern on ka / ru / en, kind). Order matters.
_REFINE: list[tuple[re.Pattern, str]] = [
    (re.compile(r"სასახლ|дворец|palace", re.I), "palace"),
    (re.compile(r"ციხე|ციხის|ციხესიმაგრ|крепост|fortress|castle|citadel", re.I), "fortress"),
    (re.compile(r"მუზეუმ|музей|museum", re.I), "museum"),
    (re.compile(r"კინოთეატრ|кинотеатр|cinema", re.I), "cinema"),
    (re.compile(r"თეატრ|театр|theatre|theater", re.I), "theatre"),
    (re.compile(r"ეკლესი|ტაძარ|მონასტერ|სამლოცველო|церк|храм|собор|монастыр|church|cathedral|monastery", re.I),
     "church"),
    (re.compile(r"აბანო|бан[яи]|bath", re.I), "bath"),
    (re.compile(r"სასაფლაო|პანთეონ|кладбищ|пантеон|cemetery|pantheon", re.I), "cemetery"),
]
#: Kinds whose tag-based value may be refined by the name.
REFINABLE = {GENERIC: None, "fortress": {"palace"}}

#: A name that is only a type word is not a name ("ეკლესია", "Statue").
GENERIC_NAMES = {w.casefold() for k in KIND_TABLE if k.code in LANDMARK_KINDS for w in (*k.ka, k.ru, *k.syn)}
GENERIC_NAMES |= {"church", "chapel", "monument", "memorial", "statue", "bust", "sculpture", "museum", "school",
                  "library", "cemetery", "mosque", "synagogue", "theatre", "cinema", "stadium", "market", "mall",
                  "station", "fountain", "tower", "ruins", "plaque", "სამლოცველო", "წყარო", "შადრევანი",
                  "საჯარო სკოლა", "კერძო სკოლა", "памятник", "церковь", "часовня"}


@dataclass
class Cand:
    obj: OsmObj
    kind: str
    ka: str
    keys: set[str]                 # skeletons of ka / OSM ru / en
    geom_xy: BaseGeometry
    area_m2: float


@dataclass
class Landmark:
    kind: str
    ka: str
    ru: str | None
    en: str | None
    ru_generated: int
    geom_ll: BaseGeometry
    osm_ids: list[str]
    objs: list[OsmObj]
    wikidata: str | None = None
    names: set[tuple[str, str, str, str]] = field(default_factory=set)
    parent: int | None = None
    parent_how: str | None = None


# ---------------------------------------------------------------------------- classification
def refine_kind(kind: str, tags: dict[str, str]) -> str:
    """Generic kinds refined by the words of the name (ka, name:ru, name:en)."""
    if kind not in REFINABLE:
        return kind
    allowed = REFINABLE[kind]
    text = " ".join(v for v in (tags.get("name"), tags.get("name:ru"), tags.get("name:en")) if v)
    for rx, k in _REFINE:
        if (allowed is None or k in allowed) and rx.search(text):
            return k
    return kind


#: Names that only label a part of a complex ("Block A", "Hall 3", "12ბ", "023").
_PART_NAME = re.compile(r"^\W*(?P<pre>(?:hall|block|building|корпус|блок|зал|კორპუსი|ბლოკი|შენობა|დარბაზი)\s*)?"
                        r"(?P<core>\w{0,4})(?P<post>\s*(?:block|ბლოკი|корпус|блок|კორპუსი))?\W*$", re.IGNORECASE)
#: Kinds whose names are person names: never a duplicate of a street / metro named after the same person.
PERSON_NAMED = {"monument", "plaque"}


def is_part_label(name: str) -> bool:
    """"Block A", "Hall 3", "12ბ", "023", "B": a label of a part of a complex, not a name."""
    m = _PART_NAME.match(name.strip())
    if not m:
        return False
    core = m.group("core")
    return bool(m.group("pre") or m.group("post")) or len(core) <= 2 or any(ch.isdigit() for ch in core)


def is_generic_name(name: str) -> bool:
    """Only a type word ("ეკლესია", "Monument") or only a part label ("Block A", "12ბ")."""
    return normalize_ka(name).casefold().strip(" .") in GENERIC_NAMES or is_part_label(name)


def _area_m2(g_xy: BaseGeometry) -> float:
    return float(g_xy.area) if isinstance(g_xy, (Polygon, MultiPolygon)) else 0.0


def name_keys(tags: dict[str, str]) -> set[str]:
    out = set()
    for k in ("name", "name:ru", "name:en"):
        v = (tags.get(k) or "").strip()
        if v:
            sk = skeleton(normalize_ka(v)) if k == "name" else skeleton(v)
            if sk:
                out.add(sk)
    return out


def classify(objs: list[OsmObj]) -> tuple[list[Cand], Counter]:
    st: Counter = Counter()
    out: list[Cand] = []
    for o in objs:
        name = (o.tags.get("name") or "").strip()
        if not name:
            continue
        g_xy = to_xy(o.geom)
        area = _area_m2(g_xy)
        k = landmark_kind(o.tags, area)
        if k is None:
            st["skip_small_or_untyped"] += 1
            continue
        k = refine_kind(k, o.tags)
        if is_generic_name(name) and k != "station":  # "2 კმ" is a real railway halt
            st["skip_generic_name"] += 1
            continue
        if k == "residential" and area > RESIDENTIAL_MAX_M2:
            st["skip_residential_too_big"] += 1
            continue
        out.append(Cand(o, k, name, name_keys(o.tags), g_xy, area))
        st["cand_" + k] += 1
    return out, st


def _compatible(a: str, b: str) -> bool:
    return a == b or a == GENERIC or b == GENERIC


def cluster(cands: list[Cand], dist_m: float = MERGE_M) -> list[list[Cand]]:
    """Single linkage over pairs with a common name key, compatible kinds and distance <= dist_m."""
    if not cands:
        return []
    geoms = [c.geom_xy for c in cands]
    tree = shapely.STRtree(geoms)
    a, b = tree.query(geoms, predicate="dwithin", distance=dist_m)
    uf = UnionFind(len(cands))
    for i, j in zip(a.tolist(), b.tolist()):
        if i < j and cands[i].keys & cands[j].keys and _compatible(cands[i].kind, cands[j].kind):
            uf.union(i, j)
    return [[cands[i] for i in sorted(g)] for g in uf.groups()]


def _cluster_kind(cl: list[Cand]) -> str:
    specific = Counter(c.kind for c in cl if c.kind != GENERIC)
    if not specific:
        return GENERIC
    best = max(specific.values())
    return min((k for k, v in specific.items() if v == best), key=lambda k: KIND_INDEX[k])


def _geometry(cl: list[Cand]) -> BaseGeometry:
    """Outline of the largest polygon when big enough, else a representative point (WGS84)."""
    polys = [c for c in cl if c.area_m2 > 0]
    if polys:
        big = max(polys, key=lambda c: c.area_m2)
        if big.area_m2 >= POLYGON_MIN_M2:
            g = big.obj.geom
            return g if g.is_valid else shapely.make_valid(g)
        lat, lon = representative_point(big.obj.geom)
        return Point(lon, lat)
    pts = [c for c in cl if isinstance(c.obj.geom, Point)]
    if pts:
        p = pts[0].obj.geom
        return Point(p.x, p.y)
    g = cl[0].obj.geom
    if isinstance(g, (LineString, MultiLineString)):
        lines = list(g.geoms) if isinstance(g, MultiLineString) else [g]
        longest = max(lines, key=lambda ln: to_xy(ln).length)
        p = longest.interpolate(0.5, normalized=True)
        return Point(p.x, p.y)
    lat, lon = representative_point(g)
    return Point(lon, lat)


def _wikidata(objs: list[OsmObj]) -> str | None:
    for key in ("name:etymology:wikidata", "subject:wikidata"):
        v = weighted_mode((o.tags.get(key), 1) for o in objs)
        if v:
            return v
    return None


def build_landmark(cl: list[Cand]) -> Landmark:
    objs = [c.obj for c in cl]
    # primary object: the specific kind, then polygons, then the longest name list
    kind = _cluster_kind(cl)
    prim = sorted(cl, key=lambda c: (c.kind != kind, c.area_m2 == 0, -len(c.obj.tags), c.obj.ref))[0]
    ka = prim.ka
    ru_osm = prim.obj.tags.get("name:ru") or weighted_mode((o.tags.get("name:ru"), 1) for o in objs)
    en = prim.obj.tags.get("name:en") or weighted_mode((o.tags.get("name:en"), 1) for o in objs)
    ru, gen = (normalize_ru_name(ru_osm), 0) if ru_osm else (generated_ru(ka), 1)
    lm = Landmark(kind=kind, ka=ka, ru=ru, en=en, ru_generated=gen, geom_ll=_geometry(cl),
                  osm_ids=sorted({o.ref for o in objs}), objs=objs, wikidata=_wikidata(objs))
    for o in objs:
        lm.names.update(name_variants(o.tags))
        nm = (o.tags.get("name") or "").strip()
        if nm and nm != ka:
            lm.names.add(("ka" if re.search(r"[ა-ჰ]", nm) else "lat", nm, "alt", "osm:name"))
    primary = {lm.ka, lm.en} | ({lm.ru} if not lm.ru_generated else set())
    lm.names = {t for t in lm.names if t[1] and t[1] not in primary
                and not (t[0] == "ru" and not lm.ru_generated and normalize_ru_name(t[1]) == lm.ru)}
    return lm


# ---------------------------------------------------------------------------- duplicates of features / areas
def _name_keys_row(*names: str | None) -> set[str]:
    return {sk for sk in (skeleton(normalize_ka(n)) for n in names if n) if sk}


class ExistingIndex:
    """Non-landmark features and areas, for the duplicate check (projected)."""

    def __init__(self, con: sqlite3.Connection):
        ph = ",".join("?" * len(LANDMARK_KINDS))
        rows = con.execute(f"SELECT id, kind, ka, ru, en, geom_wkt FROM feature WHERE geom_wkt IS NOT NULL "
                           f"AND kind NOT IN ({ph}) AND kind NOT IN ('district', 'neighbourhood')",
                           sorted(LANDMARK_KINDS)).fetchall()
        self.feat: dict[str, list[tuple[int, str, BaseGeometry]]] = defaultdict(list)
        for r in rows:
            g = to_xy(shapely.from_wkt(r["geom_wkt"]))
            for k in _name_keys_row(r["ka"], r["ru"], r["en"]):
                self.feat[k].append((r["id"], r["kind"], g))
        self.area: dict[str, list[tuple[int, BaseGeometry]]] = defaultdict(list)
        for r in con.execute("SELECT id, ka, ru, en, lat, lon, geom_wkt FROM area").fetchall():
            g = shapely.from_wkt(r["geom_wkt"]) if r["geom_wkt"] else (
                Point(r["lon"], r["lat"]) if r["lat"] is not None else None)
            if g is None:
                continue
            g = to_xy(g)
            for k in _name_keys_row(r["ka"], r["ru"], r["en"]):
                self.area[k].append((r["id"], g))

    def duplicate_of(self, lm: Landmark) -> str | None:
        """Kind of an existing feature (or "area") this landmark duplicates, else None.

        Same Georgian name (any name for the generic kind) within EXISTING_DEDUPE_M; monuments and
        plaques are named after persons and are never duplicates of a street or a metro station.
        """
        g = to_xy(lm.geom_ll)
        keys = _name_keys_row(lm.ka, None if lm.ru_generated else lm.ru, lm.en)
        fkeys = keys if lm.kind == GENERIC else _name_keys_row(lm.ka)
        for k in fkeys:
            for _fid, kind, fg in self.feat.get(k, []):
                if lm.kind in PERSON_NAMED and KIND_GROUP.get(kind) not in STREET_GROUPS:
                    continue  # a bust of X next to the metro station "X"; a plaque "X ქუჩა" is a street sign
                if fg.distance(g) <= EXISTING_DEDUPE_M:
                    return kind
        if lm.kind in (GENERIC, "residential"):
            for k in keys:
                for _aid, ag in self.area.get(k, []):
                    if ag.distance(g) <= AREA_DEDUPE_M:
                        return "area"
        return None


# ---------------------------------------------------------------------------- parent street
class StreetFinder:
    """Street matching for landmark addresses (the houses cascade) and nearest-street fallback."""

    def __init__(self, con: sqlite3.Connection):
        from pipeline.osm.houses import StreetIndex  # local import: houses imports this module's kinds

        self.idx = StreetIndex(con)
        self.street_like = {fid for fid in self.idx.fids
                            if KIND_GROUP.get(self.idx.kind[fid]) in STREET_GROUPS or self.idx.kind[fid] == "square"}

    def by_address(self, street: str, p_xy: Point) -> tuple[int | None, str, float | None]:
        from pipeline.osm.houses import match_street

        how, fid, d = match_street(self.idx, street, p_xy)
        if fid is None or d is None or d > ADDR_MAX_M:
            return None, how, d
        return fid, how, d

    def nearest(self, g_xy: BaseGeometry, radius: float = PARENT_NEAR_M) -> tuple[int | None, float | None]:
        if self.idx.tree is None:
            return None, None
        cand = [i for i in self.idx.tree.query(g_xy, predicate="dwithin", distance=radius).tolist()
                if self.idx.fids[i] in self.street_like]
        if not cand:
            return None, None
        d = shapely.distance(self.idx.geoms[cand], g_xy)
        j = int(d.argmin())
        return self.idx.fids[cand[j]], float(d[j])


def address_of(lm: Landmark) -> tuple[str | None, str | None]:
    """(addr:street, addr:housenumber) of the first object that has a street."""
    for o in sorted(lm.objs, key=lambda o: ("addr:housenumber" not in o.tags, o.ref)):
        for k in ADDR_TAGS:
            v = (o.tags.get(k) or "").strip()
            if v:
                return v, (o.tags.get("addr:housenumber") or "").strip() or None
    return None, None


def assign_parents(con: sqlite3.Connection, lms: list[Landmark]) -> dict:
    finder = StreetFinder(con)
    st: Counter = Counter()
    for lm in lms:
        g_xy = to_xy(lm.geom_ll)
        p = g_xy if isinstance(g_xy, Point) else g_xy.representative_point()
        street, num = address_of(lm)
        if street:
            st["with_addr_street"] += 1
            fid, how, d = finder.by_address(street, p)
            if fid is not None:
                lm.parent, lm.parent_how = fid, "addr"
                st["parent_addr"] += 1
                st["addr_" + how] += 1
                continue
            st["addr_unmatched"] += 1
        fid, d = finder.nearest(g_xy)
        if fid is not None:
            lm.parent, lm.parent_how = fid, "near"
            st["parent_near"] += 1
        else:
            st["no_parent"] += 1
    return dict(st)


# ---------------------------------------------------------------------------- stage
def build_landmarks(con: sqlite3.Connection, ex: Extract) -> tuple[list[Landmark], dict]:
    cands, st = classify(ex.landmarks)
    clusters = cluster(cands)
    st["clusters"] = len(clusters)
    st["merged_objects"] = len(cands) - len(clusters)
    existing = ExistingIndex(con)
    out: list[Landmark] = []
    samples: dict[str, list[str]] = defaultdict(list)
    for cl in sorted(clusters, key=lambda c: c[0].obj.ref):
        lm = build_landmark(cl)
        dup = existing.duplicate_of(lm)
        if dup:
            st["dup_" + dup] += 1
            if len(samples[dup]) < 5:
                samples[dup].append(lm.ka)
            continue
        out.append(lm)
    out.sort(key=lambda lm: (KIND_INDEX[lm.kind], lm.ka, lm.osm_ids))
    stats = {k: v for k, v in sorted(st.items())}
    stats["kinds"] = dict(sorted(Counter(lm.kind for lm in out).items(), key=lambda kv: KIND_INDEX[kv[0]]))
    stats["polygons"] = sum(isinstance(lm.geom_ll, (Polygon, MultiPolygon)) for lm in out)
    stats["dup_samples"] = dict(samples)
    stats["parents"] = assign_parents(con, out)
    return out, stats


def write_landmarks(con: sqlite3.Connection, lms: list[Landmark]) -> dict:
    ph = ",".join("?" * len(LANDMARK_KINDS))
    with tx(con):
        removed = con.execute(f"DELETE FROM feature WHERE kind IN ({ph})", sorted(LANDMARK_KINDS)).rowcount
        rows = []
        for lm in lms:
            lat, lon = representative_point(lm.geom_ll)
            cur = con.execute(
                "INSERT INTO feature(kind, ka, ru, en, ru_generated, ord, regnal, family_id, parent_id, homonym_group, "
                "len_m, lat, lon, bbox, geom_wkt, highway, osm_ids, wikidata) "
                "VALUES (?,?,?,?,?,NULL,NULL,NULL,?,NULL,0,?,?,?,?,NULL,?,?)",
                (lm.kind, lm.ka, lm.ru, lm.en, lm.ru_generated, lm.parent, round(lat, 7), round(lon, 7),
                 bbox_str(lm.geom_ll), wkt(lm.geom_ll), json.dumps(lm.osm_ids), lm.wikidata))
            fid = cur.lastrowid
            rows += [(fid, lang, nm, k, src) for lang, nm, k, src in sorted(lm.names)]
        con.executemany("INSERT OR IGNORE INTO feature_name(feature_id, lang, name, kind, source) VALUES (?,?,?,?,?)",
                        rows)
    return {"landmarks": len(lms), "removed_old": removed, "feature_names": len(rows)}


def run(con: sqlite3.Connection, ex: Extract) -> dict:
    with stage(log, "osm.landmarks", candidates=len(ex.landmarks)) as out:
        lms, st = build_landmarks(con, ex)
        out.update(st)
        out.update(write_landmarks(con, lms))
        log.info("landmarks by kind: %s", st["kinds"])
        return out


__all__ = ["run", "build_landmarks", "write_landmarks", "refine_kind", "classify", "cluster", "is_generic_name",
           "LANDMARK_KINDS"]
