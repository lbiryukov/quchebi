"""Extract a bbox/zoom subset of a PMTiles v3 archive into a new archive.

Steps: read header + root directory (first 16 KiB), resolve every wanted tile id through the
root/leaf directories, merge the byte spans of the wanted tiles into a few large range
requests, download them (cached), slice the tiles out and write them, in tile-id order, with
``pmtiles.writer.Writer``. The result is then re-opened and checked tile by tile.
"""
from __future__ import annotations

import bisect
import gzip
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from pmtiles.tile import (
    Compression,
    Entry,
    TileType,
    deserialize_directory,
    deserialize_header,
    find_tile,
    tileid_to_zxy,
)
from pmtiles.writer import Writer

from pipeline.log import get_logger
from pipeline.tiles.geo import BBox, tile_ids_in_bbox
from pipeline.tiles.source import HEADER_FETCH, RangeSource

log = get_logger(__name__)

MAX_DIR_DEPTH = 4            # PMTiles spec: root + up to 3 levels of leaves
DEFAULT_MAX_GAP = 64 * 1024  # merge spans separated by at most this many unused bytes
DEFAULT_MAX_REQUEST = 16 << 20


class ExtractError(RuntimeError):
    pass


@dataclass
class Archive:
    header: dict
    root: list[Entry]
    metadata: dict


@dataclass
class ExtractResult:
    path: Path
    bytes: int
    bbox: list[float]
    minzoom: int
    maxzoom: int
    tiles_by_zoom: dict[int, int]
    missing_by_zoom: dict[int, int]
    tile_contents: int
    tile_data_bytes: int
    ranges: int
    range_bytes: int
    header: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    @property
    def addressed_tiles(self) -> int:
        return sum(self.tiles_by_zoom.values())


# -- reading ------------------------------------------------------------------------
def _parse_directory(buf: bytes, compression: Compression) -> list[Entry]:
    if compression == Compression.GZIP:
        return deserialize_directory(buf)
    if compression == Compression.NONE:
        return deserialize_directory(gzip.compress(buf))  # library only reads gzip input
    raise ExtractError(f"unsupported internal compression {compression}")


def _decompress(buf: bytes, compression: Compression) -> bytes:
    if compression == Compression.GZIP:
        return gzip.decompress(buf)
    if compression == Compression.NONE:
        return buf
    raise ExtractError(f"unsupported internal compression {compression}")


def open_archive(source: RangeSource) -> Archive:
    """Read header, root directory and JSON metadata of a PMTiles v3 archive."""
    head = source.get(0, HEADER_FETCH)
    if len(head) < 127:
        raise ExtractError(f"{source.key}: file too short for a PMTiles header")
    header = deserialize_header(head[:127])
    ro, rl = header["root_offset"], header["root_length"]
    root_bytes = head[ro:ro + rl] if ro + rl <= len(head) else source.get(ro, rl)
    root = _parse_directory(root_bytes, header["internal_compression"])
    meta_raw = source.get(header["metadata_offset"], header["metadata_length"])
    metadata = json.loads(_decompress(meta_raw, header["internal_compression"]) or b"{}")
    log.info("archive %s: z%d-%d, %s tiles addressed, tile data %.1f GB, type %s/%s, "
             "root entries %d", source.key, header["min_zoom"], header["max_zoom"],
             f"{header['addressed_tiles_count']:,}", header["tile_data_length"] / 1e9,
             header["tile_type"].name, header["tile_compression"].name, len(root))
    return Archive(header=header, root=root, metadata=metadata)


