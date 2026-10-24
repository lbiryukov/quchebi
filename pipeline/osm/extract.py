"""Read OSM data for Tbilisi into an :class:`~pipeline.osm.model.Extract`.

Primary source: the Geofabrik PBF read with pyosmium in two passes (relations, then nodes and
ways with a node-location cache). The Tbilisi boundary is the relation
``boundary=administrative admin_level=4 name:en=Tbilisi`` from the same file.

Fallback: the Overpass cache ``data/raw/prototype_cache/overpass/*/*.json`` (``out body geom``);
there the boundary is the union of the district polygons.

All downloaded content is treated as untrusted data: tags are copied as plain strings,
geometries are validated, broken rings are dropped and counted.
"""
from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from pathlib import Path

import shapely
from shapely.geometry import LineString, MultiLineString, Point, Polygon

from pipeline.config import INTERIM, TBILISI_BBOX
from pipeline.log import get_logger, stage
from pipeline.osm.geo import as_multipolygon, polygon_from_rings
from pipeline.osm.model import EXTRACT_VERSION, Extract, OsmObj, OsmWay

log = get_logger(__name__)

PLACE_KINDS = ("suburb", "neighbourhood", "quarter")
NON_LAKE_WATER = {"river", "stream", "canal", "ditch", "drain", "wastewater", "riverbank", "fountain",
                  "moat", "lock", "stream_pool"}
BBOX_MARGIN = 0.03  # degrees around config.TBILISI_BBOX for the coarse pre-filter
EXTRACT_CACHE = INTERIM / "osm_extract.json"


# ---------------------------------------------------------------------------- classification
def poi_kind(t: dict[str, str]) -> str | None:
    """Tag-based kind of a named non-street object (None if not imported)."""
    if not t.get("name"):
        return None
    if t.get("station") == "subway" or (t.get("railway") == "station" and t.get("subway") == "yes"):
        if t.get("railway") in ("subway_entrance", "platform", "stop") or t.get("public_transport") == "platform":
            return None
        return "metro"
    if t.get("place") == "square":
        return "square"
    if t.get("leisure") == "park":
        return "park"
    if t.get("leisure") == "garden":
        return "garden"
    if t.get("natural") == "water" and t.get("water", "lake") not in NON_LAKE_WATER:
        return "lake"
    if t.get("landuse") == "reservoir" and t.get("water", "reservoir") not in NON_LAKE_WATER:
        return "lake"
    if t.get("man_made") == "bridge":
        return "bridge"
    return None


#: Keys that make a named object a landmark candidate (see :func:`landmark_kind`).
LANDMARK_KEYS = ("tourism", "amenity", "historic", "leisure", "shop", "railway", "building", "aerialway",
                 "landuse", "memorial", "man_made", "public_transport")
PLAQUE_MEMORIALS = {"plaque", "blue_plaque", "stolperstein"}
MONUMENT_ARTWORK = {"statue", "sculpture", "bust", "monument", "stone", "obelisk"}
WORSHIP_BUILDINGS = {"church": "church", "cathedral": "church", "chapel": "church", "monastery": "church",
                     "temple": "church", "basilica": "church", "mosque": "mosque", "synagogue": "synagogue"}
WORSHIP_HISTORIC = {"church": "church", "monastery": "church", "chapel": "church", "cathedral": "church",
                    "temple": "church", "mosque": "mosque", "synagogue": "synagogue"}
GENERIC_TOURISM = {"attraction", "viewpoint", "zoo", "theme_park", "aquarium"}
GENERIC_HISTORIC = {"building", "house", "ruins", "archaeological_site", "yes", "city_gate", "tomb", "manor",
                    "caravanserai", "tower", "gate", "aqueduct", "heritage", "monument_ensemble", "fountain",
                    "bridge", "palace"}
