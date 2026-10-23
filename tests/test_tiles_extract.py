"""Basemap extraction on tiny synthetic PMTiles archives (no network)."""
from __future__ import annotations

import gzip

import pytest
from pmtiles.reader import MemorySource, Reader, all_tiles
from pmtiles.tile import Compression, zxy_to_tileid

from pipeline.config import TBILISI_BBOX
from pipeline.tiles.extract import (
    ExtractError,
    extract,
    open_archive,
    plan_ranges,
    verify_archive,
)
from pipeline.tiles.geo import BBox, count_by_zoom, tile_range, tiles_in_bbox
from pipeline.tiles.source import LocalFileSource
from pipeline.tiles.synthetic import DEFAULT_METADATA, minimal_mvt, write_synthetic

BBOX = BBox(44.70, 41.65, 44.92, 41.79)


def _tile(z: int, x: int, y: int) -> bytes:
    return gzip.compress(minimal_mvt({"earth": 1, "roads": z}, tag=f"{z}/{x}/{y}".encode()),
                         mtime=0)


def _make_tiles(maxzoom: int = 10, ring: int = 1) -> tuple[dict, set]:
    """Tiles inside BBOX for z0..maxzoom plus a ring of ``ring`` tiles outside it."""
    tiles, inside = {}, set()
    for z in range(maxzoom + 1):
        x0, y0, x1, y1 = tile_range(BBOX, z)
        n = 1 << z
        for x in range(max(0, x0 - ring), min(n - 1, x1 + ring) + 1):
            for y in range(max(0, y0 - ring), min(n - 1, y1 + ring) + 1):
                tiles[(z, x, y)] = _tile(z, x, y)
                if x0 <= x <= x1 and y0 <= y <= y1:
                    inside.add((z, x, y))
    return tiles, inside


def _read_all(path) -> dict:
    buf = path.read_bytes()
    return {zxy: data for zxy, data in all_tiles(MemorySource(buf))}


def test_tbilisi_tile_counts_match_measurement():
    counts = count_by_zoom(BBox(*TBILISI_BBOX), 0, 15)
    assert counts[15] == 1598          # prototype measurement on the public build
    assert counts[0] == 1 and counts[14] < counts[15]
    assert sum(1 for _ in tiles_in_bbox(BBox(*TBILISI_BBOX), 15, 15)) == 1598


def test_bbox_parse_and_validation():
    assert BBox.parse("44.6, 41.6,45.1,41.87").as_list() == [44.6, 41.6, 45.1, 41.87]
    with pytest.raises(ValueError):
        BBox.parse("1,2,3")
    with pytest.raises(ValueError):
        BBox(45.0, 41.0, 44.0, 42.0)


def test_plan_ranges_merges_adjacent_and_small_gaps():
    spans = [(100, 10), (110, 5), (115, 5), (200, 10), (100, 10), (205, 2), (10_000, 1)]
    assert plan_ranges(spans, max_gap=0) == [(100, 20), (200, 10), (10_000, 1)]
    assert plan_ranges(spans, max_gap=100) == [(100, 110), (10_000, 1)]
    assert plan_ranges(spans, max_gap=1_000_000) == [(100, 9901)]
    # max_request stops gap-merging but never splits a span
    assert plan_ranges([(0, 50), (60, 50)], max_gap=100, max_request=80) == [(0, 50), (60, 50)]
    assert plan_ranges([(0, 50), (50, 50)], max_gap=0, max_request=10) == [(0, 100)]
    assert plan_ranges([]) == []


@pytest.mark.parametrize("leaf_size", [None, 3])
def test_extract_keeps_only_bbox_tiles(tmp_path, leaf_size):
    tiles, inside = _make_tiles()
    src_path = write_synthetic(tmp_path / "src.pmtiles", tiles, leaf_size=leaf_size)
    out = tmp_path / "out.pmtiles"
    res = extract(LocalFileSource(src_path), out, BBOX, maxzoom=10, center_zoom=8,
                  extra_metadata={"source_build": "20260101"})

    got = _read_all(out)
    assert set(got) == inside
    assert all(got[k] == tiles[k] for k in inside)
    assert res.addressed_tiles == len(inside)
    assert res.missing_by_zoom == {}
    assert res.tiles_by_zoom == {z: n for z, n in count_by_zoom(BBOX, 0, 10).items()}

    r = Reader(MemorySource(out.read_bytes()))
    h = r.header()
    assert (h["min_zoom"], h["max_zoom"]) == (0, 10)
    assert h["min_lon_e7"] == 447000000 and h["max_lat_e7"] == 417900000
    assert h["center_zoom"] == 8
    assert h["min_lon_e7"] < h["center_lon_e7"] < h["max_lon_e7"]
    assert h["tile_compression"] == Compression.GZIP
    meta = r.metadata()
    assert meta["vector_layers"] == DEFAULT_METADATA["vector_layers"]
    assert meta["attribution"] == DEFAULT_METADATA["attribution"]
    assert meta["quchebi"]["bbox"] == BBOX.as_list()
    assert meta["quchebi"]["source_build"] == "20260101"
    # lookups through the library reader
    z, x, y = sorted(inside)[-1]
    assert r.get(z, x, y) == tiles[(z, x, y)]
    outside = next(k for k in tiles if k not in inside and k[0] == 10)
    assert r.get(*outside) is None