def resolve_tiles(source: RangeSource, archive: Archive,
                  tile_ids: Iterable[int]) -> tuple[dict[int, tuple[int, int]], set[int]]:
    """Map tile id -> (offset relative to tile data, length); also return ids not found.

    Leaf directories needed at one depth are fetched together (≤4 parallel requests).
    """
    header = archive.header
    leaf_cache: dict[tuple[int, int], list[Entry]] = {}
    current: dict[int, list[Entry]] = {tid: archive.root for tid in tile_ids}
    found: dict[int, tuple[int, int]] = {}
    missing: set[int] = set()
    for depth in range(MAX_DIR_DEPTH):
        next_leaf: dict[int, tuple[int, int]] = {}
        for tid, entries in current.items():
            e = find_tile(entries, tid)
            if e is None:
                missing.add(tid)
            elif e.run_length > 0:
                found[tid] = (e.offset, e.length)
            else:
                next_leaf[tid] = (header["leaf_directory_offset"] + e.offset, e.length)
        if not next_leaf:
            break
        need = sorted(set(next_leaf.values()) - leaf_cache.keys())
        if need:
            log.info("directory depth %d: fetching %d leaf directories (%.1f KB)", depth + 1,
                     len(need), sum(n for _, n in need) / 1024)
            for key, buf in source.get_many(need).items():
                leaf_cache[key] = _parse_directory(buf, header["internal_compression"])
        current = {tid: leaf_cache[key] for tid, key in next_leaf.items()}
    else:
        if current:
            raise ExtractError("maximum directory depth exceeded")
    return found, missing


def plan_ranges(spans: Iterable[tuple[int, int]], max_gap: int = DEFAULT_MAX_GAP,
                max_request: int = DEFAULT_MAX_REQUEST) -> list[tuple[int, int]]:
    """Merge (offset, length) spans into few (offset, length) requests.

    Overlapping/adjacent spans are always merged; spans separated by a gap of at most
    ``max_gap`` bytes are merged while the request stays ≤ ``max_request`` bytes.
    """
    ordered = sorted({(o, n) for o, n in spans if n > 0})
    out: list[list[int]] = []
    for o, n in ordered:
        end = o + n
        if out:
            cur = out[-1]
            cur_end = cur[0] + cur[1]
            if o <= cur_end or (o - cur_end <= max_gap and end - cur[0] <= max_request):
                cur[1] = max(cur_end, end) - cur[0]
                continue
        out.append([o, n])
    return [(o, n) for o, n in out]


class _Slicer:
    """Slice absolute byte spans out of downloaded ranges."""

    def __init__(self, blobs: dict[tuple[int, int], bytes]):
        self.keys = sorted(blobs)
        self.starts = [k[0] for k in self.keys]
        self.blobs = blobs

    def get(self, offset: int, length: int) -> bytes:
        i = bisect.bisect_right(self.starts, offset) - 1
        if i < 0:
            raise ExtractError(f"span {offset}+{length} not downloaded")
        o, n = self.keys[i]
        if offset + length > o + n:
            raise ExtractError(f"span {offset}+{length} exceeds range {o}+{n}")
        return self.blobs[(o, n)][offset - o:offset - o + length]


# -- writing ------------------------------------------------------------------------
def _e7(v: float) -> int:
    return int(round(v * 10_000_000))


def write_archive(path: Path, tiles: list[tuple[int, bytes]], *, tile_type: TileType,
                  tile_compression: Compression, bbox: BBox, center_zoom: int,
                  metadata: dict) -> None:
    """Write tiles (sorted by id) to ``path`` atomically."""
    if not tiles:
        raise ExtractError("no tiles to write")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    lon, lat = bbox.center
    header = {
        "tile_type": tile_type,
        "tile_compression": tile_compression,
        "min_lon_e7": _e7(bbox.min_lon),
        "min_lat_e7": _e7(bbox.min_lat),
        "max_lon_e7": _e7(bbox.max_lon),
        "max_lat_e7": _e7(bbox.max_lat),
        "center_zoom": center_zoom,
        "center_lon_e7": _e7(lon),
        "center_lat_e7": _e7(lat),
    }
    with open(tmp, "wb") as f:
        w = Writer(f)
        for tid, data in tiles:
            w.write_tile(tid, data)
        w.finalize(header, metadata)
    # pmtiles.writer derives max_zoom from the first id of the last directory entry, which is
    # wrong when a run of identical tiles crosses a zoom boundary: patch both zoom bytes.
    zmin, zmax = tileid_to_zxy(tiles[0][0])[0], tileid_to_zxy(tiles[-1][0])[0]
    with open(tmp, "r+b") as f:
        f.seek(100)
        if f.read(2) != bytes([zmin, zmax]):
            log.warning("patching header zooms to z%d-%d", zmin, zmax)
            f.seek(100)
            f.write(bytes([zmin, zmax]))
    os.replace(tmp, path)


