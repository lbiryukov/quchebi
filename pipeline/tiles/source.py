"""Byte-range sources for PMTiles archives: remote (HTTP Range + disk cache) and local file.

The remote planet build is ~140 GB, so every request is a bounded ``Range`` request and a
server that ignores ``Range`` (HTTP 200) is treated as a fatal error instead of being read.
Downloaded ranges are cached under ``data/raw/protomaps/<build>/ranges/``; a cached range
also serves any sub-range, so a smaller bbox re-uses the downloads of a larger one.
"""
from __future__ import annotations

import bisect
import json
import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

from pipeline.log import get_logger
from pipeline.tiles import net

log = get_logger(__name__)

BUILDS_URL = "https://build-metadata.protomaps.dev/builds.json"
BUILD_URL_TEMPLATE = "https://build.protomaps.com/{build}.pmtiles"
MAX_PARALLEL = 4
HEADER_FETCH = 16384  # PMTiles v3: header + root directory always fit in the first 16 KiB

_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$")
_CACHE_NAME = re.compile(r"^r_(\d+)_(\d+)\.bin$")
_BUILD_KEY = re.compile(r"^(\d{8})\.pmtiles$")


class RangeSource(Protocol):
    key: str

    def get(self, offset: int, length: int) -> bytes: ...

    def get_many(self, ranges: list[tuple[int, int]]) -> dict[tuple[int, int], bytes]: ...


class RangeCache:
    """Directory of downloaded byte ranges ``r_<offset>_<length>.bin``."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._starts: list[int] = []
        self._entries: list[tuple[int, int]] = []
        for p in self.root.iterdir():
            m = _CACHE_NAME.match(p.name)
            if m and p.stat().st_size == int(m.group(2)):
                self._insert(int(m.group(1)), int(m.group(2)))
            elif p.suffix == ".tmp" or m:
                log.warning("range cache: dropping incomplete file %s", p.name)
                p.unlink(missing_ok=True)
        log.debug("range cache %s: %d ranges", root, len(self._entries))

    def __len__(self) -> int:
        return len(self._entries)

    def _insert(self, offset: int, length: int) -> None:
        i = bisect.bisect_left(self._entries, (offset, length))
        if i < len(self._entries) and self._entries[i] == (offset, length):
            return
        self._entries.insert(i, (offset, length))
        self._starts.insert(i, offset)

    def _path(self, offset: int, length: int) -> Path:
        return self.root / f"r_{offset}_{length}.bin"

    def find(self, offset: int, length: int) -> tuple[int, int] | None:
        """Return a cached (offset, length) range that fully covers the request, if any."""
        with self._lock:
            i = bisect.bisect_right(self._starts, offset)
            # Scan a few candidates to the left: larger ranges may start earlier.
            for j in range(i - 1, max(-1, i - 64), -1):
                o, n = self._entries[j]
                if o <= offset and offset + length <= o + n:
                    return o, n
        return None

    def read(self, offset: int, length: int) -> bytes | None:
        hit = self.find(offset, length)
        if hit is None:
            return None
        o, _ = hit
        with open(self._path(*hit), "rb") as f:
            f.seek(offset - o)
            data = f.read(length)
        if len(data) != length:
            log.warning("range cache: truncated file for %s, ignoring", hit)
            return None
        return data

    def write(self, offset: int, data: bytes) -> None:
        path = self._path(offset, len(data))
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        with self._lock:
            self._insert(offset, len(data))

    def clear(self) -> None:
        with self._lock:
            shutil.rmtree(self.root, ignore_errors=True)
            self.root.mkdir(parents=True, exist_ok=True)
            self._entries.clear()
            self._starts.clear()


class LocalFileSource:
    """Range reads from a local PMTiles file (tests, re-extraction from a local archive)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.key = str(self.path)
        self.size = self.path.stat().st_size
        self.requests = 0

    def get(self, offset: int, length: int) -> bytes:
        self.requests += 1
        with open(self.path, "rb") as f:
            f.seek(offset)
            return f.read(length)

    def get_many(self, ranges: list[tuple[int, int]]) -> dict[tuple[int, int], bytes]:
        return {r: self.get(*r) for r in ranges}


