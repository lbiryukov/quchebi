"""HTTP sources of the Russian reference stage: Wikipedia / Wikivoyage wikitext, Wikidata SPARQL.

The MediaWiki ``api.php`` endpoints answer 429 to shared cloud addresses, so pages are read as raw
wikitext (``index.php?action=raw``) and Wikidata through the SPARQL endpoint. Every response is
cached on disk (``data/<city>/raw/ref/``); a re-run is offline. Requests are sequential, spaced and
retried with back-off on 429/5xx.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import requests

from pipeline.config import RAW, USER_AGENT, WIKIDATA_SPARQL
from pipeline.log import get_logger

log = get_logger(__name__)

REF_DIR = RAW / "ref"
_last: dict[str, float] = {}
_session: requests.Session | None = None
_REDIRECT = re.compile(r"^\s*#\s*(?:redirect|перенаправление)\s*:?\s*\[\[([^\]|#]+)", re.IGNORECASE)


class Offline(RuntimeError):
    pass


def _sess() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    return _session


def cached_get(url: str, params: dict | None, kind: str, *, offline: bool = False, min_interval: float = 1.5,
               retries: int = 7, accept: str | None = None, method: str = "GET") -> str | None:
    """Text of ``url`` (None for 404). Cached under ``REF_DIR/<kind>/<sha1>.json``."""
    key = hashlib.sha1((url + "|" + json.dumps(params or {}, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
    path = REF_DIR / kind / f"{key}.json"
    if path.exists():
        d = json.loads(path.read_text(encoding="utf-8"))
        return d["text"]
    if offline:
        raise Offline(f"not cached: {url} {params}")
    host = url.split("/")[2]
    delay = 5.0
    for attempt in range(retries):
        wait = _last.get(host, 0) + min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            if method == "POST":
                r = _sess().post(url, data=params, timeout=90, headers={"Accept": accept} if accept else None)
            else:
                r = _sess().get(url, params=params, timeout=90, headers={"Accept": accept} if accept else None)
        except requests.RequestException as exc:
            log.warning("http %s: %s (attempt %d)", host, exc, attempt + 1)
            _last[host] = time.monotonic()
            time.sleep(delay)
            delay = min(delay * 2, 120)
            continue
        _last[host] = time.monotonic()
        if r.status_code == 404:
            text = None
        elif r.status_code in (429, 500, 502, 503, 504):
            ra = r.headers.get("Retry-After")
            pause = min(float(ra), 180.0) if ra and ra.isdigit() else delay
            log.info("http %s: %d, retry in %.0fs (attempt %d)", host, r.status_code, pause, attempt + 1)
            time.sleep(pause)
            delay = min(delay * 2, 120)
            continue
        else:
            r.raise_for_status()
            r.encoding = "utf-8"
            text = r.text
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"url": url, "params": params, "text": text}, ensure_ascii=False), encoding="utf-8")
        return text
    raise RuntimeError(f"http {host}: giving up after {retries} attempts: {url} {params}")


def wikitext(site: str, title: str, *, offline: bool = False, follow: int = 3) -> tuple[str | None, str]:
    """(wikitext or None, final title) of a page, following redirects."""
    for _ in range(follow + 1):
        text = cached_get(f"https://{site}/w/index.php", {"title": title, "action": "raw"}, "wikitext", offline=offline)
        if text is None:
            return None, title
        m = _REDIRECT.match(text)
        if not m:
            return text, title
        title = m.group(1).strip().replace("_", " ")
    return None, title


def sparql(query: str, *, offline: bool = False) -> list[dict]:
    """Rows of a SPARQL SELECT as {var: value string}."""
    text = cached_get(WIKIDATA_SPARQL, {"query": query, "format": "json"}, "sparql", offline=offline,
                      min_interval=2.0, accept="application/sparql-results+json", method="POST")
    if not text:
        return []
    data = json.loads(text)
    return [{k: v.get("value", "") for k, v in b.items()} for b in data.get("results", {}).get("bindings", [])]


def qid_of(uri: str) -> str:
    return uri.rsplit("/", 1)[-1] if uri else ""