def extract(source: RangeSource, out_path: Path, bbox: BBox, *, minzoom: int = 0,
            maxzoom: int = 15, center_zoom: int = 12, max_gap: int = DEFAULT_MAX_GAP,
            max_request: int = DEFAULT_MAX_REQUEST, extra_metadata: dict | None = None,
            archive: Archive | None = None) -> ExtractResult:
    """Extract ``bbox`` at ``minzoom..maxzoom`` from ``source`` into ``out_path`` and verify it."""
    t0 = time.perf_counter()
    archive = archive or open_archive(source)
    h = archive.header
    if h["tile_type"] == TileType.UNKNOWN:
        raise ExtractError("source tile type is unknown")
    z0, z1 = max(minzoom, h["min_zoom"]), min(maxzoom, h["max_zoom"])
    if z0 > z1:
        raise ExtractError(f"zoom range {minzoom}-{maxzoom} outside source z{h['min_zoom']}-"
                           f"{h['max_zoom']}")
    if maxzoom > h["max_zoom"]:
        log.warning("maxzoom %d > source maxzoom %d: clamped", maxzoom, h["max_zoom"])
    wanted = tile_ids_in_bbox(bbox, z0, z1)
    log.info("bbox %s z%d-%d: %d candidate tiles", bbox.as_list(), z0, z1, len(wanted))

    found, missing = resolve_tiles(source, archive, wanted)
    data_off = h["tile_data_offset"]
    spans = [(data_off + o, n) for o, n in found.values()]
    ranges = plan_ranges(spans, max_gap=max_gap, max_request=max_request)
    useful = sum(n for _, n in set(spans))
    range_bytes = sum(n for _, n in ranges)
    log.info("%d tiles found (%d missing), %d unique contents, %.2f MB useful -> %d range "
             "requests, %.2f MB (overhead %.1f%%)", len(found), len(missing),
             len(set(found.values())), useful / 1048576, len(ranges), range_bytes / 1048576,
             100.0 * (range_bytes - useful) / max(useful, 1))
    blobs = source.get_many(ranges)
    slicer = _Slicer(blobs)

    tiles: list[tuple[int, bytes]] = []
    for tid in sorted(found):
        o, n = found[tid]
        tiles.append((tid, slicer.get(data_off + o, n)))

    metadata = dict(archive.metadata)
    metadata["quchebi"] = {"bbox": bbox.as_list(), "minzoom": z0, "maxzoom": z1,
                           **(extra_metadata or {})}
    if not tiles:
        raise ExtractError(f"source has no tiles in bbox {bbox.as_list()} z{z0}-{z1}")
    write_archive(out_path, tiles, tile_type=h["tile_type"],
                  tile_compression=h["tile_compression"], bbox=bbox,
                  center_zoom=min(max(center_zoom, tileid_to_zxy(tiles[0][0])[0]),
                                  tileid_to_zxy(tiles[-1][0])[0]),
                  metadata=metadata)

    expected = {tid: hashlib.sha256(data).digest() for tid, data in tiles}
    zmin, zmax = tileid_to_zxy(tiles[0][0])[0], tileid_to_zxy(tiles[-1][0])[0]
    if (zmin, zmax) != (z0, z1):
        log.warning("source has no tiles of this bbox for some zooms: archive is z%d-%d, "
                    "requested z%d-%d", zmin, zmax, z0, z1)
    report = verify_archive(out_path, expected=expected, bbox=bbox, minzoom=zmin, maxzoom=zmax)
    missing_by_zoom: dict[int, int] = {}
    for tid in missing:
        z = tileid_to_zxy(tid)[0]
        missing_by_zoom[z] = missing_by_zoom.get(z, 0) + 1
    result = ExtractResult(
        path=out_path, bytes=out_path.stat().st_size, bbox=bbox.as_list(), minzoom=zmin,
        maxzoom=zmax, tiles_by_zoom=report["tiles_by_zoom"], missing_by_zoom=missing_by_zoom,
        tile_contents=report["header"]["tile_contents_count"],
        tile_data_bytes=report["header"]["tile_data_length"], ranges=len(ranges),
        range_bytes=range_bytes, header=report["header"], metadata=report["metadata"],
    )
    log.info("wrote %s: %.2f MB, %d tiles (%d unique) in %.1fs", out_path,
             result.bytes / 1048576, result.addressed_tiles, result.tile_contents,
             time.perf_counter() - t0)
    return result


