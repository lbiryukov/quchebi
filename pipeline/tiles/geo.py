"""Web Mercator tile math for bbox extraction (XYZ scheme, as used by PMTiles)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterator

from pmtiles.tile import zxy_to_tileid

# Web Mercator latitude limit
MAX_LAT = 85.0511287798066


@dataclass(frozen=True)
class BBox:
    """Geographic bounding box in degrees (lon_min, lat_min, lon_max, lat_max)."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def __post_init__(self) -> None:
        if not (-180.0 <= self.min_lon < self.max_lon <= 180.0):
            raise ValueError(f"invalid longitude range: {self.min_lon}..{self.max_lon}")
        if not (-90.0 <= self.min_lat < self.max_lat <= 90.0):
            raise ValueError(f"invalid latitude range: {self.min_lat}..{self.max_lat}")

    @classmethod
    def parse(cls, text: str) -> "BBox":
        """Parse ``"lon_min,lat_min,lon_max,lat_max"``."""
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 4:
            raise ValueError(f"bbox needs 4 comma-separated numbers, got {text!r}")
        return cls(*(float(p) for p in parts))

    def as_list(self) -> list[float]:
        return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]

    @property
    def center(self) -> tuple[float, float]:
        return ((self.min_lon + self.max_lon) / 2.0, (self.min_lat + self.max_lat) / 2.0)


def lon_to_x(lon: float, z: int) -> int:
    n = 1 << z
    x = int(math.floor((lon + 180.0) / 360.0 * n))
    return min(max(x, 0), n - 1)


def lat_to_y(lat: float, z: int) -> int:
    n = 1 << z
    lat = min(max(lat, -MAX_LAT), MAX_LAT)
    r = math.radians(lat)
    y = int(math.floor((1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * n))
    return min(max(y, 0), n - 1)


def tile_range(bbox: BBox, z: int) -> tuple[int, int, int, int]:
    """Inclusive tile range (x0, y0, x1, y1) covering ``bbox`` at zoom ``z``."""
    x0, x1 = lon_to_x(bbox.min_lon, z), lon_to_x(bbox.max_lon, z)
    y0, y1 = lat_to_y(bbox.max_lat, z), lat_to_y(bbox.min_lat, z)
    return x0, y0, x1, y1


def tiles_in_bbox(bbox: BBox, minzoom: int, maxzoom: int) -> Iterator[tuple[int, int, int]]:
    """Yield every (z, x, y) whose tile intersects ``bbox`` for ``minzoom..maxzoom``."""
    for z in range(minzoom, maxzoom + 1):
        x0, y0, x1, y1 = tile_range(bbox, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                yield z, x, y


def tile_ids_in_bbox(bbox: BBox, minzoom: int, maxzoom: int) -> list[int]:
    """Sorted PMTiles (Hilbert) tile ids covering ``bbox``."""
    return sorted(zxy_to_tileid(z, x, y) for z, x, y in tiles_in_bbox(bbox, minzoom, maxzoom))


def count_by_zoom(bbox: BBox, minzoom: int, maxzoom: int) -> dict[int, int]:
    out: dict[int, int] = {}
    for z in range(minzoom, maxzoom + 1):
        x0, y0, x1, y1 = tile_range(bbox, z)
        out[z] = (x1 - x0 + 1) * (y1 - y0 + 1)
    return out
