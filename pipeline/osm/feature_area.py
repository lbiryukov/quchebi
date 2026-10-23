"""feature_area stage: share of every feature inside districts and neighbourhoods.

Lines are sampled at the midpoints of pieces of at most :data:`STEP_M` metres (weight = piece
length); polygons on a :data:`STEP_M` grid inside them (plus their label point); points count as
one sample. Each sample belongs to the district polygon containing it and to the smallest place
polygon containing it (else to the nearest point-only place within :data:`POINT_PLACE_M`).
Shares are fractions of the total weight. Area features get their own area with share 1.0, and
neighbourhoods additionally their districts by overlap.
"""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from pipeline.db import tx
from pipeline.log import get_logger, stage
from pipeline.osm.geo import to_xy

log = get_logger(__name__)

STEP_M = 50.0
POINT_PLACE_M = 300.0   # same radius the client uses for point-only places
MIN_SHARE = 0.0005


def sample_line(g_xy: BaseGeometry, step: float = STEP_M) -> tuple[np.ndarray, np.ndarray]:
    """Midpoints (N x 2) and weights (metres) of pieces <= ``step`` along all line parts."""
    pts, ws = [], []
    parts = g_xy.geoms if isinstance(g_xy, MultiLineString) else [g_xy]
    for ln in parts:
        c = np.asarray(ln.coords)[:, :2]
        if len(c) < 2:
            continue
        a, b = c[:-1], c[1:]
        seg = np.hypot(*(b - a).T)
        keep = seg > 0
        a, b, seg = a[keep], b[keep], seg[keep]
        if not len(seg):
            continue
        k = np.maximum(1, np.ceil(seg / step)).astype(np.int64)
        si = np.repeat(np.arange(len(seg)), k)                 # segment of every piece
        j = np.arange(len(si)) - np.repeat(np.cumsum(k) - k, k)  # piece number inside its segment
        f = (j + 0.5) / k[si]
        pts.append(a[si] + f[:, None] * (b[si] - a[si]))
        ws.append((seg / k)[si])
    if not pts:
        return np.zeros((0, 2)), np.zeros(0)
    return np.vstack(pts), np.concatenate(ws)


def sample_polygon(g_xy: BaseGeometry, step: float = STEP_M) -> tuple[np.ndarray, np.ndarray]:
    minx, miny, maxx, maxy = g_xy.bounds
    xs = np.arange(minx + step / 2, maxx, step)
    ys = np.arange(miny + step / 2, maxy, step)
    rp = g_xy.representative_point()
    pts = [np.array([[rp.x, rp.y]])]
    if len(xs) and len(ys):
        gx, gy = np.meshgrid(xs, ys)
        gx, gy = gx.ravel(), gy.ravel()
        m = shapely.contains_xy(g_xy, gx, gy)
        if m.any():
            pts.append(np.column_stack([gx[m], gy[m]]))
    p = np.vstack(pts)
    return p, np.ones(len(p))


def sample(g_xy: BaseGeometry) -> tuple[np.ndarray, np.ndarray]:
    if isinstance(g_xy, (LineString, MultiLineString)):
        return sample_line(g_xy)
    if isinstance(g_xy, (Polygon, MultiPolygon)):
        return sample_polygon(g_xy)
    if isinstance(g_xy, Point):
        return np.array([[g_xy.x, g_xy.y]]), np.ones(1)
    c = g_xy.centroid
    return np.array([[c.x, c.y]]), np.ones(1)


class AreaIndex:
    """Districts and places (projected) for point lookups."""

    def __init__(self, rows: list[sqlite3.Row]):
        self.districts: list[tuple[int, BaseGeometry]] = []
        self.places: list[tuple[int, BaseGeometry, float]] = []
        self.place_points: list[tuple[int, float, float]] = []
        for r in rows:
            if r["geom_wkt"]:
                g = to_xy(shapely.from_wkt(r["geom_wkt"]))
                shapely.prepare(g)
                if r["kind"] == "district":
                    self.districts.append((r["id"], g))
                else:
                    self.places.append((r["id"], g, float(g.area)))
            elif r["kind"] != "district" and r["lat"] is not None:
                x, y = to_xy(Point(r["lon"], r["lat"])).coords[0]
                self.place_points.append((r["id"], x, y))
        self.places.sort(key=lambda t: t[2])  # smallest first
        self.pp_xy = np.array([[x, y] for _, x, y in self.place_points]) if self.place_points else np.zeros((0, 2))

    def district_of(self, pts: np.ndarray) -> np.ndarray:
        out = np.zeros(len(pts), dtype=np.int64)
        if not len(pts):
            return out
        for aid, g in self.districts:
            free = out == 0
            if not free.any():
                break
            m = shapely.contains_xy(g, pts[free, 0], pts[free, 1])
            idx = np.nonzero(free)[0][m]
            out[idx] = aid
        return out

    def place_of(self, pts: np.ndarray) -> tuple[np.ndarray, int]:
        out = np.zeros(len(pts), dtype=np.int64)
        if not len(pts):
            return out, 0
        for aid, g, _a in self.places:
            free = out == 0
            if not free.any():
                break
            m = shapely.contains_xy(g, pts[free, 0], pts[free, 1])
            out[np.nonzero(free)[0][m]] = aid
        by_point = 0
        free = np.nonzero(out == 0)[0]
        if len(free) and len(self.pp_xy):
            d2 = ((pts[free, None, :] - self.pp_xy[None, :, :]) ** 2).sum(axis=2)
            j = d2.argmin(axis=1)
            ok = d2[np.arange(len(free)), j] <= POINT_PLACE_M ** 2
            out[free[ok]] = [self.place_points[k][0] for k in j[ok]]
            by_point = int(ok.sum())
        return out, by_point


