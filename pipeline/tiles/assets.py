"""Glyph (font PBF) and sprite assets for the @protomaps/basemaps style, served offline.

Source: https://github.com/protomaps/basemaps-assets (``fonts/<fontstack>/<range>.pbf`` and
``sprites/v4/<flavor>[@2x].json|png``). Files are pinned to the current ``main`` commit when
the GitHub API answers, cached under ``data/raw/protomaps/basemaps-assets/<ref>/`` and copied to
``app/public/tiles/{fonts,sprites}/``. Downloads are validated (glyph PBF decodes, sprite JSON
parses, PNG signature) and never executed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from pipeline.config import ROOT
from pipeline.log import get_logger
from pipeline.tiles import net

log = get_logger(__name__)

REPO = "protomaps/basemaps-assets"
RAW_URL = "https://raw.githubusercontent.com/" + REPO + "/{ref}/{path}"
COMMIT_API = "https://api.github.com/repos/" + REPO + "/commits/main"

# Fontstacks referenced by @protomaps/basemaps 5.7.2 (src/base_layers.ts). The style also
# switches to "Noto Sans Devanagari Regular v1" for features whose script is Devanagari;
# Tbilisi has none, so it is not shipped (MapLibre renders such codepoints locally).
FONTSTACKS = ("Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic")
FALLBACK_FONTSTACK = "Noto Sans Regular"
OPTIONAL_FONTSTACKS = ("Noto Sans Devanagari Regular v1",)

# Unicode blocks (start, end) needed for Tbilisi labels: Latin (+ext, IPA/modifiers such as
# U+02BC, U+0259), combining marks, Cyrillic (+supplement/Armenian), Georgian Mkhedruli and
# Asomtavruli (U+10A0-10FF), Georgian Mtavruli (U+1C90-1CBF), punctuation and letterlike
# symbols (dashes, quotes, numero sign).
GLYPH_RANGES: tuple[tuple[int, int], ...] = (
    (0, 255), (256, 511), (512, 767), (768, 1023), (1024, 1279), (1280, 1535),
    (4096, 4351), (7168, 7423), (8192, 8447), (8448, 8703),
)

SPRITE_FLAVORS = ("light", "dark")
SPRITE_SCALES = ("", "@2x")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
STYLE_DIST = ROOT / "app" / "node_modules" / "@protomaps" / "basemaps" / "dist" / "esm" / "index.js"


@dataclass
class AssetsResult:
    ref: str
    fonts: dict[str, list[str]] = field(default_factory=dict)          # fontstack -> ranges
    missing_fonts: dict[str, list[str]] = field(default_factory=dict)  # fontstack -> ranges
    glyph_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    glyph_fallback: dict[str, dict[str, int]] = field(default_factory=dict)  # stack -> range -> n
    sprites: list[str] = field(default_factory=list)
    missing_sprites: list[str] = field(default_factory=list)
    font_bytes: int = 0
    sprite_bytes: int = 0


# -- protobuf (glyphs.proto: glyphs{1: fontstack{1: name, 2: range, 3: glyph{1: id, ...}}}) --
def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return result, pos
        if shift > 63:
            raise ValueError("varint too long")


def _fields(buf: bytes, start: int, end: int):
    pos = start
    while pos < end:
        key, pos = _varint(buf, pos)
        fnum, wire = key >> 3, key & 7
        if wire == 0:
            val, pos = _varint(buf, pos)
            yield fnum, wire, val
        elif wire == 2:
            n, pos = _varint(buf, pos)
            if pos + n > end:
                raise ValueError("truncated length-delimited field")
            yield fnum, wire, (pos, pos + n)
            pos += n
        elif wire == 5:
            pos += 4
        elif wire == 1:
            pos += 8
        else:
            raise ValueError(f"unsupported wire type {wire}")
    if pos != end:
        raise ValueError("field overruns message")


def decode_glyph_pbf(buf: bytes) -> tuple[str, list[int]]:
    """Return (fontstack name, sorted glyph codepoints) of a MapLibre glyph PBF."""
    name, ids = "", []
    for f, w, v in _fields(buf, 0, len(buf)):
        if f == 1 and w == 2:
            for f2, w2, v2 in _fields(buf, *v):
                if f2 == 1 and w2 == 2:
                    name = buf[v2[0]:v2[1]].decode("utf-8")
                elif f2 == 3 and w2 == 2:
                    for f3, w3, v3 in _fields(buf, *v2):
                        if f3 == 1 and w3 == 0:
                            ids.append(v3)
    return name, sorted(ids)


def _glyph_parts(buf: bytes) -> tuple[bytes, bytes, list[tuple[int, bytes]]]:
    """Split a glyph PBF into (name, range, [(codepoint, raw glyph message)])."""
    name = rng = b""
    glyphs: list[tuple[int, bytes]] = []
    stacks = 0
    for f, w, v in _fields(buf, 0, len(buf)):
        if f != 1 or w != 2:
            continue
        stacks += 1
        for f2, w2, v2 in _fields(buf, *v):
            if f2 == 1 and w2 == 2:
                name = buf[v2[0]:v2[1]]
            elif f2 == 2 and w2 == 2:
                rng = buf[v2[0]:v2[1]]
            elif f2 == 3 and w2 == 2:
                cp = next((v3 for f3, w3, v3 in _fields(buf, *v2) if f3 == 1 and w3 == 0), None)
                if cp is not None:
                    glyphs.append((cp, buf[v2[0]:v2[1]]))
    if stacks != 1:
        raise ValueError(f"expected one fontstack in glyph PBF, got {stacks}")
    return name, rng, glyphs


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


def merge_glyph_pbf(primary: bytes, fallback: bytes) -> tuple[bytes, int]:
    """Add to ``primary`` the glyphs of ``fallback`` whose codepoints it lacks.

    Returns (pbf bytes, number of glyphs added); ``primary`` is returned unchanged when
    nothing is added. Glyph messages are copied verbatim.
    """
    name, rng, own = _glyph_parts(primary)
    _, _, other = _glyph_parts(fallback)
    have = {cp for cp, _ in own}
    extra = [(cp, g) for cp, g in other if cp not in have]
    if not extra:
        return primary, 0
    body = _pb_ld(1, name) + _pb_ld(2, rng)
    body += b"".join(_pb_ld(3, g) for _, g in sorted(own + extra))
    return _pb_ld(1, body), len(extra)


def encode_glyph_pbf(name: str, codepoints: list[int], range_text: str = "0-255") -> bytes:
    """Minimal glyph PBF writer (empty bitmaps; used by tests)."""

    def vi(fnum: int, n: int) -> bytes:
        return _pb_varint(fnum << 3) + _pb_varint(n)

    glyphs = b"".join(_pb_ld(3, vi(1, cp) + vi(3, 0) + vi(4, 0) + vi(5, 0) + vi(6, 0) + vi(7, 0))
                      for cp in codepoints)
    return _pb_ld(1, _pb_ld(1, name.encode()) + _pb_ld(2, range_text.encode()) + glyphs)


# -- style inspection ---------------------------------------------------------------
def style_fontstacks(dist: Path = STYLE_DIST) -> set[str]:
    """Fontstack names hardcoded in the installed @protomaps/basemaps bundle (read only)."""
    if not dist.exists():
        log.warning("style bundle %s not found (run npm install in app/)", dist)
        return set()
    text = dist.read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r'"(Noto Sans[A-Za-z0-9 ]*)"', text))


# -- download -----------------------------------------------------------------------
def resolve_ref() -> str:
    """Current commit sha of basemaps-assets ``main`` (falls back to ``main``)."""
    try:
        body = net.get_bytes(COMMIT_API, retries=3)
        sha = json.loads(body).get("sha", "")
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            log.info("basemaps-assets main = %s", sha)
            return sha
    except Exception as exc:  # noqa: BLE001 - API is best effort (rate limits)
        log.warning("cannot resolve basemaps-assets commit (%s): using 'main'", exc)
    return "main"


def _fetch_cached(ref: str, rel: str, cache_root: Path) -> bytes | None:
    """Download ``rel`` from the repo at ``ref`` (cached; ``None`` when 404)."""
    cached = cache_root / ref / rel
    missing_marker = cached.with_name(cached.name + ".404")
    if cached.exists():
        return cached.read_bytes()
    if missing_marker.exists() and ref != "main":
        return None
    url = RAW_URL.format(ref=ref, path=quote(rel, safe="/@"))
    data = net.get_bytes(url, allow_404=True)
    cached.parent.mkdir(parents=True, exist_ok=True)
    if data is None:
        missing_marker.write_text("404\n", encoding="utf-8")
        return None
    tmp = cached.with_name(cached.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, cached)
    return data


def _install(data: bytes, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.read_bytes() == data:
        return
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def fetch_assets(tiles_dir: Path, cache_root: Path, *, ref: str | None = None,
                 fontstacks: tuple[str, ...] = FONTSTACKS,
                 ranges: tuple[tuple[int, int], ...] = GLYPH_RANGES,
                 flavors: tuple[str, ...] = SPRITE_FLAVORS, prune: bool = True,
                 fill_from: str | None = FALLBACK_FONTSTACK) -> AssetsResult:
    """Download fonts and sprites into ``tiles_dir/fonts`` and ``tiles_dir/sprites``.

    Glyphs a fontstack lacks (e.g. Georgian in "Noto Sans Italic") are filled from
    ``fill_from``: MapLibre does not fall back to another font for a codepoint missing from a
    loaded range, it just drops it, so Georgian river names would otherwise be blank.
    """
    ref = ref or resolve_ref()
    res = AssetsResult(ref=ref)
    in_style = style_fontstacks()
    if in_style:
        unknown = in_style - set(fontstacks) - set(OPTIONAL_FONTSTACKS)
        if unknown:
            log.warning("style uses fontstacks not shipped: %s", sorted(unknown))
        log.info("style fontstacks: %s", sorted(in_style))

    fonts_dir = tiles_dir / "fonts"
    raw: dict[tuple[str, str], bytes] = {}
    for stack in fontstacks:
        for a, b in ranges:
            rng = f"{a}-{b}"
            data = _fetch_cached(ref, f"fonts/{stack}/{rng}.pbf", cache_root)
            if data is None:
                log.warning("glyphs %s/%s: not in %s", stack, rng, REPO)
                continue
            name, ids = decode_glyph_pbf(data)
            outside = [c for c in ids if not a <= c <= b]
            if outside:
                log.warning("glyphs %s/%s: %d codepoints outside range", stack, rng,
                            len(outside))
            log.info("glyphs %s/%s: %d glyphs, %.1f KB (pbf name %r)", stack, rng, len(ids),
                     len(data) / 1024, name)
            raw[(stack, rng)] = data

    for stack in fontstacks:
        got, missing = [], []
        for a, b in ranges:
            rng = f"{a}-{b}"
            data = raw.get((stack, rng))
            fb = raw.get((fill_from, rng)) if fill_from and stack != fill_from else None
            if fb is not None:
                if data is None:
                    data, added = fb, len(decode_glyph_pbf(fb)[1])
                else:
                    data, added = merge_glyph_pbf(data, fb)
                if added:
                    res.glyph_fallback.setdefault(stack, {})[rng] = added
                    log.info("glyphs %s/%s: %d glyphs filled from %s", stack, rng, added,
                             fill_from)
            if data is None:
                missing.append(rng)
                log.warning("glyphs %s/%s: missing, skipped", stack, rng)
                continue
            res.glyph_counts.setdefault(stack, {})[rng] = len(decode_glyph_pbf(data)[1])
            _install(data, fonts_dir / stack / f"{rng}.pbf")
            res.font_bytes += len(data)
            got.append(rng)
        res.fonts[stack] = got
        if missing:
            res.missing_fonts[stack] = missing

    sprites_dir = tiles_dir / "sprites"
    for flavor in flavors:
        for scale in SPRITE_SCALES:
            for ext in ("json", "png"):
                fname = f"{flavor}{scale}.{ext}"
                data = _fetch_cached(ref, f"sprites/v4/{fname}", cache_root)
                if data is None:
                    res.missing_sprites.append(fname)
                    log.warning("sprite %s: not in %s, skipped", fname, REPO)
                    continue
                if ext == "json":
                    icons = json.loads(data)
                    if not isinstance(icons, dict) or not icons:
                        raise ValueError(f"sprite {fname}: unexpected JSON")
                    log.info("sprite %s: %d icons", fname, len(icons))
                elif not data.startswith(PNG_SIGNATURE):
                    raise ValueError(f"sprite {fname}: not a PNG")
                _install(data, sprites_dir / fname)
                res.sprite_bytes += len(data)
                res.sprites.append(fname)

    if prune:
        _prune(fonts_dir, {f"{s}/{r}.pbf" for s, rs in res.fonts.items() for r in rs})
        _prune(sprites_dir, set(res.sprites))
    log.info("assets: %d font files (%.1f KB), %d sprite files (%.1f KB), ref %s",
             sum(len(v) for v in res.fonts.values()), res.font_bytes / 1024, len(res.sprites),
             res.sprite_bytes / 1024, ref)
    return res


def _prune(root: Path, keep: set[str]) -> None:
    """Remove files under ``root`` that are not in ``keep`` (relative posix paths)."""
    if not root.exists():
        return
    for p in sorted(root.rglob("*"), reverse=True):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and rel not in keep:
            log.info("removing stale asset %s", p)
            p.unlink()
        elif p.is_dir() and not any(p.iterdir()):
            shutil.rmtree(p, ignore_errors=True)
