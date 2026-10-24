"""In-memory OSM extract for Tbilisi, shared by all import stages.

The extract is produced either from the Geofabrik PBF (:mod:`pipeline.osm.extract`) or from the
Overpass cache; both give the same structure. It can be cached as JSON in ``data/interim``.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import shapely
from shapely.geometry.base import BaseGeometry

from pipeline.log import get_logger

log = get_logger(__name__)

EXTRACT_VERSION = 4  # 4: landmarks


@dataclass
class OsmWay:
    """A linear OSM way with node ids and (lat, lon) coordinates (same length)."""

    id: int
    tags: dict[str, str]
    nodes: list[int]
    coords: list[tuple[float, float]]

    @property
    def ref(self) -> str:
        return f"w{self.id}"


@dataclass
class OsmObj:
    """Any OSM object with a WGS84 geometry (Point / LineString / (Multi)Polygon / MultiLineString)."""

    type: str  # n | w | r
    id: int
    tags: dict[str, str]
    geom: BaseGeometry

    @property
    def ref(self) -> str:
        return f"{self.type}{self.id}"


@dataclass
class Extract:
    source: str                         # pbf | overpass | synthetic
    snapshot: str | None                # ISO timestamp of the OSM data
    boundary: BaseGeometry | None       # Tbilisi (Multi)Polygon, WGS84
    highways: list[OsmWay] = field(default_factory=list)    # named highway ways
    waterways: list[OsmWay] = field(default_factory=list)   # named waterway ways
    districts: list[OsmObj] = field(default_factory=list)   # boundary=administrative admin_level=10
    places: list[OsmObj] = field(default_factory=list)      # place=suburb|neighbourhood|quarter
    pois: list[OsmObj] = field(default_factory=list)        # metro, squares, parks, lakes, bridges
    addresses: list[OsmObj] = field(default_factory=list)   # addr:housenumber objects (Point = centroid)
    landmarks: list[OsmObj] = field(default_factory=list)   # named museums, churches, monuments ... (any geometry)
    info: dict = field(default_factory=dict)                # provenance (file, counts)

    def counts(self) -> dict[str, int]:
        return {
            "highways": len(self.highways), "waterways": len(self.waterways),
            "districts": len(self.districts), "places": len(self.places),
            "pois": len(self.pois), "addresses": len(self.addresses), "landmarks": len(self.landmarks),
        }

    # ------------------------------------------------------------------ JSON cache
    def to_json(self) -> dict:
        def ways(ws):
            return [[w.id, w.tags, w.nodes, [[round(a, 7), round(b, 7)] for a, b in w.coords]] for w in ws]

        def objs(os_):
            return [[o.type, o.id, o.tags, shapely.to_wkt(o.geom, rounding_precision=7)] for o in os_]

        return {
            "version": EXTRACT_VERSION, "source": self.source, "snapshot": self.snapshot,
            "boundary": shapely.to_wkt(self.boundary, rounding_precision=7) if self.boundary is not None else None,
            "highways": ways(self.highways), "waterways": ways(self.waterways),
            "districts": objs(self.districts), "places": objs(self.places), "pois": objs(self.pois),
            "addresses": objs(self.addresses), "landmarks": objs(self.landmarks), "info": self.info,
        }

    @classmethod
    def from_json(cls, d: dict) -> "Extract":
        if d.get("version") != EXTRACT_VERSION:
            raise ValueError(f"extract cache version {d.get('version')} != {EXTRACT_VERSION}")

        def ways(rows):
            return [OsmWay(int(i), dict(t), [int(n) for n in ns], [(c[0], c[1]) for c in cs]) for i, t, ns, cs in rows]

        def objs(rows):
            geoms = shapely.from_wkt([r[3] for r in rows]) if rows else []
            return [OsmObj(r[0], int(r[1]), dict(r[2]), g) for r, g in zip(rows, geoms)]

        return cls(
            source=d["source"], snapshot=d.get("snapshot"),
            boundary=shapely.from_wkt(d["boundary"]) if d.get("boundary") else None,
            highways=ways(d["highways"]), waterways=ways(d["waterways"]),
            districts=objs(d["districts"]), places=objs(d["places"]), pois=objs(d["pois"]),
            addresses=objs(d["addresses"]), landmarks=objs(d.get("landmarks", [])), info=d.get("info", {}),
        )

    def save(self, path: Path) -> None:
        t0 = time.perf_counter()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_json(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
        log.info("extract cache saved: %s (%.1f MB, %.1fs)", path, path.stat().st_size / 1e6, time.perf_counter() - t0)

    @classmethod
    def load(cls, path: Path) -> "Extract":
        t0 = time.perf_counter()
        ex = cls.from_json(json.loads(path.read_text(encoding="utf-8")))
        log.info("extract cache loaded: %s %s (%.1fs)", path.name, ex.counts(), time.perf_counter() - t0)
        return ex
