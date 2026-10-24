"""Polite, cached HTTP for Wikidata / Wikipedia.

* Every response is stored in a SQLite cache (``data/raw/wikidata/http_cache.sqlite``) keyed by
  ``sha1(method | url | sorted params)``; a cached answer is never re-requested unless
  ``refresh=True``.
* Requests carry the project User-Agent and are spaced by a per-endpoint minimum gap
  (Wikidata SPARQL has been seen at 1 request/minute, ``wbsearchentities`` answered 429).
* 429/5xx and network errors are retried with exponential back-off, honouring ``Retry-After``;
  after a 429 the minimum gap for that endpoint is doubled (up to ``MAX_GAP``).
* ``offline=True`` turns every cache miss into :class:`CacheMiss` (tests, reproducible reruns).

Downloaded content is untrusted data: it is parsed as JSON and never executed.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

import requests

from pipeline.config import RAW, USER_AGENT
from pipeline.log import get_logger

log = get_logger(__name__)

CACHE_PATH = RAW / "wikidata" / "http_cache.sqlite"
MAX_GAP = 120.0
DEFAULT_GAPS = {"api": 1.0, "sparql": 5.0, "wiki": 1.0}


class CacheMiss(RuntimeError):
    """Raised in offline mode when a request is not in the cache."""


class HttpError(RuntimeError):
    """Non-retryable HTTP failure (4xx other than 429) or retries exhausted."""


def cache_key(method: str, url: str, params: dict[str, Any] | None) -> str:
    blob = method.upper() + "|" + url + "|" + json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


class HttpCache:
    """Tiny key/value store for raw JSON responses (and parsed entities, see client)."""

    def __init__(self, path: Path | str = CACHE_PATH):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.path), check_same_thread=False)
        self.con.execute("PRAGMA journal_mode = WAL") if str(self.path) != ":memory:" else None
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS resp (key TEXT PRIMARY KEY, method TEXT, url TEXT, params TEXT, "
            "body TEXT NOT NULL, fetched_at TEXT NOT NULL)"
        )
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS entity (qid TEXT PRIMARY KEY, props TEXT NOT NULL, data TEXT NOT NULL, "
            "fetched_at TEXT NOT NULL)"
        )
        self.con.commit()
        self._lock = threading.Lock()

    # -- raw responses
    def get(self, key: str) -> Any | None:
        row = self.con.execute("SELECT body FROM resp WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, method: str, url: str, params: dict | None, body: Any) -> None:
        with self._lock:
            self.con.execute(
                "INSERT OR REPLACE INTO resp(key, method, url, params, body, fetched_at) VALUES (?,?,?,?,?,?)",
                (key, method, url, json.dumps(params or {}, ensure_ascii=False)[:4000],
                 json.dumps(body, ensure_ascii=False), time.strftime("%Y-%m-%dT%H:%M:%S")),
            )
            self.con.commit()

    # -- entities
    def get_entities(self, qids: list[str]) -> dict[str, tuple[str, dict]]:
        out: dict[str, tuple[str, dict]] = {}
        for i in range(0, len(qids), 500):
            chunk = qids[i:i + 500]
            q = "SELECT qid, props, data FROM entity WHERE qid IN (%s)" % ",".join("?" * len(chunk))
            for qid, props, data in self.con.execute(q, chunk):
                out[qid] = (props, json.loads(data))
        return out

    def put_entities(self, items: dict[str, dict], props: str, fetched_at: str | None = None) -> None:
        ts = fetched_at or time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            self.con.executemany(
                "INSERT OR REPLACE INTO entity(qid, props, data, fetched_at) VALUES (?,?,?,?)",
                [(q, props, json.dumps(e, ensure_ascii=False), ts) for q, e in items.items()],
            )
            self.con.commit()

    def count(self) -> dict[str, int]:
        return {
            "responses": self.con.execute("SELECT COUNT(*) FROM resp").fetchone()[0],
            "entities": self.con.execute("SELECT COUNT(*) FROM entity").fetchone()[0],
        }

    def close(self) -> None:
        self.con.close()


class Fetcher:
    """Rate-limited, retrying, cached JSON fetcher."""

    def __init__(self, cache: HttpCache | None = None, *, offline: bool = False,
                 gaps: dict[str, float] | None = None, retries: int = 6,
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.cache = cache or HttpCache()
        self.offline = offline
        self.gaps = dict(DEFAULT_GAPS)
        if gaps:
            self.gaps.update(gaps)
        self.retries = retries
        self._session = session
        self._last: dict[str, float] = {}
        self._sleep = sleep
        self.stats = {"hits": 0, "misses": 0, "requests": 0, "retries": 0, "errors": 0}

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers["User-Agent"] = USER_AGENT
            self._session.headers["Accept-Encoding"] = "gzip"
        return self._session

    def _pause(self, endpoint: str) -> None:
        gap = self.gaps.get(endpoint, 1.0)
        wait = gap - (time.monotonic() - self._last.get(endpoint, 0.0))
        if wait > 0:
            self._sleep(wait)
        self._last[endpoint] = time.monotonic()

    def get_json(self, url: str, params: dict[str, Any], *, endpoint: str = "api", method: str = "GET",
                 accept: str = "application/json", refresh: bool = False, timeout: int = 90,
                 validate: Callable[[Any], bool] | None = None) -> Any:
        """Return parsed JSON for ``url`` + ``params`` from cache or network."""
        key = cache_key(method, url, params)
        if not refresh:
            hit = self.cache.get(key)
            if hit is not None:
                self.stats["hits"] += 1
                return hit
        if self.offline:
            raise CacheMiss(f"offline cache miss: {method} {url} {json.dumps(params, ensure_ascii=False)[:200]}")
        self.stats["misses"] += 1
        last_err: str = ""
        for attempt in range(1, self.retries + 1):
            self._pause(endpoint)
            self.stats["requests"] += 1
            try:
                if method.upper() == "POST":
                    r = self.session.post(url, data=params, headers={"Accept": accept}, timeout=timeout)
                else:
                    r = self.session.get(url, params=params, headers={"Accept": accept}, timeout=timeout)
            except requests.RequestException as e:
                last_err = repr(e)
                wait = min(MAX_GAP, 5.0 * 2 ** (attempt - 1))
                log.warning("http %s %s attempt %d failed: %s; retry in %.0fs", endpoint, url, attempt, e, wait)
                self.stats["retries"] += 1
                self._sleep(wait)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                ra = r.headers.get("Retry-After") or ""
                try:
                    wait = float(ra)
                except ValueError:
                    wait = 0.0
                wait = min(300.0, wait or 5.0 * 2 ** (attempt - 1))
                if r.status_code == 429:
                    self.gaps[endpoint] = min(MAX_GAP, max(self.gaps.get(endpoint, 1.0) * 2, 2.0))
                log.warning("http %s -> %d (attempt %d), waiting %.0fs, gap now %.1fs",
                            endpoint, r.status_code, attempt, wait, self.gaps[endpoint])
                last_err = f"HTTP {r.status_code}"
                self.stats["retries"] += 1
                self._sleep(wait)
                continue
            if r.status_code >= 400:
                self.stats["errors"] += 1
                raise HttpError(f"HTTP {r.status_code} for {url}: {r.text[:300]}")
            try:
                data = r.json()
            except ValueError:
                # SPARQL sometimes returns control characters; strict=False parses them
                try:
                    data = json.loads(r.text, strict=False)
                except ValueError as e:
                    last_err = f"bad json: {e}"
                    log.warning("http %s bad JSON (attempt %d): %s", endpoint, attempt, e)
                    self.stats["retries"] += 1
                    self._sleep(min(MAX_GAP, 5.0 * attempt))
                    continue
            if isinstance(data, dict) and isinstance(data.get("error"), dict):
                code = data["error"].get("code")
                if code == "maxlag":
                    wait = min(30.0, max(5.0, float(data["error"].get("lag", 5) or 5)))
                    log.warning("http %s maxlag, waiting %.0fs", endpoint, wait)
                    self.stats["retries"] += 1
                    self._sleep(wait)
                    continue
                self.stats["errors"] += 1
                raise HttpError(f"API error {code}: {str(data['error'])[:300]}")
            if validate is not None and not validate(data):
                self.stats["errors"] += 1
                raise HttpError(f"unexpected response shape from {url}")
            self.cache.put(key, method, url, params, data)
            return data
        self.stats["errors"] += 1
        raise HttpError(f"giving up after {self.retries} attempts: {url} ({last_err})")
