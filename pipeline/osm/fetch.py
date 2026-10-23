"""Download the Geofabrik Georgia PBF into ``data/raw/osm/`` (dated file name, reused while fresh)."""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from pipeline.config import GEOFABRIK_GEORGIA_PBF, RAW, USER_AGENT
from pipeline.log import get_logger, stage

log = get_logger(__name__)

OSM_RAW = RAW / "osm"
OVERPASS_CACHE = RAW / "prototype_cache" / "overpass"
PBF_PREFIX = "georgia-"
PBF_RE = re.compile(r"^georgia-(\d{8})\.osm\.pbf$")
MIN_PBF_BYTES = 10_000_000  # the Georgia extract is ~97 MB; anything far smaller is broken
DEFAULT_MAX_AGE_DAYS = 7.0


@dataclass
class PbfInfo:
    path: Path
    snapshot: str | None      # ISO timestamp of the OSM data (osmosis_replication_timestamp)
    downloaded: bool          # True when fetched in this run
    sha256: str | None = None


def find_pbfs(folder: Path = OSM_RAW) -> list[Path]:
    """Dated PBF files in ``folder``, newest first."""
    if not folder.exists():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and PBF_RE.match(p.name) and p.stat().st_size >= MIN_PBF_BYTES]
    return sorted(files, key=lambda p: p.name, reverse=True)


def pbf_snapshot(path: Path) -> str | None:
    """Replication timestamp from the PBF header (Geofabrik writes it), else None."""
    import osmium

    try:
        hdr = osmium.io.Reader(str(path), osmium.osm.NOTHING).header()
        ts = hdr.get("osmosis_replication_timestamp")
        return ts or None
    except Exception as exc:  # noqa: BLE001 - a broken file is reported, not fatal here
        log.warning("cannot read PBF header of %s: %s", path, exc)
        return None


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def _age_days(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 86400.0


def download_pbf(url: str = GEOFABRIK_GEORGIA_PBF, folder: Path = OSM_RAW, timeout: float = 60.0) -> Path:
    """Stream ``url`` to ``folder/georgia-YYYYMMDD.osm.pbf`` via a ``.part`` file."""
    folder.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    dst = folder / f"{PBF_PREFIX}{day}.osm.pbf"
    part = dst.with_suffix(dst.suffix + ".part")
    log.info("downloading %s -> %s", url, dst)
    t0 = time.perf_counter()
    n = 0
    with requests.get(url, stream=True, timeout=timeout, headers={"User-Agent": USER_AGENT}) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        next_report = 10_000_000
        with open(part, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                n += len(chunk)
                if n >= next_report:
                    log.info("  %.0f MB%s", n / 1e6, f" of {total / 1e6:.0f} MB" if total else "")
                    next_report += 10_000_000
    if n < MIN_PBF_BYTES:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"downloaded PBF is too small ({n} bytes): {url}")
    if total and n != total:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"incomplete download: {n} of {total} bytes")
    part.replace(dst)
    log.info("downloaded %.1f MB in %.1fs", n / 1e6, time.perf_counter() - t0)
    return dst


def fetch(offline: bool = False, max_age_days: float = DEFAULT_MAX_AGE_DAYS, folder: Path = OSM_RAW) -> PbfInfo | None:
    """Return a usable PBF: the newest local one if fresh (or offline), else a new download.

    Returns None when offline and no PBF exists (the caller falls back to the Overpass cache).
    A failed download falls back to an older local PBF when there is one.
    """
    with stage(log, "osm.fetch", offline=offline, max_age_days=max_age_days) as out:
        local = find_pbfs(folder)
        newest = local[0] if local else None
        if newest is not None:
            out["local"] = newest.name
            out["age_days"] = round(_age_days(newest), 2)
        if newest is not None and (offline or _age_days(newest) <= max_age_days):
            path, downloaded = newest, False
        elif offline:
            log.warning("offline and no local PBF in %s", folder)
            out["result"] = "none"
            return None
        else:
            try:
                path, downloaded = download_pbf(folder=folder), True
            except (requests.RequestException, RuntimeError, OSError) as exc:
                if newest is None:
                    raise
                log.warning("download failed (%s); using older PBF %s", exc, newest.name)
                path, downloaded = newest, False
        snap = pbf_snapshot(path)
        if snap is None:
            log.warning("PBF %s has no replication timestamp; using file mtime", path.name)
            snap = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out.update(path=path.name, snapshot=snap, downloaded=downloaded, mb=round(path.stat().st_size / 1e6, 1))
        return PbfInfo(path=path, snapshot=snap, downloaded=downloaded)


if __name__ == "__main__":  # manual: uv run python -m pipeline.osm.fetch
    import sys

    info = fetch(offline="--offline" in sys.argv)
    print(info)
