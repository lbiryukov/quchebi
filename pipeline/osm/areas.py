"""Areas stage: districts (admin_level=10) and neighbourhoods (place=suburb|neighbourhood|quarter).

Writes ``area`` rows plus one ``feature`` per area (kind ``district`` / ``neighbourhood``,
``feature.area_id`` set). ``area.kind`` is ``district`` or the OSM ``place`` value.

Parent of a neighbourhood: the smallest neighbourhood polygon that contains it (>= 90% of its
area, or its point), else the district containing its label point. Places that duplicate a
district (``admin_level=10``, name ending in ``რაიონი``, same name, or >= 90% overlap) are skipped;
a place point is skipped when a polygon place with the same name exists.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

import shapely
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from pipeline.db import tx
from pipeline.log import get_logger, stage
from pipeline.names import normalize_ka, normalize_ru_name
from pipeline.osm.features import generated_ru, name_variants
from pipeline.osm.geo import bbox_str, to_xy, wkt
from pipeline.osm.model import Extract, OsmObj

log = get_logger(__name__)

CONTAIN_SHARE = 0.9


@dataclass
class AreaRec:
    kind: str                 # district | suburb | neighbourhood | quarter
    ka: str
    ru: str | None
    en: str | None
    ru_generated: int
    osm_type: str
    osm_id: int
    geom_ll: BaseGeometry     # (Multi)Polygon or Point
    area_km2: float | None
    lat: float
    lon: float
    names: set = field(default_factory=set)
    parent: int | None = None  # index into the area list
    wikidata: str | None = None

    @property
    def is_poly(self) -> bool:
        return isinstance(self.geom_ll, (Polygon, MultiPolygon))


def _rec(o: OsmObj, kind: str) -> AreaRec:
    t = o.tags
    ka = t["name"].strip()
    ru_osm = t.get("name:ru")
    ru, gen = (normalize_ru_name(ru_osm), 0) if ru_osm else (generated_ru(ka), 1)
    g = o.geom
    if isinstance(g, (Polygon, MultiPolygon)):
        km2 = round(float(to_xy(g).area) / 1e6, 4)
        lp = g.point_on_surface()
    else:
        km2 = None
        lp = g if isinstance(g, Point) else g.representative_point()
    r = AreaRec(kind=kind, ka=ka, ru=ru, en=t.get("name:en"), ru_generated=gen, osm_type=o.type, osm_id=o.id,
                geom_ll=g, area_km2=km2, lat=lp.y, lon=lp.x, wikidata=t.get("name:etymology:wikidata"))
    primary = {ka, r.en} | ({r.ru} if not gen else set())
    r.names = {v for v in name_variants(t) if v[1] not in primary}
    return r


def _overlap_share(a_xy: BaseGeometry, b_xy: BaseGeometry) -> float:
    """Share of ``a`` inside ``b`` (area for polygons, 1/0 for points)."""
    if a_xy.geom_type == "Point":
        return 1.0 if b_xy.covers(a_xy) else 0.0
    if a_xy.area <= 0:
        return 0.0
    return float(a_xy.intersection(b_xy).area / a_xy.area)


def build_areas(ex: Extract) -> tuple[list[AreaRec], dict]:
    st = Counter()
    districts = [_rec(o, "district") for o in ex.districts
                 if o.tags.get("name") and isinstance(o.geom, (Polygon, MultiPolygon))]
    districts.sort(key=lambda r: r.ka)
    d_names = {normalize_ka(d.ka) for d in districts}
    d_xy = [to_xy(d.geom_ll) for d in districts]
    for g in d_xy:
        shapely.prepare(g)

    polys, points = [], []
    for o in ex.places:
        t = o.tags
        nm = (t.get("name") or "").strip()
        if not nm:
            continue
        if t.get("admin_level") == "10" or nm.endswith("რაიონი") or normalize_ka(nm) in d_names:
            st["skip_admin_dup"] += 1
            continue
        (polys if isinstance(o.geom, (Polygon, MultiPolygon)) else points).append(o)

    # polygons: drop near-duplicates of districts and of each other (way + relation of one place)
    recs: list[AreaRec] = []
    for o in sorted(polys, key=lambda o: (o.type != "r", -to_xy(o.geom).area, o.id)):
        r = _rec(o, o.tags["place"])
        rxy = to_xy(r.geom_ll)
        if any(_overlap_share(rxy, g) >= CONTAIN_SHARE and _overlap_share(g, rxy) >= CONTAIN_SHARE for g in d_xy):
            st["skip_district_shape"] += 1
            continue
        dup = False
        for q in recs:
            if normalize_ka(q.ka) == normalize_ka(r.ka):
                qxy = to_xy(q.geom_ll)
                if _overlap_share(rxy, qxy) >= 0.8 or _overlap_share(qxy, rxy) >= 0.8:
                    dup = True
                    break
        if dup:
            st["skip_poly_dup"] += 1
            continue
        recs.append(r)
    poly_names = {normalize_ka(r.ka) for r in recs}
    pts_seen: set[str] = set()
    for o in sorted(points, key=lambda o: o.id):
        nk = normalize_ka(o.tags["name"])
        if nk in poly_names:
            st["skip_point_has_polygon"] += 1
            continue
        if nk in pts_seen:
            st["skip_point_dup"] += 1
            continue
        pts_seen.add(nk)
        recs.append(_rec(o, o.tags["place"]))
    recs.sort(key=lambda r: (not r.is_poly, r.ka, r.osm_id))
    areas = districts + recs
    nd = len(districts)

    # parents: smallest containing place polygon, else district containing the label point
    xy = [to_xy(a.geom_ll) for a in areas]
    by_size = sorted((i for i in range(nd, len(areas)) if areas[i].is_poly), key=lambda i: areas[i].area_km2)
    for i in range(nd, len(areas)):
        a = areas[i]
        a_xy = xy[i]
        for j in by_size:
            if j == i or (a.is_poly and areas[j].area_km2 <= a.area_km2):
                continue
            if _overlap_share(a_xy, xy[j]) >= CONTAIN_SHARE:
                a.parent = j
                st["parent_place"] += 1
                break
        if a.parent is None:
            lp = to_xy(Point(a.lon, a.lat))
            for j in range(nd):
                if xy[j].covers(lp):
                    a.parent = j
                    st["parent_district"] += 1
                    break
        if a.parent is None:
            st["no_parent"] += 1
    st.update(districts=nd, place_polygons=sum(1 for r in recs if r.is_poly),
              place_points=sum(1 for r in recs if not r.is_poly))
    st.update({"kind_" + k: v for k, v in Counter(r.kind for r in recs).items()})
    return areas, dict(st)


def clear_areas(con: sqlite3.Connection) -> None:
    con.execute("UPDATE house SET area_id = NULL WHERE area_id IS NOT NULL")
    con.execute("DELETE FROM feature WHERE kind IN ('district', 'neighbourhood')")
    con.execute("UPDATE area SET parent_id = NULL")
    con.execute("DELETE FROM area")


def write_areas(con: sqlite3.Connection, areas: list[AreaRec]) -> dict:
    with tx(con):
        clear_areas(con)
        ids: list[int] = []
        for a in areas:
            cur = con.execute(
                "INSERT INTO area(kind, ka, ru, en, osm_type, osm_id, area_km2, lat, lon, geom_wkt) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (a.kind, a.ka, a.ru, a.en, a.osm_type, a.osm_id, a.area_km2, round(a.lat, 7), round(a.lon, 7),
                 wkt(a.geom_ll) if a.is_poly else None))
            ids.append(cur.lastrowid)
        con.executemany("UPDATE area SET parent_id=? WHERE id=?",
                        [(ids[a.parent], ids[i]) for i, a in enumerate(areas) if a.parent is not None])
        nf = 0
        names = 0
        for i, a in enumerate(areas):
            cur = con.execute(
                "INSERT INTO feature(kind, ka, ru, en, ru_generated, len_m, lat, lon, bbox, geom_wkt, area_id, "
                "osm_ids, wikidata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("district" if a.kind == "district" else "neighbourhood", a.ka, a.ru, a.en, a.ru_generated, 0.0,
                 round(a.lat, 7), round(a.lon, 7), bbox_str(a.geom_ll), wkt(a.geom_ll), ids[i],
                 json.dumps([f"{a.osm_type}{a.osm_id}"]), a.wikidata))
            nf += 1
            fid = cur.lastrowid
            for lang, nm, k, src in sorted(a.names):
                con.execute("INSERT OR IGNORE INTO feature_name(feature_id, lang, name, kind, source) VALUES (?,?,?,?,?)",
                            (fid, lang, nm, k, src))
                names += 1
    return {"areas": len(ids), "area_features": nf, "area_feature_names": names}


def run(con: sqlite3.Connection, ex: Extract) -> dict:
    with stage(log, "osm.areas", districts_in=len(ex.districts), places_in=len(ex.places)) as out:
        areas, st = build_areas(ex)
        out.update(st)
        out.update(write_areas(con, areas))
        if out.get("districts") != 10:
            log.warning("expected 10 districts, got %s", out.get("districts"))
        return out
