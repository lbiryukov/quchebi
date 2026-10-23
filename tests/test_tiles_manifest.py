"""tiles-manifest.json: contents, integrity checks and the committed manifest (if built)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re

import pytest

from pipeline.config import APP_TILES
from pipeline.tiles import manifest as mf


def _populate(d):
    (d / "tbilisi.pmtiles").write_bytes(b"PMTiles\x03" + b"\0" * 200)
    (d / "tbilisi-center.pmtiles").write_bytes(b"PMTiles\x03other")
    f = d / "fonts" / "Noto Sans Regular"
    f.mkdir(parents=True)
    (f / "0-255.pbf").write_bytes(b"glyphs-a")
    (f / "4096-4351.pbf").write_bytes(b"glyphs-ka")
    s = d / "sprites"
    s.mkdir()
    (s / "light.json").write_text("{}", encoding="utf-8")
    (s / "light.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (s / "light.png.tmp").write_bytes(b"partial")


def test_build_manifest_lists_basemap_fonts_sprites(tmp_path):
    _populate(tmp_path)
    m = mf.build_manifest(tmp_path, basemap="tbilisi.pmtiles", source_build="20261023",
                          bbox=[44.6, 41.6, 45.1, 41.87], maxzoom=15, built=dt.date(2026, 10, 24),
                          extra={"minzoom": 0, "built": "ignored"})
    assert m["built"] == "24/10/2026"
    assert m["source_build"] == "20261023"
    assert m["attribution"] == "Protomaps © OpenStreetMap contributors, ODbL"
    assert m["minzoom"] == 0
    assert list(m["files"]) == ["tbilisi.pmtiles", "fonts/Noto Sans Regular/0-255.pbf",
                                "fonts/Noto Sans Regular/4096-4351.pbf", "sprites/light.json",
                                "sprites/light.png"]
    info = m["files"]["fonts/Noto Sans Regular/4096-4351.pbf"]
    assert info == {"bytes": 9, "sha256": hashlib.sha256(b"glyphs-ka").hexdigest()}
    assert m["total_bytes"] == sum(v["bytes"] for v in m["files"].values())
    assert mf.verify_manifest(tmp_path, m) == []
    mf.write_manifest(tmp_path, m)
    loaded = json.loads((tmp_path / mf.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert loaded == m
    assert mf.verify_manifest(tmp_path) == []


def test_verify_manifest_detects_problems(tmp_path):
    _populate(tmp_path)
    m = mf.build_manifest(tmp_path, basemap="tbilisi.pmtiles", source_build="20261023",
                          bbox=[1, 2, 3, 4], maxzoom=15)
    (tmp_path / "sprites" / "light.json").write_text("[]", encoding="utf-8")  # same size
    (tmp_path / "fonts" / "Noto Sans Regular" / "0-255.pbf").unlink()
    bad = dict(m, built="2026-10-24", files=dict(m["files"], **{"../x": {"bytes": 1}}))
    problems = mf.verify_manifest(tmp_path, bad)
    assert any("sha256 mismatch sprites/light.json" in p for p in problems)
    assert any("missing file fonts/Noto Sans Regular/0-255.pbf" in p for p in problems)
    assert any("DD/MM/YYYY" in p for p in problems)
    assert any("bad path" in p for p in problems)
    assert mf.verify_manifest(tmp_path, {"files": {}}) != []


def test_build_manifest_requires_basemap(tmp_path):
    with pytest.raises(FileNotFoundError):
        mf.build_manifest(tmp_path, basemap="tbilisi.pmtiles", source_build="20261023",
                          bbox=[1, 2, 3, 4], maxzoom=15)


@pytest.mark.skipif(not (APP_TILES / mf.MANIFEST_NAME).exists(), reason="basemap not built")
def test_built_manifest_matches_files():
    m = mf.load_manifest(APP_TILES)
    assert mf.verify_manifest(APP_TILES, m) == []
    assert re.fullmatch(r"\d{2}/\d{2}/\d{4}", m["built"])
    assert re.fullmatch(r"\d{8}", m["source_build"])
    assert m["basemap"].endswith(".pmtiles") and m["basemap"] in m["files"]
    assert any(k.startswith("fonts/") for k in m["files"])
    assert any(k.startswith("sprites/") for k in m["files"])
    assert "OpenStreetMap" in m["attribution"]
