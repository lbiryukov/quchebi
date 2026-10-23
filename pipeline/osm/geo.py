"""Geometry helpers: local metric projection, WKT, bbox, bearings and direction masks.

All metric work happens in a local equirectangular projection centred on Tbilisi
(error well below 0.5% inside the city), x = east metres, y = north metres. Geometries are
stored in WGS84 (lon, lat order inside shapely/WKT, as usual).
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

LAT0 = 41.72
LON0 = 44.79
KX = 111320.0 * math.cos(math.radians(LAT0))  # metres per degree of longitude at LAT0
KY = 110540.0                                  # metres per degree of latitude

SECTORS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def proj(lat: float, lon: float) -> tuple[float, float]:
    """WGS84 -> local metres (x east, y north)."""
    return ((lon - LON0) * KX, (lat - LAT0) * KY)


def unproj(x: float, y: float) -> tuple[float, float]:
    """Local metres -> (lat, lon)."""
    return (y / KY + LAT0, x / KX + LON0)


def _fwd(coords: np.ndarray) -> np.ndarray:
    out = np.empty_like(coords)
    out[:, 0] = (coords[:, 0] - LON0) * KX
    out[:, 1] = (coords[:, 1] - LAT0) * KY
    return out


def _inv(coords: np.ndarray) -> np.ndarray:
    out = np.empty_like(coords)
    out[:, 0] = coords[:, 0] / KX + LON0
    out[:, 1] = coords[:, 1] / KY + LAT0
    return out


def to_xy(g):
    """Shapely geometry (or array of them) in WGS84 lon/lat -> local metres."""
    return shapely.transform(g, _fwd)


def to_ll(g):
    """Shapely geometry (or array of them) in local metres -> WGS84 lon/lat."""
    return shapely.transform(g, _inv)


def line_ll(coords_latlon: Sequence[tuple[float, float]]) -> LineString:
    """LineString in lon/lat order from a list of (lat, lon)."""
    return LineString([(lon, lat) for lat, lon in coords_latlon])


def wkt(g: BaseGeometry | None, precision: int = 7) -> str | None:
    if g is None or g.is_empty:
        return None
    return shapely.to_wkt(g, rounding_precision=precision, trim=True)


def bbox_str(g: BaseGeometry) -> str:
    """``"minlat,minlon,maxlat,maxlon"`` of a WGS84 geometry."""
    minx, miny, maxx, maxy = g.bounds
    return f"{miny:.6f},{minx:.6f},{maxy:.6f},{maxx:.6f}"


def as_multiline(g: BaseGeometry) -> MultiLineString:
    if isinstance(g, MultiLineString):
        return g
    if isinstance(g, LineString):
        return MultiLineString([g])
    parts = [p for p in getattr(g, "geoms", []) if isinstance(p, LineString)]
    for p in getattr(g, "geoms", []):
        if isinstance(p, MultiLineString):
            parts.extend(p.geoms)
    return MultiLineString(parts)


def as_multipolygon(g: BaseGeometry) -> MultiPolygon | Polygon:
    if isinstance(g, (Polygon, MultiPolygon)):
        return g
    polys = [p for p in getattr(g, "geoms", []) if isinstance(p, (Polygon, MultiPolygon))]
    return shapely.union_all(polys) if polys else Polygon()


def representative_point(g_ll: BaseGeometry) -> tuple[float, float]:
    """(lat, lon) of a representative point: middle of the longest part for lines,
    point-on-surface for polygons, the point itself for points."""
    if isinstance(g_ll, Point):
        return g_ll.y, g_ll.x
    if isinstance(g_ll, (LineString, MultiLineString)):
        parts = list(as_multiline(g_ll).geoms)
        if not parts:
            c = g_ll.centroid
            return c.y, c.x
        xy = [to_xy(p) for p in parts]
        i = max(range(len(parts)), key=lambda k: xy[k].length)
        p = xy[i].interpolate(0.5, normalized=True)
        lat, lon = unproj(p.x, p.y)
        return lat, lon
    p = g_ll.point_on_surface() if not g_ll.is_empty else g_ll.centroid
    return p.y, p.x


def bearing_deg(dx: float, dy: float) -> float:
    """Compass bearing of a metric vector: 0 = N, 90 = E."""
    return math.degrees(math.atan2(dx, dy)) % 360.0


def sector_of(bearing: float) -> int:
    """8 sectors of 45 degrees centred on N, NE, E, ... -> 0..7."""
    return int(((bearing + 22.5) % 360.0) // 45.0)


def direction_mask(lines_xy: Iterable[LineString], p_xy: tuple[float, float], reach: float = 30.0,
                   near: float = 5.0, min_leg: float = 2.0) -> int:
    """Bitmask of sectors in which the lines leave point ``p_xy`` over the first ``reach`` metres.

    For every line passing within ``near`` metres of the point the position along the line is
    found; the chord from the point to the line position ``reach`` metres further (and back)
    gives a leaving direction, unless less than ``min_leg`` metres of line remain on that side.
    """
    P = Point(p_xy)
    mask = 0
    for ln in lines_xy:
        if ln.is_empty or ln.distance(P) > near:
            continue
        L = ln.length
        s = ln.project(P)
        for target in (s + reach, s - reach):
            t = min(max(target, 0.0), L)
            if abs(t - s) < min_leg:
                continue
            q = ln.interpolate(t)
            dx, dy = q.x - p_xy[0], q.y - p_xy[1]
            if dx * dx + dy * dy < min_leg * min_leg:
                continue
            mask |= 1 << sector_of(bearing_deg(dx, dy))
    return mask


def mask_names(mask: int) -> list[str]:
    return [SECTORS[i] for i in range(8) if mask >> i & 1]


def ring_area_m2(g_ll: BaseGeometry) -> float:
    return float(to_xy(g_ll).area)


def assemble_rings(lines: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Join open polylines of (lat, lon) into rings by matching end points (exact coordinates).

    Closed rings are returned closed; leftovers that cannot be closed are returned as they are
    (callers drop rings with < 4 points or unclosed ones after logging).
    """
    pending = [list(l) for l in lines if len(l) >= 2]
    rings: list[list[tuple[float, float]]] = []

    def key(p):
        return (round(p[0], 7), round(p[1], 7))

    by_end: dict = {}
    while pending:
        cur = pending.pop()
        changed = True
        while key(cur[0]) != key(cur[-1]) and changed:
            changed = False
            for i, l in enumerate(pending):
                if key(l[0]) == key(cur[-1]):
                    cur = cur + l[1:]
                elif key(l[-1]) == key(cur[-1]):
                    cur = cur + l[::-1][1:]
                elif key(l[-1]) == key(cur[0]):
                    cur = l + cur[1:]
                elif key(l[0]) == key(cur[0]):
                    cur = l[::-1] + cur[1:]
                else:
                    continue
                pending.pop(i)
                changed = True
                break
        rings.append(cur)
    del by_end
    return rings