class HttpRangeSource:
    """HTTP ``Range`` reads with retries, ≤ ``MAX_PARALLEL`` concurrent requests and a disk cache."""

    def __init__(self, url: str, cache_dir: Path, *, max_parallel: int = MAX_PARALLEL,
                 retries: int = 5):
        self.url = url
        self.key = url
        self.cache_dir = cache_dir
        self.cache = RangeCache(cache_dir / "ranges")
        self.max_parallel = max(1, min(max_parallel, MAX_PARALLEL))
        self.retries = retries
        self.etag: str | None = None
        self.size: int | None = None
        self.identity_checked = False
        self.requests = 0
        self.bytes_downloaded = 0
        self.cache_hits = 0
        self._stats_lock = threading.Lock()

    # -- identity / cache validation -------------------------------------------------
    def _meta_path(self) -> Path:
        return self.cache_dir / "source.json"

    def _check_identity(self, etag: str | None, size: int | None) -> None:
        """Drop the cache when the remote object changed (different ETag or size)."""
        with self._stats_lock:
            if self.identity_checked:
                return
            self.identity_checked = True
        self.etag, self.size = etag, size
        meta_path = self._meta_path()
        if meta_path.exists():
            old = json.loads(meta_path.read_text(encoding="utf-8"))
            if (old.get("etag"), old.get("size")) != (etag, size):
                log.warning("remote %s changed (etag %s -> %s, size %s -> %s): clearing cache",
                            self.url, old.get("etag"), etag, old.get("size"), size)
                self.cache.clear()
        meta_path.write_text(json.dumps({"url": self.url, "etag": etag, "size": size}, indent=1),
                             encoding="utf-8")

    def probe(self) -> None:
        """Fetch the header block once per run to learn ETag/size (validates cached ranges)."""
        if not self.identity_checked:
            data = self._download(0, HEADER_FETCH)
            self.cache.write(0, data)

    # -- reads ------------------------------------------------------------------------
    def get(self, offset: int, length: int) -> bytes:
        if length <= 0:
            return b""
        self.probe()
        cached = self.cache.read(offset, length)
        if cached is not None:
            with self._stats_lock:
                self.cache_hits += 1
            return cached
        data = self._download(offset, length)
        self.cache.write(offset, data)
        return data

    def get_many(self, ranges: list[tuple[int, int]]) -> dict[tuple[int, int], bytes]:
        self.probe()
        out: dict[tuple[int, int], bytes] = {}
        missing: list[tuple[int, int]] = []
        for r in ranges:
            data = self.cache.read(*r)
            if data is None:
                missing.append(r)
            else:
                out[r] = data
        with self._stats_lock:
            self.cache_hits += len(out)
        if missing:
            total = sum(n for _, n in missing)
            log.info("downloading %d ranges (%.2f MB) with %d parallel requests, %d from cache",
                     len(missing), total / 1048576, self.max_parallel, len(out))
            done = [0, 0]
            lock = threading.Lock()

            def task(r: tuple[int, int]) -> tuple[tuple[int, int], bytes]:
                data = self._download(*r)
                self.cache.write(r[0], data)
                with lock:
                    done[0] += 1
                    done[1] += len(data)
                    if done[0] % 10 == 0 or done[0] == len(missing):
                        log.info("  ranges %d/%d, %.2f/%.2f MB", done[0], len(missing),
                                 done[1] / 1048576, total / 1048576)
                return r, data

            with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
                for r, data in pool.map(task, missing):
                    out[r] = data
        return out

    def _download(self, offset: int, length: int) -> bytes:
        end = offset + length - 1
        what = f"range {offset}-{end} of {self.url}"

        def once() -> bytes:
            headers = {"Range": f"bytes={offset}-{end}", "Accept-Encoding": "identity"}
            with net.session().get(self.url, headers=headers, stream=True,
                                   timeout=net.DEFAULT_TIMEOUT) as r:
                if r.status_code in net.RETRY_STATUSES:
                    raise net.RetryableError(f"HTTP {r.status_code}")
                if r.status_code == 200:
                    # Never read a full-object response: it would be the whole planet.
                    raise net.FatalHTTPError(f"{what}: server ignored Range (HTTP 200)")
                if r.status_code != 206:
                    raise net.FatalHTTPError(f"{what}: HTTP {r.status_code}")
                m = _CONTENT_RANGE.match(r.headers.get("Content-Range", ""))
                if not m or int(m.group(1)) != offset:
                    raise net.FatalHTTPError(f"{what}: bad Content-Range "
                                             f"{r.headers.get('Content-Range')!r}")
                size = None if m.group(3) == "*" else int(m.group(3))
                got_end = int(m.group(2))
                if got_end != end and not (size is not None and got_end == size - 1):
                    raise net.FatalHTTPError(f"{what}: server returned {offset}-{got_end}")
                self._check_identity(r.headers.get("ETag"), size)
                expected = got_end - offset + 1
                chunks, total = [], 0
                for chunk in r.iter_content(1 << 16):
                    total += len(chunk)
                    if total > expected:
                        raise net.FatalHTTPError(f"{what}: body longer than requested")
                    chunks.append(chunk)
                if total != expected:
                    raise net.RetryableError(f"short read {total}/{expected}")
                with self._stats_lock:
                    self.requests += 1
                    self.bytes_downloaded += total
                log.debug("downloaded %s (%d bytes)", what, total)
                return b"".join(chunks)

        return net.with_retries(once, what, retries=self.retries)


# -- build discovery ------------------------------------------------------------------
def list_builds() -> list[dict]:
    body = net.get_bytes(BUILDS_URL)
    builds = json.loads(body)
    if not isinstance(builds, list):
        raise ValueError("builds.json: expected a list")
    return builds


def daily_build_keys(builds: list[dict]) -> list[str]:
    """Sorted ``YYYYMMDD`` keys of daily planet builds (oldest first)."""
    keys = []
    for b in builds:
        m = _BUILD_KEY.match(str(b.get("key", "")))
        if m:
            keys.append(m.group(1))
    return sorted(set(keys))


def build_url(build: str) -> str:
    if not re.fullmatch(r"\d{8}", build):
        raise ValueError(f"build must be YYYYMMDD, got {build!r}")
    return BUILD_URL_TEMPLATE.format(build=build)


def build_info(builds: list[dict], build: str) -> dict:
    for b in builds:
        if b.get("key") == f"{build}.pmtiles":
            return b
    return {}
