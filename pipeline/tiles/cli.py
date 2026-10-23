"""CLI: build the offline basemap.

    uv run python -m pipeline.tiles [--maxzoom 15] [--bbox LON,LAT,LON,LAT] [--center-only]

Picks the newest daily Protomaps planet build from builds.json (or ``--build YYYYMMDD``),
extracts the bbox with HTTP range requests into ``app/public/tiles/tbilisi.pmtiles``
(``tbilisi-center.pmtiles`` with ``--center-only``), downloads glyphs and sprites, writes
``tiles-manifest.json`` and checks the archive with the JS pmtiles library.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path

from pipeline.config import APP_TILES, RAW, ROOT, TBILISI_BBOX
from pipeline.log import get_logger, stage
from pipeline.tiles import assets, manifest, net, source
from pipeline.tiles.extract import DEFAULT_MAX_GAP, extract, open_archive
from pipeline.tiles.geo import BBox, count_by_zoom

log = get_logger(__name__)

CENTER_BBOX = (44.70, 41.65, 44.92, 41.79)
DEFAULT_NAME = "tbilisi.pmtiles"
CENTER_NAME = "tbilisi-center.pmtiles"
CENTER_ZOOM = 12
CACHE_ROOT = RAW / "protomaps"
CHECK_JS = Path(__file__).with_name("check_js.mjs")
BUILD_FALLBACKS = 3  # try this many older builds when the newest one is not reachable


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m pipeline.tiles", description=__doc__.split("\n\n")[0])
    p.add_argument("--maxzoom", type=int, default=15, help="max zoom to extract (default 15)")
    p.add_argument("--minzoom", type=int, default=0, help="min zoom to extract (default 0)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--bbox", type=BBox.parse, default=None,
                   help="lon_min,lat_min,lon_max,lat_max (default pipeline.config.TBILISI_BBOX)")
    g.add_argument("--center-only", action="store_true",
                   help=f"central Tbilisi {CENTER_BBOX} into {CENTER_NAME}")
    p.add_argument("--build", help="Protomaps build YYYYMMDD (default: newest daily build)")
    p.add_argument("--source", help="PMTiles URL or local path instead of a Protomaps build")
    p.add_argument("--out", type=Path, help="output .pmtiles path (default in --tiles-dir)")
    p.add_argument("--tiles-dir", type=Path, default=APP_TILES,
                   help="directory served as /tiles (default app/public/tiles)")
    p.add_argument("--cache-dir", type=Path, default=CACHE_ROOT,
                   help="cache for downloaded ranges and assets (default data/raw/protomaps)")
    p.add_argument("--max-gap", type=int, default=DEFAULT_MAX_GAP,
                   help="merge tile spans separated by at most this many bytes")
    p.add_argument("--skip-assets", action="store_true", help="do not download fonts/sprites")
    p.add_argument("--assets-ref", help="basemaps-assets git ref (default: current main sha)")
    p.add_argument("--no-manifest", action="store_true", help="do not write tiles-manifest.json")
    p.add_argument("--no-js-check", action="store_true", help="skip the node pmtiles check")
    a = p.parse_args(argv)
    if a.build and not re.fullmatch(r"\d{8}", a.build):
        p.error("--build must be YYYYMMDD")
    if not 0 <= a.minzoom <= a.maxzoom <= 15:
        p.error("need 0 <= minzoom <= maxzoom <= 15")
    return a


def _open_source(args: argparse.Namespace) -> tuple[object, str, str]:
    """Return (range source, build id YYYYMMDD, source url/path)."""
    if args.source:
        build = args.build
        if not build:
            m = re.search(r"(\d{8})\.pmtiles$", args.source)
            build = m.group(1) if m else "00000000"
            if not m:
                log.warning("cannot infer build date from %s; use --build", args.source)
        if re.match(r"^https?://", args.source):
            slug = re.sub(r"[^A-Za-z0-9._-]+", "_", args.source.split("://", 1)[1])[:120]
            src = source.HttpRangeSource(args.source, args.cache_dir / slug)
        else:
            src = source.LocalFileSource(Path(args.source))
        return src, build, args.source

    if args.build:
        candidates = [args.build]
    else:
        keys = source.daily_build_keys(source.list_builds())
        if not keys:
            raise RuntimeError("builds.json lists no daily builds")
        candidates = list(reversed(keys))[:BUILD_FALLBACKS + 1]
        log.info("newest daily builds: %s", candidates)
    last_exc: Exception | None = None
    for build in candidates:
        url = source.build_url(build)
        src = source.HttpRangeSource(url, args.cache_dir / build)
        try:
            src.probe()
            log.info("using build %s (%s), etag %s, %.1f GB", build, url, src.etag,
                     (src.size or 0) / 1e9)
            return src, build, url
        except Exception as exc:  # noqa: BLE001 - fall back to an older build
            log.warning("build %s not usable: %s", build, exc)
            last_exc = exc
    raise RuntimeError(f"no usable Protomaps build among {candidates}") from last_exc


def run_js_check(path: Path) -> dict | None:
    node = shutil.which("node")
    if not node:
        log.warning("node not found: JS check skipped")
        return None
    proc = subprocess.run([node, str(CHECK_JS), str(path)], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", timeout=120)
    line = (proc.stdout.strip().splitlines() or [""])[-1]
    try:
        res = json.loads(line)
    except json.JSONDecodeError:
        res = {"ok": False, "error": (proc.stderr or proc.stdout)[-2000:]}
    if proc.returncode != 0 or not res.get("ok"):
        log.error("JS check failed for %s: %s", path, res.get("error"))
    else:
        log.info("JS check ok: header z%s-%s bounds %s, tile %s/%s/%s %d bytes, layers %s",
                 res["header"]["minZoom"], res["header"]["maxZoom"], res["header"]["bounds"],
                 res["tile"]["z"], res["tile"]["x"], res["tile"]["y"], res["tile"]["bytes"],
                 res["tile"]["layers"])
    return res


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return _run(args)
    except (RuntimeError, OSError, ValueError, net.FatalHTTPError, net.RetryableError) as exc:
        log.error("tiles failed: %s", exc)
        return 1


def _run(args: argparse.Namespace) -> int:
    if args.center_only:
        bbox, name = BBox(*CENTER_BBOX), CENTER_NAME
    else:
        bbox, name = (args.bbox or BBox(*TBILISI_BBOX)), DEFAULT_NAME
    tiles_dir: Path = args.tiles_dir
    out: Path = args.out or tiles_dir / name
    log.info("tiles: bbox %s z%d-%d -> %s", bbox.as_list(), args.minzoom, args.maxzoom, out)
    log.info("expected tiles per zoom: %s", count_by_zoom(bbox, args.minzoom, args.maxzoom))

    with stage(log, "tiles_extract", bbox=bbox.as_list(), maxzoom=args.maxzoom) as st:
        src, build, url = _open_source(args)
        archive = open_archive(src)
        tileset_version = archive.metadata.get("version")
        extra = {"source_build": build, "source": url,
                 "extracted": dt.date.today().strftime("%d/%m/%Y")}
        res = extract(src, out, bbox, minzoom=args.minzoom, maxzoom=args.maxzoom,
                      center_zoom=CENTER_ZOOM, max_gap=args.max_gap, extra_metadata=extra,
                      archive=archive)
        st.update(tiles=res.addressed_tiles, mb=round(res.bytes / 1048576, 2),
                  ranges=res.ranges, build=build)
        if isinstance(src, source.HttpRangeSource):
            log.info("http: %d requests, %.2f MB downloaded, %d cache hits", src.requests,
                     src.bytes_downloaded / 1048576, src.cache_hits)
        for z, n in res.tiles_by_zoom.items():
            log.info("  z%-2d %5d tiles%s", z, n,
                     f" ({res.missing_by_zoom[z]} missing in source)"
                     if res.missing_by_zoom.get(z) else "")

    assets_res = None
    if not args.skip_assets:
        with stage(log, "tiles_assets") as st:
            assets_res = assets.fetch_assets(tiles_dir, args.cache_dir / "basemaps-assets",
                                             ref=args.assets_ref)
            st.update(fonts=sum(len(v) for v in assets_res.fonts.values()),
                      sprites=len(assets_res.sprites), missing=assets_res.missing_fonts)

    js = None if args.no_js_check else run_js_check(out)

    rc = 0 if (js is None or js.get("ok")) else 1
    if args.no_manifest:
        return rc
    try:
        basemap = out.resolve().relative_to(tiles_dir.resolve()).as_posix()
    except ValueError:
        log.warning("%s is outside %s: manifest not written", out, tiles_dir)
        return rc
    extra_m: dict = {
        "minzoom": res.minzoom,
        "center": [*bbox.center, CENTER_ZOOM],
        "tileset_version": tileset_version,
        "tiles_by_zoom": {str(z): n for z, n in res.tiles_by_zoom.items()},
        "glyphs": "fonts/{fontstack}/{range}.pbf",
        "sprites": {f: f"sprites/{f}" for f in assets.SPRITE_FLAVORS},
    }
    if assets_res is not None:
        extra_m.update(
            assets_ref=assets_res.ref,
            fontstacks=list(assets_res.fonts),
            glyph_ranges=sorted({r for rs in assets_res.fonts.values() for r in rs},
                                key=lambda r: int(r.split("-")[0])),
            missing_glyph_ranges=assets_res.missing_fonts,
            glyph_fallback={"from": assets.FALLBACK_FONTSTACK,
                            "added": assets_res.glyph_fallback},
        )
    m = manifest.build_manifest(tiles_dir, basemap=basemap, source_build=build,
                                bbox=bbox.as_list(), maxzoom=res.maxzoom, extra=extra_m)
    manifest.write_manifest(tiles_dir, m)
    problems = manifest.verify_manifest(tiles_dir)
    if problems:
        log.error("manifest problems: %s", problems)
        return 1
    log.info("done: %s %.2f MB, fonts %.1f KB, sprites %.1f KB, total %.2f MB", basemap,
             res.bytes / 1048576, (assets_res.font_bytes if assets_res else 0) / 1024,
             (assets_res.sprite_bytes if assets_res else 0) / 1024, m["total_bytes"] / 1048576)
    return rc
