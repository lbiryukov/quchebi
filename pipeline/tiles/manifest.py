"""``app/public/tiles/tiles-manifest.json``: what the pack export ships as ``manifest.tiles``.

Paths in ``files`` are relative to ``app/public/tiles/`` (posix separators). Only the basemap
archive of this build plus everything under ``fonts/`` and ``sprites/`` is listed; other
``*.pmtiles`` files in the directory (e.g. an alternative extract) are ignored.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path

from pipeline.log import get_logger

log = get_logger(__name__)

MANIFEST_NAME = "tiles-manifest.json"
ATTRIBUTION = "Protomaps © OpenStreetMap contributors, ODbL"
ASSET_DIRS = ("fonts", "sprites")
_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_files(tiles_dir: Path, basemap: str) -> list[str]:
    files = [basemap]
    for d in ASSET_DIRS:
        root = tiles_dir / d
        if root.exists():
            files += sorted(p.relative_to(tiles_dir).as_posix() for p in root.rglob("*")
                            if p.is_file() and not p.name.endswith(".tmp"))
    return files


def build_manifest(tiles_dir: Path, *, basemap: str, source_build: str, bbox: list[float],
                   maxzoom: int, built: dt.date | None = None, extra: dict | None = None) -> dict:
    """Describe ``basemap`` + fonts + sprites with sizes and sha256."""
    if not (tiles_dir / basemap).is_file():
        raise FileNotFoundError(tiles_dir / basemap)
    built = built or dt.date.today()
    files = {}
    for rel in list_files(tiles_dir, basemap):
        p = tiles_dir / rel
        files[rel] = {"bytes": p.stat().st_size, "sha256": sha256_file(p)}
    manifest = {
        "v": 1,
        "built": built.strftime("%d/%m/%Y"),
        "source_build": source_build,
        "bbox": list(bbox),
        "maxzoom": maxzoom,
        "basemap": basemap,
        "files": files,
        "total_bytes": sum(f["bytes"] for f in files.values()),
        "attribution": ATTRIBUTION,
    }
    for k, v in (extra or {}).items():
        manifest.setdefault(k, v)
    return manifest


def write_manifest(tiles_dir: Path, manifest: dict) -> Path:
    path = tiles_dir / MANIFEST_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    log.info("wrote %s: %d files, %.2f MB", path, len(manifest["files"]),
             manifest["total_bytes"] / 1048576)
    return path


def load_manifest(tiles_dir: Path) -> dict:
    return json.loads((tiles_dir / MANIFEST_NAME).read_text(encoding="utf-8"))


def verify_manifest(tiles_dir: Path, manifest: dict | None = None) -> list[str]:
    """Return a list of problems (empty when the manifest matches the files on disk)."""
    m = manifest if manifest is not None else load_manifest(tiles_dir)
    problems: list[str] = []
    for key in ("built", "source_build", "bbox", "maxzoom", "files", "attribution"):
        if key not in m:
            problems.append(f"missing key {key!r}")
    if problems:
        return problems
    if not _DATE.match(str(m["built"])):
        problems.append(f"built {m['built']!r} is not DD/MM/YYYY")
    if not re.fullmatch(r"\d{8}", str(m["source_build"])):
        problems.append(f"source_build {m['source_build']!r} is not YYYYMMDD")
    if len(m["bbox"]) != 4:
        problems.append("bbox must have 4 numbers")
    if m.get("basemap") and m["basemap"] not in m["files"]:
        problems.append("basemap not listed in files")
    for rel, info in m["files"].items():
        if "\\" in rel or rel.startswith("/") or ".." in rel.split("/"):
            problems.append(f"bad path {rel!r}")
            continue
        p = tiles_dir / rel
        if not p.is_file():
            problems.append(f"missing file {rel}")
            continue
        if p.stat().st_size != info.get("bytes"):
            problems.append(f"size mismatch {rel}")
        elif sha256_file(p) != info.get("sha256"):
            problems.append(f"sha256 mismatch {rel}")
    return problems
