"""Tiny synthetic PMTiles archives for tests (optionally with forced leaf directories)."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from pmtiles.tile import (
    Compression,
    Entry,
    TileType,
    serialize_header,
    tileid_to_zxy,
    zxy_to_tileid,
)
from pmtiles.writer import Writer, build_roots_leaves

DEFAULT_METADATA = {
    "name": "synthetic",
    "version": "4.0.0",
    "attribution": "test attribution",
    "vector_layers": [{"id": "earth", "fields": {}}, {"id": "roads", "fields": {"kind": "String"}}],
}


def _pb_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _pb_ld(fnum: int, payload: bytes) -> bytes:
    return _pb_varint(fnum << 3 | 2) + _pb_varint(len(payload)) + payload


def minimal_mvt(layers: dict[str, int], tag: bytes = b"") -> bytes:
    """Uncompressed MVT with the given layers (name -> number of empty point features)."""
    out = b""
    for name, n in layers.items():
        feature = _pb_varint(3 << 3) + _pb_varint(1)            # type = POINT
        feature += _pb_ld(4, _pb_varint(9) + _pb_varint(50) + _pb_varint(50))  # MoveTo(25,25)
        body = _pb_varint(15 << 3) + _pb_varint(2)              # version = 2
        body += _pb_ld(1, name.encode())
        body += b"".join(_pb_ld(2, feature) for _ in range(n))
        if tag:
            body += _pb_ld(3, tag)                               # key (keeps tiles distinct)
        body += _pb_varint(5 << 3) + _pb_varint(4096)           # extent
        out += _pb_ld(3, body)
    return out


def write_synthetic(path: Path, tiles: dict[tuple[int, int, int], bytes], *,
                    metadata: dict | None = None, leaf_size: int | None = None,
                    tile_compression: Compression = Compression.GZIP,
                    tile_type: TileType = TileType.MVT) -> Path:
    """Write ``tiles`` {(z, x, y): stored bytes} as PMTiles v3.

    With ``leaf_size`` the directory is split into leaves of that many entries (to exercise
    leaf traversal); otherwise ``pmtiles.writer.Writer`` is used.
    """
    metadata = DEFAULT_METADATA if metadata is None else metadata
    items = sorted((zxy_to_tileid(*k), v) for k, v in tiles.items())
    header = {"tile_type": tile_type, "tile_compression": tile_compression,
              "min_lon_e7": -1800000000, "min_lat_e7": -850000000,
              "max_lon_e7": 1800000000, "max_lat_e7": 850000000,
              "center_zoom": 0, "center_lon_e7": 0, "center_lat_e7": 0}
    if leaf_size is None:
        with open(path, "wb") as f:
            w = Writer(f)
            for tid, data in items:
                w.write_tile(tid, data)
            w.finalize(header, metadata)
        return path

    entries: list[Entry] = []
    blobs: list[bytes] = []
    offsets: dict[bytes, int] = {}
    offset = 0
    for tid, data in items:
        if data in offsets:
            last = entries[-1]
            if last.offset == offsets[data] and tid == last.tile_id + last.run_length:
                last.run_length += 1
            else:
                entries.append(Entry(tid, offsets[data], len(data), 1))
            continue
        offsets[data] = offset
        entries.append(Entry(tid, offset, len(data), 1))
        blobs.append(data)
        offset += len(data)
    root, leaves, _ = build_roots_leaves(entries, leaf_size)
    meta = gzip.compress(json.dumps(metadata).encode())
    header.update(
        clustered=True, internal_compression=Compression.GZIP,
        addressed_tiles_count=len(items), tile_entries_count=len(entries),
        tile_contents_count=len(blobs),
        min_zoom=tileid_to_zxy(items[0][0])[0], max_zoom=tileid_to_zxy(items[-1][0])[0],
        root_offset=127, root_length=len(root),
    )
    header["metadata_offset"] = 127 + len(root)
    header["metadata_length"] = len(meta)
    header["leaf_directory_offset"] = header["metadata_offset"] + len(meta)
    header["leaf_directory_length"] = len(leaves)
    header["tile_data_offset"] = header["leaf_directory_offset"] + len(leaves)
    header["tile_data_length"] = offset
    with open(path, "wb") as f:
        f.write(serialize_header(header))
        f.write(root)
        f.write(meta)
        f.write(leaves)
        f.write(b"".join(blobs))
    return path