GENERIC_AMENITY = {"arts_centre", "exhibition_centre", "planetarium", "fountain", "townhall"}
NON_STOP_RAILWAY = {"station", "halt"}
#: A sports centre or pitch is a "stadium" when its name says so (or it is a large area).
_STADIUM_NAME = re.compile(r"სტადიონ|არენა|სასახლე|სპორტული კომპლექს|stadium|arena|palace|sports complex|"
                           r"стадион|арена|дворец|спорткомплекс", re.IGNORECASE)
STADIUM_MIN_M2 = 5000.0


def landmark_kind(t: dict[str, str], area_m2: float = 0.0) -> str | None:
    """Tag-based landmark kind (group ``place*`` of KIND_TABLE) of a named object, None if not one.

    ``area_m2`` is the polygon area (0 for points and lines; ``math.inf`` = "any size", used by the
    extractor to keep candidates whose kind depends on the size). Streets, water and the POIs of
    :func:`poi_kind` (metro, squares, parks, lakes, bridges) are never landmarks. The first rule that
    matches wins; :func:`pipeline.osm.landmarks.refine_kind` may refine a generic kind by the name.
    """
    name = (t.get("name") or "").strip()
    if not name:
        return None
    if "highway" in t or "waterway" in t or "boundary" in t or "place" in t or poi_kind(t):
        return None
    if t.get("public_transport") in ("platform", "stop_position", "stop_area") or \
            t.get("railway") in ("platform", "stop", "subway_entrance", "tram_stop"):
        return None
    am, bl, hi, tu = t.get("amenity"), t.get("building"), t.get("historic"), t.get("tourism")
    le, sh, lu, mem, art = t.get("leisure"), t.get("shop"), t.get("landuse"), t.get("memorial"), t.get("artwork_type")
    if mem in PLAQUE_MEMORIALS or (tu == "artwork" and art == "star"):
        return "plaque"
    if am in ("place_of_worship", "monastery") or bl in WORSHIP_BUILDINGS or hi in WORSHIP_HISTORIC \
            or lu == "religious":
        rel = t.get("religion")
        if rel == "muslim" or bl == "mosque" or hi == "mosque":
            return "mosque"
        if rel == "jewish" or bl == "synagogue" or hi == "synagogue":
            return "synagogue"
        return "church"
    if tu in ("museum", "gallery") or am == "museum" or bl == "museum":
        return "museum"
    if am == "cinema" or bl == "cinema":
        return "cinema"
    if am in ("theatre", "concert_hall", "circus") or bl == "theatre":
        return "theatre"
    if am == "public_bath":
        return "bath"
    if hi in ("monument", "memorial") or (tu == "artwork" and art in MONUMENT_ARTWORK):
        return "monument"
    if hi in ("castle", "fort", "citywalls", "fortress") or bl in ("castle", "fortress"):
        return "fortress"
    if bl == "palace":
        return "palace"
    if le == "stadium" or bl == "stadium":
        return "stadium"
    if le in ("sports_centre", "sports_hall") or bl in ("sports_centre", "sports_hall"):
        return "stadium" if (_STADIUM_NAME.search(name) or area_m2 >= STADIUM_MIN_M2) else None
    if le == "pitch":
        return "stadium" if _STADIUM_NAME.search(name) else None
    if sh in ("mall", "department_store"):
        return "mall"
    if am == "marketplace":
        return "market"
    if lu == "retail" and not am and not sh:
        return "mall"
    if (t.get("railway") in NON_STOP_RAILWAY and t.get("station") not in ("subway", "light_rail")
            and t.get("subway") != "yes") or am == "bus_station" or t.get("aerialway") == "station" \
            or bl == "train_station":
        return "station"
    if am in ("university", "college", "research_institute") or bl in ("university", "college"):
        return "university"
    if am == "school" or bl == "school":
        return "school"
    if am == "library":
        return "library"
    if lu == "cemetery" or am == "grave_yard":
        return "cemetery"
    if lu == "industrial" or t.get("man_made") == "works":
        return "factory"
    if lu == "residential" or bl in ("apartments", "residential"):
        return "residential" if area_m2 > 0 else None
    if tu in GENERIC_TOURISM or hi in GENERIC_HISTORIC or am in GENERIC_AMENITY \
            or (tu == "artwork" and art not in ("graffiti",)) \
            or t.get("man_made") in ("tower", "communications_tower", "obelisk"):
        return "landmark"
    return None


