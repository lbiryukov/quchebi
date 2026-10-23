"""Polite downloader of Runcity route pages for Tbilisi events (stdlib only).

Downloads, for every event slug, the routes index, the "all CPs" page and one page per
category (griffin, lion-*, sphynx, ...) into ``data/raw/runcity/<event>/``. Pages are public;
their texts are untrusted data and stay in ``data/raw`` (gitignored). Already downloaded
files are skipped unless ``--force``.

Usage: uv run python tests/acceptance/tools/fetch_runcity.py [--force] [event ...]
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw" / "runcity"
LOGS = ROOT / "logs"
BASE = "https://www.runcity.org/ru/events/{event}/routes/{page}"
UA = "Mozilla/5.0 (compatible; Quchebi acceptance set builder)"
PAUSE_S = 4.0

TBILISI_EVENTS = [
    "tbilisi2019", "tbilisi2021", "tbilisi2022", "tbilisi2023", "teplotbilisi2023",
    "moderntbilisi2023", "tbilisi2024", "tbilisi2025", "plans2025tbilisi",
]
NOT_CATEGORIES = {"all", "cp_stats", "history", "kml", "map", ""}

log = logging.getLogger("acceptance.fetch")


def setup_logging() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%d/%m/%Y %H:%M:%S")
    fh = logging.FileHandler(LOGS / "acceptance.log", encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)
    root.addHandler(ch)


def fetch(url: str, dest: Path, force: bool) -> str | None:
    if dest.exists() and not force:
        log.debug("skip (cached) %s", dest)
        return dest.read_text(encoding="utf-8", errors="replace")
    log.info("GET %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read().decode("utf-8", errors="replace")
    except Exception as exc:  # network errors are logged, the run continues
        log.error("failed %s: %s", url, exc)
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body, encoding="utf-8")
    log.info("saved %s (%d bytes)", dest.relative_to(ROOT), len(body))
    time.sleep(PAUSE_S)
    return body


def categories(index_html: str, event: str) -> list[str]:
    cats = re.findall(rf"/events/{re.escape(event)}/routes/([a-z0-9_-]+)/\"", index_html)
    out = []
    for c in cats:
        if c not in NOT_CATEGORIES and not re.fullmatch(r"cp\d+", c) and c not in out:
            out.append(c)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("events", nargs="*", default=TBILISI_EVENTS)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    setup_logging()
    for ev in args.events:
        d = RAW / ev
        idx = fetch(BASE.format(event=ev, page=""), d / "routes.html", args.force)
        if idx is None:
            continue
        fetch(BASE.format(event=ev, page="all/"), d / "all.html", args.force)
        cats = categories(idx, ev)
        log.info("%s: categories %s", ev, cats)
        for c in cats:
            fetch(BASE.format(event=ev, page=f"{c}/"), d / f"cat_{c}.html", args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