def test_extract_preserves_dedup_and_run_lengths(tmp_path):
    tiles, inside = _make_tiles(maxzoom=9, ring=2)
    same = gzip.compress(minimal_mvt({"earth": 1}), mtime=0)
    for k in tiles:
        if k[0] >= 8:
            tiles[k] = same  # identical content -> run-length / dedup entries
    src_path = write_synthetic(tmp_path / "src.pmtiles", tiles, leaf_size=4)
    out = tmp_path / "out.pmtiles"
    res = extract(LocalFileSource(src_path), out, BBOX, maxzoom=9)
    got = _read_all(out)
    assert set(got) == inside
    assert all(got[k] == tiles[k] for k in inside)
    n_distinct = len({tiles[k] for k in inside})
    assert res.tile_contents == n_distinct


def test_extract_merges_tile_reads_into_few_requests(tmp_path):
    tiles, _ = _make_tiles(maxzoom=10, ring=3)
    src_path = write_synthetic(tmp_path / "src.pmtiles", tiles)
    one = LocalFileSource(src_path)
    archive = open_archive(one)
    before = one.requests
    res = extract(one, tmp_path / "a.pmtiles", BBOX, maxzoom=10, max_gap=10**9, archive=archive)
    assert res.ranges == 1 and one.requests - before == 1
    many = LocalFileSource(src_path)
    res2 = extract(many, tmp_path / "b.pmtiles", BBOX, maxzoom=10, max_gap=0)
    assert res2.ranges > 1
    assert res2.range_bytes <= res.range_bytes
    assert _read_all(tmp_path / "a.pmtiles") == _read_all(tmp_path / "b.pmtiles")


def test_extract_reports_tiles_missing_in_source(tmp_path):
    tiles, inside = _make_tiles(maxzoom=12)
    dropped = sorted(k for k in inside if k[0] == 12)[:2]
    assert len(dropped) == 2
    for k in dropped:
        del tiles[k]
    src_path = write_synthetic(tmp_path / "src.pmtiles", tiles)
    res = extract(LocalFileSource(src_path), tmp_path / "out.pmtiles", BBOX, maxzoom=12)
    assert res.missing_by_zoom == {12: 2}
    assert res.maxzoom == 12
    assert set(_read_all(tmp_path / "out.pmtiles")) == inside - set(dropped)


def test_extract_header_zooms_follow_written_tiles(tmp_path):
    tiles, inside = _make_tiles(maxzoom=8)
    for k in [k for k in inside if k[0] == 8]:
        del tiles[k]                       # bbox has no z8 tile in the source
    src_path = write_synthetic(tmp_path / "src.pmtiles", tiles)
    res = extract(LocalFileSource(src_path), tmp_path / "out.pmtiles", BBOX, maxzoom=8)
    assert (res.minzoom, res.maxzoom) == (0, 7)
    assert Reader(MemorySource((tmp_path / "out.pmtiles").read_bytes())).header()["max_zoom"] == 7


def test_header_max_zoom_fixed_when_run_crosses_zoom_boundary(tmp_path):
    from pipeline.tiles.extract import write_archive
    from pmtiles.tile import TileType
    last_z1 = zxy_to_tileid(2, 0, 0) - 1          # last tile id of z1
    same = b"same-content"
    tiles = [(0, b"root"), (last_z1, same), (last_z1 + 1, same)]   # run spans z1 -> z2
    out = tmp_path / "x.pmtiles"
    write_archive(out, tiles, tile_type=TileType.MVT, tile_compression=Compression.GZIP,
                  bbox=BBox(-180, -85, 180, 85), center_zoom=0, metadata={})
    h = Reader(MemorySource(out.read_bytes())).header()
    assert (h["min_zoom"], h["max_zoom"]) == (0, 2)
    assert verify_archive(out)["tiles_by_zoom"] == {0: 1, 1: 1, 2: 1}


def test_extract_clamps_maxzoom_to_source(tmp_path):
    tiles, inside = _make_tiles(maxzoom=6)
    src_path = write_synthetic(tmp_path / "src.pmtiles", tiles)
    res = extract(LocalFileSource(src_path), tmp_path / "out.pmtiles", BBOX, maxzoom=15)
    assert res.maxzoom == 6
    assert set(_read_all(tmp_path / "out.pmtiles")) == inside


def test_extract_fails_when_bbox_has_no_tiles(tmp_path):
    tiles, _ = _make_tiles(maxzoom=5, ring=0)
    src_path = write_synthetic(tmp_path / "src.pmtiles", tiles)
    with pytest.raises(ExtractError):
        extract(LocalFileSource(src_path), tmp_path / "out.pmtiles", BBox(-60, -10, -59, -9),
                minzoom=5, maxzoom=5)
    assert not (tmp_path / "out.pmtiles").exists()


def test_verify_detects_corrupted_tile(tmp_path):
    tiles, inside = _make_tiles(maxzoom=7)
    src_path = write_synthetic(tmp_path / "src.pmtiles", tiles)
    out = tmp_path / "out.pmtiles"
    extract(LocalFileSource(src_path), out, BBOX, maxzoom=7)
    import hashlib
    expected = {zxy_to_tileid(*k): hashlib.sha256(tiles[k]).digest() for k in inside}
    verify_archive(out, expected=expected, bbox=BBOX, minzoom=0, maxzoom=7)
    buf = bytearray(out.read_bytes())
    buf[-3] ^= 0xFF
    out.write_bytes(bytes(buf))
    with pytest.raises(ExtractError):
        verify_archive(out, expected=expected)
    with pytest.raises(ExtractError):
        verify_archive(tmp_path / "src.pmtiles", bbox=BBOX)  # world bounds, not the bbox
