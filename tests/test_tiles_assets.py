"""Glyph/sprite assets: PBF decoding, download/install with a fake repo, shipped files."""
from __future__ import annotations

import json

import pytest

from pipeline.config import APP_TILES
from pipeline.tiles import assets, net

PNG = assets.PNG_SIGNATURE + b"rest"


def test_glyph_pbf_roundtrip():
    cps = [0x10D0, 0x10D1, 0x10FF]
    buf = assets.encode_glyph_pbf("Noto Sans Regular", cps, "4096-4351")
    assert assets.decode_glyph_pbf(buf) == ("Noto Sans Regular", cps)
    with pytest.raises(ValueError):
        assets.decode_glyph_pbf(buf[:-3])


def test_merge_glyph_pbf_adds_only_missing_codepoints():
    italic = assets.encode_glyph_pbf("Noto Sans Italic", [0x10D0], "4096-4351")
    regular = assets.encode_glyph_pbf("Noto Sans Regular", [0x10D0, 0x10D1, 0x10D2], "4096-4351")
    merged, added = assets.merge_glyph_pbf(italic, regular)
    assert added == 2
    assert assets.decode_glyph_pbf(merged) == ("Noto Sans Italic", [0x10D0, 0x10D1, 0x10D2])
    same, added = assets.merge_glyph_pbf(regular, italic)
    assert added == 0 and same == regular


def _fake_repo(monkeypatch, files: dict[str, bytes]) -> list[str]:
    calls: list[str] = []

    def get_bytes(url, allow_404=False, **kw):
        calls.append(url)
        if url == assets.COMMIT_API:
            return json.dumps({"sha": "a" * 40}).encode()
        prefix = "https://raw.githubusercontent.com/protomaps/basemaps-assets/" + "a" * 40 + "/"
        assert url.startswith(prefix), url
        rel = url[len(prefix):].replace("%20", " ")
        if rel in files:
            return files[rel]
        if allow_404:
            return None
        raise net.FatalHTTPError("404")

    monkeypatch.setattr(net, "get_bytes", get_bytes)
    return calls


def test_fetch_assets_installs_validates_and_reports_missing(tmp_path, monkeypatch):
    ranges = ((0, 255), (4096, 4351), (7168, 7423))
    files = {
        "fonts/Noto Sans Regular/0-255.pbf": assets.encode_glyph_pbf("Noto Sans Regular", [65, 66]),
        "fonts/Noto Sans Regular/4096-4351.pbf":
            assets.encode_glyph_pbf("Noto Sans Regular", [0x10D0], "4096-4351"),
        "sprites/v4/light.json": b'{"townspot": {"x": 0, "y": 0, "width": 9, "height": 9}}',
        "sprites/v4/light.png": PNG,
        "sprites/v4/light@2x.json": b'{"townspot": {"x": 0, "y": 0, "width": 18, "height": 18}}',
        "sprites/v4/light@2x.png": PNG,
    }
    calls = _fake_repo(monkeypatch, files)
    tiles = tmp_path / "tiles"
    stale = tiles / "fonts" / "Old Font" / "0-255.pbf"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    res = assets.fetch_assets(tiles, tmp_path / "cache", fontstacks=("Noto Sans Regular",),
                              ranges=ranges, flavors=("light",))
    assert res.ref == "a" * 40
    assert res.fonts == {"Noto Sans Regular": ["0-255", "4096-4351"]}
    assert res.missing_fonts == {"Noto Sans Regular": ["7168-7423"]}
    assert res.glyph_counts["Noto Sans Regular"]["4096-4351"] == 1
    assert sorted(res.sprites) == ["light.json", "light.png", "light@2x.json", "light@2x.png"]
    assert (tiles / "fonts" / "Noto Sans Regular" / "4096-4351.pbf").read_bytes() == \
        files["fonts/Noto Sans Regular/4096-4351.pbf"]
    assert (tiles / "sprites" / "light@2x.png").read_bytes() == PNG
    assert not stale.exists() and not stale.parent.exists()

    # second run: everything from cache (only the commit lookup and the 404 range again)
    n = len(calls)
    assets.fetch_assets(tiles, tmp_path / "cache", fontstacks=("Noto Sans Regular",),
                        ranges=ranges, flavors=("light",))
    assert len(calls) - n == 1


def test_fetch_assets_fills_glyph_gaps_from_regular(tmp_path, monkeypatch):
    ka = list(range(0x10D0, 0x10F1))
    files = {
        "fonts/Noto Sans Regular/4096-4351.pbf":
            assets.encode_glyph_pbf("Noto Sans Regular", ka, "4096-4351"),
        "fonts/Noto Sans Italic/4096-4351.pbf":
            assets.encode_glyph_pbf("Noto Sans Italic", [], "4096-4351"),
        "fonts/Noto Sans Regular/7168-7423.pbf":
            assets.encode_glyph_pbf("Noto Sans Regular", [0x1C90], "7168-7423"),
    }
    _fake_repo(monkeypatch, files)
    tiles = tmp_path / "tiles"
    res = assets.fetch_assets(tiles, tmp_path / "cache",
                              fontstacks=("Noto Sans Regular", "Noto Sans Italic"),
                              ranges=((4096, 4351), (7168, 7423)), flavors=())
    assert res.glyph_fallback == {"Noto Sans Italic": {"4096-4351": len(ka), "7168-7423": 1}}
    assert res.missing_fonts == {}
    _, ids = assets.decode_glyph_pbf((tiles / "fonts" / "Noto Sans Italic" / "4096-4351.pbf")
                                     .read_bytes())
    assert ids == ka
    # the cached original stays untouched
    cached = tmp_path / "cache" / ("a" * 40) / "fonts" / "Noto Sans Italic" / "4096-4351.pbf"
    assert assets.decode_glyph_pbf(cached.read_bytes())[1] == []


def test_fetch_assets_rejects_bad_sprite(tmp_path, monkeypatch):
    _fake_repo(monkeypatch, {"sprites/v4/dark.json": b"{}", "sprites/v4/dark.png": PNG})
    with pytest.raises(ValueError):
        assets.fetch_assets(tmp_path / "t", tmp_path / "c", fontstacks=(), flavors=("dark",))
    _fake_repo(monkeypatch, {"sprites/v4/dark.json": b'{"a": {}}', "sprites/v4/dark.png": b"GIF"})
    with pytest.raises(ValueError):
        assets.fetch_assets(tmp_path / "t", tmp_path / "c2", fontstacks=(), flavors=("dark",))


def test_style_fontstacks_are_shipped():
    found = assets.style_fontstacks()
    if not found:
        pytest.skip("app/node_modules not installed")
    assert set(assets.FONTSTACKS) <= found
    assert found - set(assets.FONTSTACKS) <= set(assets.OPTIONAL_FONTSTACKS)


FONTS = APP_TILES / "fonts"


@pytest.mark.skipif(not FONTS.exists(), reason="fonts not downloaded")
def test_shipped_fonts_cover_georgian_cyrillic_latin():
    for stack in assets.FONTSTACKS:
        d = FONTS / stack
        for rng, block in (("0-255", (0x41, 0x7A)), ("1024-1279", (0x410, 0x44F)),
                           ("4096-4351", (0x10D0, 0x10F0))):
            name, ids = assets.decode_glyph_pbf((d / f"{rng}.pbf").read_bytes())
            have = {c for c in ids if block[0] <= c <= block[1]}
            assert len(have) >= 0.9 * (block[1] - block[0] + 1) - 6, (stack, rng, len(have))
