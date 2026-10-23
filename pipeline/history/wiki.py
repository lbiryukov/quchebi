"""Fetch Tbilisi street articles from ka/ru Wikipedia (MediaWiki API) into ``data/raw/wiki``.

Category trees are walked with ``list=categorymembers``; page texts and Wikidata ids come
from ``prop=revisions|pageprops`` in chunks of 50 titles. Every API response is cached on disk,
so re-runs are offline. Texts are CC BY-SA and stay in ``data/raw`` only.
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.config import RAW
from pipeline.history.http import api_json
from pipeline.log import get_logger

log = get_logger(__name__)

WIKI_DIR = RAW / "wiki"

# (root category, depth) per language. Depth 2 reaches district and "former streets" subcats.
CATEGORIES = {
    "ka": [("კატეგორია:თბილისის ქუჩები", 3), ("კატეგორია:თბილისის მოედნები", 2),
           ("კატეგორია:თბილისის გამზირები", 1)],
    "ru": [("Категория:Улицы Тбилиси", 3), ("Категория:Площади Тбилиси", 2),
           ("Категория:Проспекты Тбилиси", 1)],
}


def api_url(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/w/api.php"


def _api(lang: str, params: dict, cache_dir: Path) -> dict:
    p = dict(params, format="json", formatversion="2")
    return api_json(api_url(lang), p, cache_dir=cache_dir, min_interval=1.0)


def category_members(lang: str, root: str, depth: int, cache_dir: Path) -> dict[str, list[str]]:
    """Return {page title: [categories through which it was reached]} (namespace 0 only)."""
    pages: dict[str, list[str]] = {}
    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(root, depth)]
    while queue:
        cat, d = queue.pop(0)
        if cat in seen:
            continue
        seen.add(cat)
        cont: dict = {}
        n_pages = n_sub = 0
        while True:
            data = _api(lang, {"action": "query", "list": "categorymembers", "cmtitle": cat,
                               "cmlimit": "500", "cmtype": "page|subcat", **cont}, cache_dir)
            for m in data.get("query", {}).get("categorymembers", []):
                if m.get("ns") == 14:
                    n_sub += 1
                    if d > 0:
                        queue.append((m["title"], d - 1))
                elif m.get("ns") == 0:
                    n_pages += 1
                    pages.setdefault(m["title"], []).append(cat)
            if "continue" in data:
                cont = data["continue"]
            else:
                break
        log.debug("wiki %s: %s -> %d pages, %d subcats (depth left %d)", lang, cat, n_pages, n_sub, d)
    log.info("wiki %s: %s (depth %d) -> %d pages from %d categories", lang, root, depth, len(pages), len(seen))
    return pages


def page_contents(lang: str, titles: list[str], cache_dir: Path) -> dict[str, dict]:
    """Return {title: {"pageid", "qid", "text"}} for the given titles."""
    res: dict[str, dict] = {}
    titles = sorted(titles)
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        data = _api(lang, {"action": "query", "prop": "revisions|pageprops", "rvprop": "content",
                           "rvslots": "main", "ppprop": "wikibase_item", "redirects": "1",
                           "titles": "|".join(chunk)}, cache_dir)
        for p in data.get("query", {}).get("pages", []):
            if p.get("missing"):
                continue
            rev = (p.get("revisions") or [{}])[0]
            text = rev.get("slots", {}).get("main", {}).get("content", "")
            res[p["title"]] = {"pageid": p.get("pageid"), "qid": p.get("pageprops", {}).get("wikibase_item"),
                               "text": text}
    return res


def fetch(lang: str, out_dir: Path = WIKI_DIR) -> Path:
    """Crawl the category trees of ``lang`` and save ``{lang}_pages.json``. Returns its path."""
    cache_dir = out_dir / "api_cache" / lang
    members: dict[str, list[str]] = {}
    for root, depth in CATEGORIES[lang]:
        for title, cats in category_members(lang, root, depth, cache_dir).items():
            members.setdefault(title, [])
            members[title].extend(c for c in cats if c not in members[title])
    contents = page_contents(lang, list(members), cache_dir)
    for title, rec in contents.items():
        rec["categories"] = members.get(title, [])
    out = out_dir / f"{lang}_pages.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(contents, ensure_ascii=False), encoding="utf-8")
    log.info("wiki %s: saved %d articles (%d with QID) to %s", lang, len(contents),
             sum(1 for v in contents.values() if v.get("qid")), out)
    return out


def load(lang: str, out_dir: Path = WIKI_DIR) -> dict[str, dict]:
    path = out_dir / f"{lang}_pages.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
