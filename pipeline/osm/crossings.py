"""Crossings stage: street pairs that meet, with the leaving directions of both streets.

Only features built from highway ways take part. Criteria:

1. shared OSM node (``kind = 'node'``); shared nodes of one pair closer than
   :data:`CLUSTER_M` to each other are one crossing (dual carriageways, overlaps);
2. geometric fallback for pairs without a shared node (``kind = 'geom'``), on the same layer:
   a way end within :data:`TOUCH_M` of the other street (:data:`TOUCH_BT_M` if either way is a
   bridge / tunnel), or a same-layer intersection of two non-bridge, non-tunnel ways (a mapping
   error: the node is missing). Different layers never count (grade separation).

``near_pair``: way ends :data:`TOUCH_M`..:data:`NEAR_M` from another street (both on the ground),
for pairs without any crossing; ``dist_m`` is the smallest such distance.

Direction masks: :func:`pipeline.osm.geo.direction_mask` over the first :data:`REACH_M` metres.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, Point

from pipeline.db import tx
from pipeline.log import get_logger, stage
from pipeline.osm.features import UnionFind
from pipeline.osm.geo import direction_mask, proj, to_xy, unproj
from pipeline.osm.model import Extract, OsmWay

log = get_logger(__name__)

TOUCH_M = 8.0
TOUCH_BT_M = 2.0
NEAR_M = 25.0
CLUSTER_M = 60.0
REACH_M = 30.0


@dataclass
class WayInfo:
    way: OsmWay
    fid: int
    xy: LineString
    layer: int
    bt: bool  # bridge or tunnel


def eff_layer(t: dict[str, str]) -> tuple[int, bool, bool]:
    """(layer, is_bridge, is_tunnel); missing layer defaults to +1 on bridges, -1 in tunnels."""
    br = t.get("bridge", "no") not in ("no",)
    tu = t.get("tunnel", "no") not in ("no",) and t.get("tunnel") != "building_passage"
    try:
        layer = int(float(t.get("layer", "0").split(";")[0]))
    except ValueError:
        layer = 0
    if "layer" not in t:
        layer = 1 if br else (-1 if tu else 0)
    return layer, br, tu


def way_feature_map(con: sqlite3.Connection, ex: Extract) -> dict[int, int]:
    """Highway way id -> feature id (only features made of highway ways)."""
    hw_ids = {w.id for w in ex.highways}
    out: dict[int, int] = {}
    for r in con.execute("SELECT id, osm_ids FROM feature WHERE kind NOT IN ('district','neighbourhood','metro')"):
        for ref in json.loads(r["osm_ids"] or "[]"):
            if ref.startswith("w"):
                wid = int(ref[1:])
                if wid in hw_ids:
                    out.setdefault(wid, r["id"])
    return out


def feature_lines(con: sqlite3.Connection, fids: set[int]) -> dict[int, list[LineString]]:
    out: dict[int, list[LineString]] = {}
    for r in con.execute("SELECT id, geom_wkt FROM feature WHERE geom_wkt IS NOT NULL"):
        if r["id"] not in fids:
            continue
        g = to_xy(shapely.from_wkt(r["geom_wkt"]))
        parts = list(g.geoms) if isinstance(g, MultiLineString) else [g] if isinstance(g, LineString) else []
        out[r["id"]] = [p for p in parts if isinstance(p, LineString)]
    return out


def compute(ways: list[WayInfo], lines: dict[int, list[LineString]]) -> tuple[list[tuple], list[tuple], dict]:
    """Return (crossing rows, near_pair rows, stats). Rows use feature ids; a < b."""
    st = Counter()
    # ---- 1. shared nodes
    node_feats: dict[int, set[int]] = defaultdict(set)
    node_xy: dict[int, tuple[float, float]] = {}
    for wi in ways:
        for nd, (lat, lon) in zip(wi.way.nodes, wi.way.coords):
            node_feats[nd].add(wi.fid)
            node_xy[nd] = proj(lat, lon)
    pair_nodes: dict[tuple[int, int], list[int]] = defaultdict(list)
    for nd, fs in node_feats.items():
        if len(fs) >= 2:
            fs_ = sorted(fs)
            for i in range(len(fs_)):
                for j in range(i + 1, len(fs_)):
                    pair_nodes[(fs_[i], fs_[j])].append(nd)
    st["node_pairs"] = len(pair_nodes)
    points: list[tuple[int, int, float, float, str, float]] = []  # a, b, x, y, kind, tolerance
    for (a, b), nds in pair_nodes.items():
        if len(nds) == 1:
            x, y = node_xy[nds[0]]
            points.append((a, b, x, y, "node", 3.0))
            continue
        xy = np.array([node_xy[n] for n in nds])
        uf = UnionFind(len(nds))
        d2 = ((xy[:, None, :] - xy[None, :, :]) ** 2).sum(axis=2)
        ii, jj = np.nonzero(np.triu(d2 <= CLUSTER_M ** 2, 1))
        for i, j in zip(ii.tolist(), jj.tolist()):
            uf.union(i, j)
        for g in uf.groups():
            c = xy[g].mean(axis=0)
            k = g[int(((xy[g] - c) ** 2).sum(axis=1).argmin())]
            points.append((a, b, float(xy[k, 0]), float(xy[k, 1]), "node", 3.0))
        st["node_pairs_multi"] += 1

    # ---- 2. geometric fallback
    geoms = [wi.xy for wi in ways]
    tree = shapely.STRtree(geoms)
    ia, ib = tree.query(geoms, predicate="dwithin", distance=NEAR_M)
    cross_geom: dict[tuple[int, int], tuple[float, float, float]] = {}
    grade_sep: set[tuple[int, int]] = set()
    near: dict[tuple[int, int], float] = {}
    for i, j in zip(ia.tolist(), ib.tolist()):
        if i == j:
            continue
        A, B = ways[i], ways[j]
        if A.fid == B.fid:
            continue
        key = (min(A.fid, B.fid), max(A.fid, B.fid))
        if key in pair_nodes:
            continue
        same = A.layer == B.layer
        if A.xy.intersects(B.xy):
            if i > j:
                continue  # handled as (j, i)
            if same and not A.bt and not B.bt:
                if key not in cross_geom:
                    ip = A.xy.intersection(B.xy)
                    p = ip if isinstance(ip, Point) else ip.representative_point()
                    cross_geom[key] = (p.x, p.y, 0.0)
                    st["geom_same_layer_intersections"] += 1
            else:
                grade_sep.add(key)
            continue
        if not same:
            continue
        thr = TOUCH_BT_M if (A.bt or B.bt) else TOUCH_M
        for e in (A.xy.coords[0], A.xy.coords[-1]):
            pe = Point(e)
            d = float(B.xy.distance(pe))
            if d <= thr:
                prev = cross_geom.get(key)
                if prev is None or prev[2] > d:
                    cross_geom[key] = (e[0], e[1], d)
            elif d <= NEAR_M and not (A.bt or B.bt):
                near[key] = min(near.get(key, d), d)
    st["geom_pairs"] = len(cross_geom)
    st["geom_end_touch"] = st["geom_pairs"] - st["geom_same_layer_intersections"]
    st["grade_separated_pairs"] = len(grade_sep - set(cross_geom))
    for (a, b), (x, y, d) in cross_geom.items():
        points.append((a, b, x, y, "geom", max(3.0, d + 1.0)))
    crossing_pairs = set(pair_nodes) | set(cross_geom)
    near_rows = sorted((a, b, round(d, 1)) for (a, b), d in near.items() if (a, b) not in crossing_pairs)
    st["pairs_total"] = len(crossing_pairs)
    st["near_pairs"] = len(near_rows)

    # ---- 3. direction masks
    rows = []
    seen = set()
    for a, b, x, y, kind, tol in points:
        lat, lon = unproj(x, y)
        lat, lon = round(lat, 7), round(lon, 7)
        if (a, b, lat, lon) in seen:
            continue
        seen.add((a, b, lat, lon))
        da = direction_mask(lines.get(a, []), (x, y), REACH_M, near=tol)
        db = direction_mask(lines.get(b, []), (x, y), REACH_M, near=tol)
        if not da or not db:
            st["empty_mask"] += 1
        rows.append((a, b, lat, lon, kind, da, db))
    st["crossing_rows"] = len(rows)
    return rows, near_rows, dict(st)


def run(con: sqlite3.Connection, ex: Extract) -> dict:
    wmap = way_feature_map(con, ex)
    with stage(log, "osm.crossings", highway_ways=len(ex.highways), mapped_ways=len(wmap)) as out:
        ways = []
        for w in ex.highways:
            fid = wmap.get(w.id)
            if fid is None or len(w.coords) < 2:
                continue
            layer, br, tu = eff_layer(w.tags)
            ways.append(WayInfo(w, fid, to_xy(LineString([(lon, lat) for lat, lon in w.coords])), layer, br or tu))
        lines = feature_lines(con, {wi.fid for wi in ways})
        rows, near_rows, st = compute(ways, lines)
        out.update(st)
        with tx(con):
            con.execute("DELETE FROM crossing")
            con.execute("DELETE FROM near_pair")
            con.executemany("INSERT OR IGNORE INTO crossing(a, b, lat, lon, kind, dir_a, dir_b) VALUES (?,?,?,?,?,?,?)",
                            rows)
            con.executemany("INSERT OR IGNORE INTO near_pair(a, b, dist_m) VALUES (?,?,?)", near_rows)
        deg = Counter()
        for a, b, *_ in rows:
            deg[a] += 1
            deg[b] += 1
        out["features_with_crossings"] = len(deg)
        return out
