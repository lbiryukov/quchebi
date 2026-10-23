"""Extract CP blocks (riddles and plain addresses) from downloaded Runcity route pages.

Input:  data/raw/runcity/<event>/all.html and cat_<category>.html (see fetch_runcity.py).
Output: data/raw/runcity/extracted.json — full texts, for local curation only. The file lives
        in data/raw (gitignored): riddle texts are copyrighted and never go to tests/acceptance.

Only CPs inside the Tbilisi bounding box are kept (multi-city events such as call2026).

Usage: uv run python tests/acceptance/tools/extract_runcity.py [--events e1 e2 ...]
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw" / "runcity"
LOGS = ROOT / "logs"
OUT = RAW / "extracted.json"
TBILISI_BBOX = (41.60, 44.60, 41.86, 45.05)  # minlat, minlon, maxlat, maxlon

CATEGORY_RU = {
    "sphynx": "Сфинкс", "lion": "Лев", "lion-pro": "Лев", "lion-mini": "Лев",
    "lion-mini-plus": "Лев", "griffin": "Грифон", "atlant": "Атлант", "quest": "квест",
}

log = logging.getLogger("acceptance.extract")


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


def text_of(fragment: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


def dd(block: str, cls: str) -> str:
    m = re.search(rf'<dd class="{cls}"[^>]*>(.*?)</dd>', block, re.S)
    return text_of(m.group(1)) if m else ""


def parse_page(page: str) -> list[dict]:
    parts = re.split(r'(?=<dt id="cp\d+")', page)
    out = []
    for part in parts[1:]:
        m = re.match(r'<dt id="cp(\d+)"[^>]*>(.*?)</dt>', part, re.S)
        if not m:
            continue
        kp = int(m.group(1))
        dt_html = m.group(2)
        media = re.findall(r'href="(https?://img\.runcity\.org/[^"]+)"', dt_html)
        header = text_of(re.sub(r'<a href="/ru/events/[^"]*">\d+</a>', " ", dt_html))
        header = header.replace("\U0001F3B5", "").strip()
        lat = re.search(r'class="latitude" title="([\d.]+)"', part)
        lon = re.search(r'class="longitude" title="([\d.]+)"', part)
        desc_html = re.search(r'<dd class="description"[^>]*>(.*?)</dd>', part, re.S)
        if desc_html:
            media += re.findall(r'(?:src|href)="(https?://img\.runcity\.org/[^"]+)"', desc_html.group(1))
        longans = dd(part, "longanswer")
        rec = {
            "kp": kp,
            "header": header,
            "is_riddle": header.lower().startswith("загадка"),
            "lat": float(lat.group(1)) if lat else None,
            "lon": float(lon.group(1)) if lon else None,
            "description": dd(part, "description"),
            "quest": dd(part, "quest"),
            "explanation": re.sub(r"^Отгадка\s*", "", longans).strip(),
            "answer": dd(part, "answer"),
            "history": dd(part, "history"),
            "media": sorted(set(media)),
        }
        out.append(rec)
    return out


def in_tbilisi(rec: dict) -> bool:
    if rec["lat"] is None:
        return False
    a, b, c, d = TBILISI_BBOX
    return a <= rec["lat"] <= c and b <= rec["lon"] <= d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", nargs="*")
    args = ap.parse_args()
    setup_logging()
    events = args.events or sorted(p.name for p in RAW.iterdir() if (p / "all.html").exists())
    result = {}
    for ev in events:
        d = RAW / ev
        recs = parse_page((d / "all.html").read_text(encoding="utf-8", errors="replace"))
        cats: dict[int, list[str]] = {}
        for cf in sorted(d.glob("cat_*.html")):
            cat = cf.stem[4:]
            for r in parse_page(cf.read_text(encoding="utf-8", errors="replace")):
                cats.setdefault(r["kp"], []).append(cat)
        kept = []
        for r in recs:
            if not in_tbilisi(r):
                continue
            r["categories"] = cats.get(r["kp"], [])
            r["categories_ru"] = sorted({CATEGORY_RU.get(c, c) for c in r["categories"]})
            kept.append(r)
        n_r = sum(1 for r in kept if r["is_riddle"])
        log.info("%s: %d CPs total, %d in Tbilisi, %d riddles", ev, len(recs), len(kept), n_r)
        result[ev] = kept
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("wrote %s", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