def compute_shares(feats: list[sqlite3.Row], idx: AreaIndex) -> tuple[list[tuple[int, int, float]], dict]:
    """All samples of all features are classified in one vectorized pass, then summed per feature."""
    rows: list[tuple[int, int, float]] = []
    st = Counter()
    pts_l, w_l, owner_l, is_line_l = [], [], [], []
    fid_of: list[int] = []
    for f in feats:
        if not f["geom_wkt"]:
            st["no_geom"] += 1
            continue
        if f["kind"] == "district" and f["area_id"]:
            rows.append((f["id"], f["area_id"], 1.0))
            continue
        g = to_xy(shapely.from_wkt(f["geom_wkt"]))
        if f["kind"] == "neighbourhood" and f["area_id"]:
            rows.append((f["id"], f["area_id"], 1.0))
            if isinstance(g, (Polygon, MultiPolygon)) and g.area > 0:
                for aid, dg in idx.districts:
                    s = float(g.intersection(dg).area / g.area)
                    if s >= MIN_SHARE:
                        rows.append((f["id"], aid, round(s, 4)))
            else:
                p = g if isinstance(g, Point) else g.representative_point()
                d = idx.district_of(np.array([[p.x, p.y]]))[0]
                if d:
                    rows.append((f["id"], int(d), 1.0))
            continue
        pts, w = sample(g)
        if not len(pts):
            st["no_samples"] += 1
            continue
        k = len(fid_of)
        fid_of.append(f["id"])
        pts_l.append(pts)
        w_l.append(w)
        owner_l.append(np.full(len(pts), k, dtype=np.int64))
        is_line_l.append(np.full(len(pts), isinstance(g, (LineString, MultiLineString))))
    if not fid_of:
        return rows, dict(st)
    pts = np.vstack(pts_l)
    w = np.concatenate(w_l)
    owner = np.concatenate(owner_l)
    is_line = np.concatenate(is_line_l)
    st["samples"] = len(pts)
    dist = idx.district_of(pts)
    plc, by_point = idx.place_of(pts)
    st["samples_by_place_point"] = by_point
    tot = np.bincount(owner, weights=w, minlength=len(fid_of))
    line_w = float(w[is_line].sum()) or 1.0
    st["pct_outside_districts"] = round(100 * float(w[is_line & (dist == 0)].sum()) / line_w, 2)
    st["pct_outside_places"] = round(100 * float(w[is_line & (plc == 0)].sum()) / line_w, 2)
    nd = Counter()
    for labels, is_dist in ((dist, True), (plc, False)):
        m = labels != 0
        keys = owner[m] * (1 << 32) + labels[m]
        uk, inv = np.unique(keys, return_inverse=True)
        sums = np.bincount(inv, weights=w[m])
        for key, v in zip(uk.tolist(), sums.tolist()):
            k, aid = divmod(key, 1 << 32)
            share = v / (tot[k] or 1.0)
            if share >= MIN_SHARE:
                rows.append((fid_of[k], aid, round(share, 4)))
                if is_dist and share >= 0.1:
                    nd[k] += 1
    for k in range(len(fid_of)):
        st[f"features_in_{min(nd.get(k, 0), 3)}_districts"] += 1
    return rows, dict(st)


def run(con: sqlite3.Connection) -> dict:
    areas = con.execute("SELECT id, kind, lat, lon, geom_wkt FROM area").fetchall()
    feats = con.execute("SELECT id, kind, area_id, geom_wkt FROM feature").fetchall()
    with stage(log, "osm.feature_area", features=len(feats), areas=len(areas)) as out:
        idx = AreaIndex(areas)
        rows, st = compute_shares(feats, idx)
        out.update(st)
        with tx(con):
            con.execute("DELETE FROM feature_area")
            con.executemany("INSERT OR REPLACE INTO feature_area(feature_id, area_id, share) VALUES (?,?,?)", rows)
        out["rows"] = len(rows)
        return out