# -- verification -------------------------------------------------------------------
def iter_entries(buf: bytes, header: dict) -> Iterable[Entry]:
    """Yield all tile entries (run_length > 0) of an in-memory archive, in id order."""
    comp = header["internal_compression"]

    def walk(offset: int, length: int, depth: int) -> Iterable[Entry]:
        if depth >= MAX_DIR_DEPTH:
            raise ExtractError("maximum directory depth exceeded")
        for e in _parse_directory(buf[offset:offset + length], comp):
            if e.run_length > 0:
                yield e
            else:
                yield from walk(header["leaf_directory_offset"] + e.offset, e.length, depth + 1)

    yield from walk(header["root_offset"], header["root_length"], 0)


def header_summary(header: dict) -> dict:
    out = {}
    for k, v in header.items():
        out[k] = v.name if hasattr(v, "name") else v
    for k in ("min_lon_e7", "min_lat_e7", "max_lon_e7", "max_lat_e7", "center_lon_e7",
              "center_lat_e7"):
        if k in header:
            out[k.replace("_e7", "")] = header[k] / 1e7
    return out


def verify_archive(path: Path, *, expected: dict[int, bytes] | None = None,
                   bbox: BBox | None = None, minzoom: int | None = None,
                   maxzoom: int | None = None) -> dict:
    """Re-open ``path`` and check structure; ``expected`` maps tile id -> sha256 digest."""
    buf = Path(path).read_bytes()
    header = deserialize_header(buf[:127])
    if header["root_offset"] + header["root_length"] > HEADER_FETCH:
        raise ExtractError("root directory does not fit in the first 16 KiB")
    if header["tile_data_offset"] + header["tile_data_length"] != len(buf):
        raise ExtractError("tile data section does not end at end of file")
    metadata = json.loads(_decompress(
        buf[header["metadata_offset"]:header["metadata_offset"] + header["metadata_length"]],
        header["internal_compression"]))
    tiles_by_zoom: dict[int, int] = {}
    seen: set[int] = set()
    last = -1
    data_off = header["tile_data_offset"]
    for e in iter_entries(buf, header):
        if e.tile_id <= last:
            raise ExtractError("directory entries are not sorted")
        if e.offset + e.length > header["tile_data_length"]:
            raise ExtractError(f"entry {e.tile_id} points outside tile data")
        data = buf[data_off + e.offset:data_off + e.offset + e.length]
        digest = hashlib.sha256(data).digest() if expected is not None else b""
        for tid in range(e.tile_id, e.tile_id + e.run_length):
            z = tileid_to_zxy(tid)[0]
            tiles_by_zoom[z] = tiles_by_zoom.get(z, 0) + 1
            seen.add(tid)
            if expected is not None and expected.get(tid) != digest:
                raise ExtractError(f"tile {tileid_to_zxy(tid)} content mismatch")
        last = e.tile_id + e.run_length - 1
    if expected is not None and seen != set(expected):
        raise ExtractError(f"tile set mismatch: {len(seen)} written vs {len(expected)} expected")
    if sum(tiles_by_zoom.values()) != header["addressed_tiles_count"]:
        raise ExtractError("addressed_tiles_count does not match directory")
    if minzoom is not None and header["min_zoom"] != minzoom:
        raise ExtractError(f"header min_zoom {header['min_zoom']} != {minzoom}")
    if maxzoom is not None and header["max_zoom"] != maxzoom:
        raise ExtractError(f"header max_zoom {header['max_zoom']} != {maxzoom}")
    if bbox is not None:
        want = [_e7(v) for v in bbox.as_list()]
        got = [header[k] for k in ("min_lon_e7", "min_lat_e7", "max_lon_e7", "max_lat_e7")]
        if want != got:
            raise ExtractError(f"header bounds {got} != {want}")
        c_lon, c_lat = header["center_lon_e7"], header["center_lat_e7"]
        if not (got[0] <= c_lon <= got[2] and got[1] <= c_lat <= got[3]):
            raise ExtractError("header center outside bounds")
    log.info("verified %s: %s", path, {f"z{z}": n for z, n in sorted(tiles_by_zoom.items())})
    return {"header": header_summary(header), "metadata": metadata,
            "tiles_by_zoom": dict(sorted(tiles_by_zoom.items()))}
