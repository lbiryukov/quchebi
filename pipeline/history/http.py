"""Polite HTTP helpers for the history stages: User-Agent, pauses, retries, disk cache.

Everything downloaded is untrusted data: it is only stored under ``data/raw`` and parsed,
never executed.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import requests

from pipeline.config import USER_AGENT
from pipeline.log import get_logger

log = get_logger(__name__)

_session: requests.Session | None = None
_last_call = 0.0


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = USER_AGENT
    return _session


def _pause(min_interval: float) -> None:
    global _last_call
    wait = min_interval - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def download(url: str, dest: Path, *, chunk: int = 1 << 20, retries: int = 5,
             expected_size: int | None = None) -> Path:
    """Download ``url`` to ``dest`` (resumable via HTTP Range). Skips if already complete."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    if dest.exists() and (expected_size is None or dest.stat().st_size == expected_size):
        log.info("download: cached %s (%d bytes)", dest, dest.stat().st_size)
        return dest
    s = session()
    for attempt in range(1, retries + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            _pause(1.0)
            with s.get(url, headers=headers, stream=True, timeout=120) as r:
                if r.status_code == 416:  # already complete
                    break
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0)) + (have if r.status_code == 206 else 0)
                mode = "ab" if r.status_code == 206 else "wb"
                if r.status_code != 206:
                    have = 0
                log.info("download: %s -> %s (attempt %d, from %d of %d bytes)", url, dest, attempt, have, total)
                done = have
                next_report = done + 10 * chunk
                with open(part, mode) as fh:
                    for block in r.iter_content(chunk):
                        fh.write(block)
                        done += len(block)
                        if done >= next_report:
                            log.info("download: %.1f / %.1f MB", done / 1e6, total / 1e6)
                            next_report = done + 10 * chunk
                if total and done < total:
                    raise IOError(f"short read {done} < {total}")
                break
        except (requests.RequestException, IOError) as e:
            log.warning("download: attempt %d failed: %s", attempt, e)
            time.sleep(5 * attempt)
    else:
        raise RuntimeError(f"download failed after {retries} attempts: {url}")
    part.replace(dest)
    log.info("download: saved %s (%d bytes)", dest, dest.stat().st_size)
    return dest


def api_json(url: str, params: dict[str, Any], *, cache_dir: Path | None = None,
             min_interval: float = 1.0, retries: int = 6, post: bool = True) -> dict:
    """GET/POST a JSON API with pauses, 429/5xx back-off and an optional on-disk cache."""
    cache_file = None
    if cache_dir is not None:
        key = hashlib.sha1((url + "?" + json.dumps(params, sort_keys=True, ensure_ascii=False)).encode("utf-8")).hexdigest()
        cache_file = cache_dir / f"{key}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))
    s = session()
    for attempt in range(1, retries + 1):
        _pause(min_interval)
        try:
            if post:
                r = s.post(url, data=params, timeout=90)
            else:
                r = s.get(url, params=params, timeout=90)
        except requests.RequestException as e:
            log.warning("api: %s attempt %d failed: %s", url, attempt, e)
            time.sleep(5 * attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After") or 0) or 10 * attempt
            log.warning("api: %s -> HTTP %d, waiting %ds", url, r.status_code, wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        data = r.json()
        if cache_file is not None:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
    raise RuntimeError(f"api failed after {retries} attempts: {url}")