def polygon_from_rings(outer: list[list[tuple[float, float]]], inner: list[list[tuple[float, float]]]
                       ) -> tuple[BaseGeometry | None, int]:
    """Build a valid (Multi)Polygon (lon/lat) from outer/inner member lines of (lat, lon).

    Returns (geometry or None, number of rings that could not be closed).
    """
    bad = 0
    outer_polys = []
    for r in assemble_rings(outer):
        if len(r) < 4 or (round(r[0][0], 7), round(r[0][1], 7)) != (round(r[-1][0], 7), round(r[-1][1], 7)):
            bad += 1
            continue
        outer_polys.append(Polygon([(lon, lat) for lat, lon in r]))
    inner_polys = []
    for r in assemble_rings(inner):
        if len(r) < 4 or (round(r[0][0], 7), round(r[0][1], 7)) != (round(r[-1][0], 7), round(r[-1][1], 7)):
            bad += 1
            continue
        inner_polys.append(Polygon([(lon, lat) for lat, lon in r]))
    if not outer_polys:
        return None, bad
    g = shapely.union_all([shapely.make_valid(p) for p in outer_polys])
    if inner_polys:
        g = shapely.difference(g, shapely.union_all([shapely.make_valid(p) for p in inner_polys]))
    g = as_multipolygon(shapely.make_valid(g))
    if g.is_empty:
        return None, bad
    return g, bad
