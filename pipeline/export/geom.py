"""Geometry helpers for the export: WKT -> simplified, rounded, encoded parts.

WKT in the database is WGS84 with ``x = lon, y = lat``. Simplification (Douglas-Peucker) runs
in a local equirectangular projection centred on Tbilisi: over a ~40 km city the scale error is
far below the 3 m tolerance, and it avoids a pyproj dependency.
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import shapely
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge

from pipeline.export.polyline import dedupe_ints, e5, encode_ints

LAT0 = 41.72
LON0 = 44.80
_M_PER_DEG = 6371008.8 * math.pi / 180.0
_KX = _M_PER_DEG * math.cos(math.radians(LAT0))
_KY = _M_PER_DEG

LINE_TOL_M = 3.0
AREA_TOL_M = 5.0

IntPart = list[tuple[int, int]]


def to_metric(g: BaseGeometry) -> BaseGeometry:
    return shapely.transform(g, lambda a: np.column_stack(((a[:, 0] - LON0) * _KX, (a[:, 1] - LAT0) * _KY)))


def to_wgs(g: BaseGeometry) -> BaseGeometry:
    return shapely.transform(g, lambda a: np.column_stack((a[:, 0] / _KX + LON0, a[:, 1] / _KY + LAT0)))


def load_wkt(text: str | None) -> BaseGeometry | None:
    if not text:
        return None
    g = shapely_wkt.loads(text)
    return None if g.is_empty else g


def _ints(coords: Iterable[tuple[float, float]]) -> IntPart:
    """Metric coords -> deduped integer (lat_e5, lon_e5)."""
    return dedupe_ints([(e5(y / _KY + LAT0), e5(x / _KX + LON0)) for x, y in coords])


def _simplify_line(coords, tol: float) -> IntPart:
    ls = LineString(coords)
    if tol > 0 and len(ls.coords) > 2:
        ls = ls.simplify(tol, preserve_topology=False)
    return _ints(ls.coords)


def _simplify_ring(coords, tol: float) -> IntPart:
    ring = list(coords)
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    simp = ring
    if tol > 0 and len(ring) > 4:
        s = list(LineString(ring).simplify(tol, preserve_topology=False).coords)
        if len(s) >= 4:
            simp = s
    out = _ints(simp)
    if out and out[0] != out[-1]:
        out.append(out[0])
    return out


def _flatten(g: BaseGeometry) -> list[BaseGeometry]:
    if hasattr(g, "geoms"):
        out = []
        for sub in g.geoms:
            out.extend(_flatten(sub))
        return out
    return [g]


def feature_parts(g: BaseGeometry | None, tol: float = LINE_TOL_M) -> list[IntPart]:
    """Parts of a feature geometry for ``geom.json``.

    Lines are merged into connected chains and simplified; points become one-vertex parts;
    polygons contribute their closed outer ring only.
    """
    if g is None:
        return []
    m = to_metric(g)
    lines = [p for p in _flatten(m) if isinstance(p, LineString)]
    others = [p for p in _flatten(m) if not isinstance(p, LineString)]
    parts: list[IntPart] = []
    if lines:
        merged = linemerge(MultiLineString(lines)) if len(lines) > 1 else lines[0]
        chains = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
        for ch in chains:
            part = _simplify_line(ch.coords, tol)
            if part:
                parts.append(part)
    for p in others:
        if isinstance(p, Point):
            parts.append(_ints([(p.x, p.y)]))
        elif isinstance(p, Polygon):
            ring = _simplify_ring(p.exterior.coords, tol)
            if ring:
                parts.append(ring)
    return parts


def area_polys(g: BaseGeometry | None, tol: float = AREA_TOL_M) -> list[list[IntPart]]:
    """Polygons of an area for ``areas.json``: ``[[outer, hole, ...], ...]``."""
    if g is None:
        return []
    out = []
    for p in _flatten(to_metric(g)):
        if not isinstance(p, Polygon):
            continue
        rings = [_simplify_ring(p.exterior.coords, tol)] + [_simplify_ring(r.coords, tol) for r in p.interiors]
        rings = [r for r in rings if len(r) >= 4]
        if rings:
            out.append(rings)
    return out


def encode_parts(parts: list[IntPart]) -> list[str]:
    return [encode_ints(p) for p in parts]


def has_lines(g: BaseGeometry) -> bool:
    return any(isinstance(p, LineString) for p in _flatten(g))


def metric_length(g: BaseGeometry | None) -> float:
    """Length in metres of the linear parts (0 for points and polygons)."""
    if g is None:
        return 0.0
    lines = [p for p in _flatten(g) if isinstance(p, LineString)]
    return float(sum(to_metric(ls).length for ls in lines))


def representative_point(g: BaseGeometry) -> tuple[float, float]:
    """(lat, lon) of a representative point: line midpoint, polygon inner point, the point."""
    if isinstance(g, (LineString, MultiLineString)):
        lines = [p for p in _flatten(g) if isinstance(p, LineString)]
        longest = max(lines, key=lambda ls: to_metric(ls).length)
        pt = longest.interpolate(0.5, normalized=True)
    elif isinstance(g, (Polygon, MultiPolygon)):
        pt = g.representative_point()
    else:
        pt = g.representative_point() if not isinstance(g, Point) else g
    return pt.y, pt.x