def is_landmark_candidate(t: dict[str, str]) -> bool:
    """Named object that may be a landmark (its kind may still depend on the polygon area)."""
    return landmark_kind(t, math.inf) is not None


def is_place(t: dict[str, str]) -> bool:
    return t.get("place") in PLACE_KINDS and bool(t.get("name"))


def is_district(t: dict[str, str]) -> bool:
    return t.get("boundary") == "administrative" and t.get("admin_level") == "10"


def is_tbilisi(t: dict[str, str]) -> bool:
    return (t.get("boundary") == "administrative" and t.get("admin_level") == "4"
            and t.get("name:en") == "Tbilisi")


def _in_bbox(lat: float, lon: float, m: float = BBOX_MARGIN) -> bool:
    lon0, lat0, lon1, lat1 = TBILISI_BBOX
    return lat0 - m <= lat <= lat1 + m and lon0 - m <= lon <= lon1 + m


def _closed_way_geom(coords: list[tuple[float, float]], nodes: list[int]):
    """Polygon for a closed way with >= 4 nodes, else LineString (lon/lat)."""
    if len(coords) >= 4 and nodes and nodes[0] == nodes[-1]:
        p = Polygon([(lon, lat) for lat, lon in coords])
        if not p.is_valid:
            p = as_multipolygon(shapely.make_valid(p))
        if not p.is_empty:
            return p
    if len(coords) >= 2:
        return LineString([(lon, lat) for lat, lon in coords])
    if coords:
        return Point(coords[0][1], coords[0][0])
    return None


def _centroid(g) -> Point | None:
    if g is None or g.is_empty:
        return None
    c = g.centroid
    return None if c.is_empty else c


# ---------------------------------------------------------------------------- Tbilisi filter
def clip_to_boundary(ex: Extract) -> Extract:
    """Keep only objects inside / intersecting ``ex.boundary``; logs before/after counts."""
    b = ex.boundary
    if b is None:
        raise ValueError("extract has no boundary")
    shapely.prepare(b)
    before = ex.counts()

    def keep_ways(ws):
        if not ws:
            return ws
        lines = [LineString([(lon, lat) for lat, lon in w.coords]) if len(w.coords) >= 2
                 else Point(w.coords[0][1], w.coords[0][0]) for w in ws]
        mask = shapely.intersects(b, lines)
        return [w for w, m in zip(ws, mask) if m]

    def keep_objs(os_, how):
        if not os_:
            return os_
        geoms = [o.geom for o in os_]
        if how == "point":
            pts = [g.representative_point() if not isinstance(g, Point) else g for g in geoms]
            mask = shapely.contains(b, pts) | shapely.touches(b, pts)
        else:
            mask = shapely.intersects(b, geoms)
        return [o for o, m in zip(os_, mask) if m]

    ex.highways = keep_ways(ex.highways)
    ex.waterways = keep_ways(ex.waterways)
    ex.districts = keep_objs(ex.districts, "point")
    ex.places = keep_objs(ex.places, "point")
    ex.pois = keep_objs(ex.pois, "intersects")
    ex.addresses = keep_objs(ex.addresses, "point")
    ex.landmarks = keep_objs(ex.landmarks, "point")
    log.info("clip to Tbilisi: before %s after %s", before, ex.counts())
    return ex


