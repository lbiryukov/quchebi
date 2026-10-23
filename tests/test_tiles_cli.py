"""End-to-end CLI run on a synthetic archive, the JS (pmtiles 4.5.0) check, and the built file."""
from __future__ import annotations

import gzip

import shutil

import pytest
from pmtiles.reader import MemorySource, Reader

from pipeline.config import APP_TILES, ROOT
from pipeline.tiles import cli
from pipeline.tiles import manifest as mf
from pipeline.tiles.extract import verify_archive
from pipeline.tiles.geo import BBox, count_by_zoom, tile_range
from pipeline.tiles.synthetic import minimal_mvt, write_synthetic

NODE = shutil.which("node")
JS_LIB = ROOT / "app" / "node_modules" / "pmtiles" / "dist" / "esm" / "index.js"
CENTER = BBox(*cli.CENTER_BBOX)


def _synthetic_world(path, maxzoom=14):
    """Tiles of CENTER_BBOX (plus one ring around it) up to ``maxzoom``, gzip-compressed MVT."""
    tiles = {}
    for z in range(maxzoom + 1):
        x0, y0, x1, y1 = tile_range(CENTER, z)
        for x in range(max(0, x0 - 1), min((1 << z) - 1, x1 + 1) + 1):
            for y in range(max(0, y0 - 1), min((1 << z) - 1, y1 + 1) + 1):
                tiles[(z, x, y)] = gzip.compress(
                    minimal_mvt({"earth": 1, "roads": 2}, tag=f"{z}/{x}/{y}".encode()), mtime=0)
    return write_synthetic(path, tiles, leaf_size=50)


def test_parse_args_defaults_and_errors():
    a = cli.parse_args([])
    assert a.maxzoom == 15 and a.bbox is None and not a.center_only
    assert cli.parse_args(["--bbox", "44.7,41.65,44.92,41.79"]).bbox == CENTER
    for bad in (["--maxzoom", "16"], ["--build", "2026-10-23"],
                ["--center-only", "--bbox", "1,2,3,4"]):
        with pytest.raises(SystemExit):
            cli.parse_args(bad)


def test_cli_center_only_end_to_end(tmp_path):
    src = _synthetic_world(tmp_path / "20260101.pmtiles", maxzoom=14)
    tiles_dir = tmp_path / "tiles"
    (tiles_dir / "sprites").mkdir(parents=True)
    (tiles_dir / "sprites" / "light.json").write_text('{"a": {}}', encoding="utf-8")
    argv = ["--center-only", "--maxzoom", "14", "--source", str(src), "--tiles-dir",
            str(tiles_dir), "--skip-assets", "--no-js-check"]
    assert cli.main(argv) == 0
    out = tiles_dir / cli.CENTER_NAME
    rep = verify_archive(out, bbox=CENTER, minzoom=0, maxzoom=14)
    assert rep["tiles_by_zoom"] == count_by_zoom(CENTER, 0, 14)
    m = mf.load_manifest(tiles_dir)
    assert m["source_build"] == "20260101"         # inferred from the file name
    assert m["basemap"] == cli.CENTER_NAME
    assert m["bbox"] == list(cli.CENTER_BBOX) and m["maxzoom"] == 14
    assert set(m["files"]) == {cli.CENTER_NAME, "sprites/light.json"}
    assert m["tiles_by_zoom"]["14"] == count_by_zoom(CENTER, 14, 14)[14]
    assert mf.verify_manifest(tiles_dir) == []
    meta = Reader(MemorySource(out.read_bytes())).metadata()
    assert meta["quchebi"]["source_build"] == "20260101"


@pytest.mark.skipif(not NODE or not JS_LIB.exists(), reason="node or app/node_modules missing")
def test_js_library_reads_extract(tmp_path):
    src = _synthetic_world(tmp_path / "20260101.pmtiles", maxzoom=14)
    out = tmp_path / "out.pmtiles"
    assert cli.main(["--center-only", "--maxzoom", "14", "--source", str(src), "--out", str(out),
                     "--skip-assets", "--no-js-check", "--no-manifest"]) == 0
    res = cli.run_js_check(out)
    assert res and res["ok"], res
    assert res["header"]["maxZoom"] == 14
    assert res["header"]["bounds"] == list(cli.CENTER_BBOX)
    assert res["tile"]["z"] == 14 and res["tile"]["layers"] == {"earth": 1, "roads": 2}
    assert res["metadata"]["vector_layers"] == ["earth", "roads"]
    assert res["fileSource"] == "same tile"


BUILT = APP_TILES / "tbilisi.pmtiles"


@pytest.mark.skipif(not BUILT.exists(), reason="basemap not built")
def test_built_basemap_is_valid():
    rep = verify_archive(BUILT)
    h = rep["header"]
    assert h["tile_type"] == "MVT" and h["min_zoom"] == 0 and h["max_zoom"] == 15
    bbox = BBox(*rep["metadata"]["quchebi"]["bbox"])
    assert rep["tiles_by_zoom"] == count_by_zoom(bbox, 0, 15)
    assert {"earth", "roads", "places"} <= {l["id"] for l in rep["metadata"]["vector_layers"]}
    assert "OpenStreetMap" in rep["metadata"]["attribution"]