# ---------------------------------------------------------------------------- PBF
def read_pbf(path: Path, snapshot: str | None) -> Extract:
    """Two-pass pyosmium read of the Georgia PBF, limited to Tbilisi."""
    import osmium

    def tags_of(o) -> dict[str, str]:
        return {tg.k: tg.v for tg in o.tags}

    with stage(log, "osm.extract.relations", file=path.name) as out:
        rels: dict[int, dict] = {}
        member_ways: set[int] = set()
        nrel = 0
        for r in osmium.FileProcessor(str(path), osmium.osm.RELATION):
            nrel += 1
            t = r.tags
            if not ("boundary" in t or "place" in t or "addr:housenumber" in t or "name" in t):
                continue
            tags = tags_of(r)
            role_kind = None
            if is_tbilisi(tags):
                role_kind = "tbilisi"
            elif is_district(tags):
                role_kind = "district"
            elif is_place(tags):
                role_kind = "place"
            elif poi_kind(tags) and tags.get("type") in ("multipolygon", "boundary", None):
                role_kind = "poi"
            elif is_landmark_candidate(tags) and tags.get("type") in ("multipolygon", None):
                role_kind = "landmark"  # also an address below when it has addr:housenumber
            elif "addr:housenumber" in tags and tags.get("type") in ("multipolygon", "building", None):
                role_kind = "address"
            if role_kind is None:
                continue
            members = [(m.ref, m.role) for m in r.members if m.type == "w"]
            rels[r.id] = {"kind": role_kind, "tags": tags, "members": members}
            member_ways.update(ref for ref, _ in members)
        out.update(relations=nrel, kept=len(rels), member_ways=len(member_ways),
                   by_kind=dict(Counter(v["kind"] for v in rels.values())))

    tb = [rid for rid, v in rels.items() if v["kind"] == "tbilisi"]
    if not tb:
        raise RuntimeError("Tbilisi boundary relation (admin_level=4, name:en=Tbilisi) not found in PBF")
    if len(tb) > 1:
        log.warning("several Tbilisi boundary relations: %s; using the first", tb)

    # pass 2: node lists of relation member ways (ways only, no locations: fast)
    member_nodes: dict[int, list[int]] = {}
    if member_ways:
        with stage(log, "osm.extract.member_ways", wanted=len(member_ways)) as out:
            fp = (osmium.FileProcessor(str(path), osmium.osm.WAY)
                  .with_filter(osmium.filter.IdFilter(member_ways).enable_for(osmium.osm.WAY)))
            for w in fp:
                member_nodes[w.id] = [n.ref for n in w.nodes]
            out["found"] = len(member_nodes)

    ex = Extract(source="pbf", snapshot=snapshot, boundary=None,
                 info={"file": path.name, "bytes": path.stat().st_size})
    way_coords: dict[int, list[tuple[float, float]]] = {}
    # pass 3: tagged nodes and candidate ways with a node-location cache; C++ filters keep the
    # Python loop to named / addressed objects
    with stage(log, "osm.extract.nodes_ways", file=path.name) as out:
        cnt = Counter()
        node_keys = tuple(dict.fromkeys(("addr:housenumber", "place", "station", "railway", "leisure", "natural",
                                         "man_made", "landuse") + LANDMARK_KEYS))
        way_keys = tuple(dict.fromkeys(("highway", "waterway", "addr:housenumber", "place", "leisure", "natural",
                                        "man_made", "station", "railway", "landuse") + LANDMARK_KEYS))
        fp = (osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY)
              .with_locations()
              .with_filter(osmium.filter.KeyFilter(*node_keys).enable_for(osmium.osm.NODE))
              .with_filter(osmium.filter.KeyFilter("name", "addr:housenumber").enable_for(osmium.osm.WAY))
              .with_filter(osmium.filter.KeyFilter(*way_keys).enable_for(osmium.osm.WAY)))
        for o in fp:
            if o.is_node():
                cnt["nodes"] += 1
                loc = o.location
                if not loc.valid() or not _in_bbox(loc.lat, loc.lon):
                    continue
                tags = tags_of(o)
                g = Point(loc.lon, loc.lat)
                if "addr:housenumber" in tags:
                    ex.addresses.append(OsmObj("n", o.id, tags, g))
                    cnt["addr_nodes"] += 1
                if is_place(tags):
                    ex.places.append(OsmObj("n", o.id, tags, g))
                k = poi_kind(tags)
                if k:
                    ex.pois.append(OsmObj("n", o.id, tags, g))
                elif "name" in tags and is_landmark_candidate(tags):
                    ex.landmarks.append(OsmObj("n", o.id, tags, g))
                    cnt["landmark_nodes"] += 1
                continue
            cnt["ways"] += 1
            t = o.tags
            wid = o.id
            named = "name" in t
            is_hw = named and "highway" in t
            is_ww = named and "waterway" in t
            has_addr = "addr:housenumber" in t
            maybe_area = named and ("place" in t or "leisure" in t or "natural" in t or "man_made" in t
                                    or "station" in t or "railway" in t or "landuse" in t)
            maybe_lm = named and not is_hw and not is_ww and any(k in t for k in LANDMARK_KEYS)
            if not (is_hw or is_ww or has_addr or maybe_area or maybe_lm):
                continue
            nodes, coords = [], []
            for n in o.nodes:
                loc = n.location
                if loc.valid():
                    nodes.append(n.ref)
                    coords.append((loc.lat, loc.lon))
            if not coords:
                cnt["ways_no_coords"] += 1
                continue
            if not any(_in_bbox(a, b) for a, b in coords[:: max(1, len(coords) // 8)] + [coords[-1]]):
                continue
            cnt["ways_in_bbox"] += 1
            tags = tags_of(o)
            if is_hw:
                ex.highways.append(OsmWay(wid, tags, nodes, coords))
            if is_ww:
                ex.waterways.append(OsmWay(wid, tags, nodes, coords))
            if has_addr or maybe_area or maybe_lm:
                g = _closed_way_geom(coords, nodes)
                if g is None:
                    continue
                if maybe_lm and not poi_kind(tags) and is_landmark_candidate(tags):
                    ex.landmarks.append(OsmObj("w", wid, tags, g))
                    cnt["landmark_ways"] += 1
                if has_addr:
                    c = _centroid(g)
                    if c is not None:
                        ex.addresses.append(OsmObj("w", wid, tags, c))
                if is_place(tags) and g.geom_type in ("Polygon", "MultiPolygon"):
                    ex.places.append(OsmObj("w", wid, tags, g))
                if poi_kind(tags):
                    ex.pois.append(OsmObj("w", wid, tags, g))
        store = fp.node_location_storage
        missing_nodes = 0
        for wid, refs in member_nodes.items():
            coords = []
            for ref in refs:
                try:
                    loc = store.get(ref)
                except (KeyError, RuntimeError):
                    missing_nodes += 1
                    continue
                if loc.valid():
                    coords.append((loc.lat, loc.lon))
                else:
                    missing_nodes += 1
            if coords:
                way_coords[wid] = coords
        cnt["member_ways_with_coords"] = len(way_coords)
        cnt["member_nodes_missing"] = missing_nodes
        out.update(cnt)

    with stage(log, "osm.extract.relation_geoms", relations=len(rels)) as out:
        bad = Counter()
        tb_geom = None
        for rid, v in rels.items():
            outer = [way_coords[ref] for ref, role in v["members"] if role in ("outer", "") and ref in way_coords]
            inner = [way_coords[ref] for ref, role in v["members"] if role == "inner" and ref in way_coords]
            missing = sum(1 for ref, _ in v["members"] if ref not in way_coords)
            if v["kind"] != "tbilisi":
                # cheap bbox test before ring assembly
                pts = [p for line in outer for p in line[:: max(1, len(line) // 4)]]
                if not pts or not any(_in_bbox(a, b) for a, b in pts):
                    continue
            g, nbad = polygon_from_rings(outer, inner)
            if nbad or missing:
                bad[v["kind"]] += 1
                log.debug("relation r%d (%s %s): %d open rings, %d missing member ways", rid, v["kind"],
                          v["tags"].get("name"), nbad, missing)
            if g is None:
                if v["kind"] == "address" and outer:
                    g = MultiLineString([[(lon, lat) for lat, lon in line] for line in outer if len(line) >= 2])
                else:
                    bad[v["kind"] + "_nogeom"] += 1
                    continue
            if v["kind"] == "tbilisi":
                tb_geom = g if tb_geom is None else tb_geom
            elif v["kind"] == "district":
                ex.districts.append(OsmObj("r", rid, v["tags"], g))
            elif v["kind"] == "place":
                ex.places.append(OsmObj("r", rid, v["tags"], g))
            elif v["kind"] == "poi":
                ex.pois.append(OsmObj("r", rid, v["tags"], g))
            elif v["kind"] == "landmark":
                ex.landmarks.append(OsmObj("r", rid, v["tags"], g))
                if "addr:housenumber" in v["tags"]:
                    c = _centroid(g)
                    if c is not None:
                        ex.addresses.append(OsmObj("r", rid, v["tags"], c))
            elif v["kind"] == "address":
                c = _centroid(g)
                if c is not None:
                    ex.addresses.append(OsmObj("r", rid, v["tags"], c))
        if tb_geom is None:
            raise RuntimeError("Tbilisi boundary relation could not be assembled into a polygon")
        ex.boundary = tb_geom
        out.update(problems=dict(bad), boundary_km2=round(float(shapely.area(_to_xy(tb_geom))) / 1e6, 1))
    return clip_to_boundary(ex)


def _to_xy(g):
    from pipeline.osm.geo import to_xy

    return to_xy(g)


# ---------------------------------------------------------------------------- Overpass cache
def _ov_load(folder: Path) -> list[dict]:
    els: list[dict] = []
    if not folder.exists():
        return els
    for f in sorted(folder.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        els.extend(d.get("elements", []))
    return els


def _ov_snapshot(folder: Path) -> str | None:
    for f in sorted(folder.glob("*/*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ts = d.get("osm3s", {}).get("timestamp_osm_base")
        if ts:
            return ts
    return None


def _ov_way_coords(e: dict) -> tuple[list[int], list[tuple[float, float]]]:
    nodes = list(e.get("nodes") or [])
    geom = e.get("geometry") or []
    coords, keep_nodes = [], []
    for i, g in enumerate(geom):
        if not g:
            continue
        coords.append((float(g["lat"]), float(g["lon"])))
        keep_nodes.append(nodes[i] if i < len(nodes) else -(e["id"] * 10000 + i))
    return keep_nodes, coords


def _ov_rel_geom(e: dict):
    outer, inner = [], []
    for m in e.get("members", []):
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        line = [(float(g["lat"]), float(g["lon"])) for g in m["geometry"] if g]
        (inner if m.get("role") == "inner" else outer).append(line)
    g, _bad = polygon_from_rings(outer, inner)
    return g


def _ov_point(e: dict) -> Point | None:
    if "lat" in e and "lon" in e:
        return Point(float(e["lon"]), float(e["lat"]))
    c = e.get("center")
    if c:
        return Point(float(c["lon"]), float(c["lat"]))
    return None


def read_overpass(folder: Path) -> Extract:
    """Build an extract from the Overpass cache (``out body geom`` / ``out tags center``)."""
    with stage(log, "osm.extract.overpass", folder=str(folder)) as out:
        ex = Extract(source="overpass", snapshot=_ov_snapshot(folder), boundary=None, info={"folder": str(folder)})
        seen: set[tuple[str, int]] = set()
        cnt = Counter()
        for e in _ov_load(folder / "highways"):
            t = e.get("tags", {})
            if e.get("type") == "way" and t.get("highway") and t.get("name"):
                nodes, coords = _ov_way_coords(e)
                if len(coords) >= 2:
                    ex.highways.append(OsmWay(int(e["id"]), dict(t), nodes, coords))
        for e in _ov_load(folder / "addresses"):
            t = e.get("tags", {})
            p = _ov_point(e)
            if t.get("addr:housenumber") and p is not None:
                ex.addresses.append(OsmObj(e["type"][0], int(e["id"]), dict(t), p))
        # place polygons first: misc.json has the same places as bare centres ("out tags center")
        for e in _ov_load(folder / "place_polygons") + _ov_load(folder / "misc"):
            t = e.get("tags", {})
            typ = e.get("type", "")[:1]
            key = (typ, int(e.get("id", 0)))
            if typ == "w" and t.get("waterway") and t.get("name") and e.get("geometry"):
                if ("ww",) + key not in seen:
                    seen.add(("ww",) + key)
                    nodes, coords = _ov_way_coords(e)
                    if len(coords) >= 2:
                        ex.waterways.append(OsmWay(key[1], dict(t), nodes, coords))
                continue
            geom = None
            if typ == "r" and e.get("members"):
                geom = _ov_rel_geom(e)
            elif typ == "w" and e.get("geometry"):
                nodes, coords = _ov_way_coords(e)
                geom = _closed_way_geom(coords, nodes)
            if geom is None:
                geom = _ov_point(e)
            if geom is None:
                cnt["no_geom"] += 1
                continue
            if is_district(t) and typ == "r" and geom.geom_type in ("Polygon", "MultiPolygon"):
                if ("d",) + key not in seen:
                    seen.add(("d",) + key)
                    ex.districts.append(OsmObj(typ, key[1], dict(t), geom))
                continue
            if is_place(t):
                if ("p",) + key not in seen:
                    seen.add(("p",) + key)
                    ex.places.append(OsmObj(typ, key[1], dict(t), geom))
                continue
            if poi_kind(t) and ("poi",) + key not in seen:
                seen.add(("poi",) + key)
                ex.pois.append(OsmObj(typ, key[1], dict(t), geom))
            elif is_landmark_candidate(t) and ("lm",) + key not in seen:
                seen.add(("lm",) + key)
                ex.landmarks.append(OsmObj(typ, key[1], dict(t), geom))
        if not ex.districts:
            raise RuntimeError(f"no district polygons in Overpass cache {folder}")
        ex.boundary = as_multipolygon(shapely.union_all([d.geom for d in ex.districts]).buffer(0))
        out.update(ex.counts())
        out.update(cnt)
    # the Overpass queries were already limited to the Tbilisi area; the district union is a
    # slightly smaller boundary, so only drop objects clearly outside it (500 m tolerance)
    exact = ex.boundary
    ex.boundary = exact.buffer(0.006)
    clip_to_boundary(ex)
    ex.boundary = exact
    return ex


# ---------------------------------------------------------------------------- entry point
def load_extract(pbf: Path | None, snapshot: str | None, overpass_dir: Path, use_cache: bool = True,
                 cache_path: Path = EXTRACT_CACHE) -> Extract:
    """Extract from ``pbf`` (cached as JSON next to other interim files) or the Overpass cache."""
    if pbf is not None:
        key = {"file": pbf.name, "bytes": pbf.stat().st_size, "version": EXTRACT_VERSION}
        if use_cache and cache_path.exists():
            try:
                ex = Extract.load(cache_path)
                if ex.source == "pbf" and ex.info.get("cache_key") == key:
                    return ex
                log.info("extract cache is stale (%s != %s)", ex.info.get("cache_key"), key)
            except (OSError, ValueError, KeyError) as exc:
                log.warning("extract cache unreadable (%s); rebuilding", exc)
        t0 = time.perf_counter()
        ex = read_pbf(pbf, snapshot)
        ex.info["cache_key"] = key
        log.info("PBF extract built in %.1fs: %s", time.perf_counter() - t0, ex.counts())
        if use_cache:
            ex.save(cache_path)
        return ex
    log.warning("no PBF available: falling back to the Overpass cache in %s", overpass_dir)
    return read_overpass(overpass_dir)
