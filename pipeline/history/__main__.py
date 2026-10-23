"""CLI: ``uv run python -m pipeline.history fetch|parse|link|batches|merge|all``.

fetch    download the 2008 encyclopedia PDF and ka/ru Wikipedia street articles (data/raw)
parse    extract the PDF text (cached), parse articles -> history_article (facts only)
link     link articles to features, rebuild name_history (needs the osm stages)
batches  queue low-confidence encyclopedia articles for the history-extractor subagent
merge    validate subagent answers (data/enrich/results/history) and rebuild name_history
all      fetch, parse, link, batches, merge
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.config import DB_PATH, ensure_dirs
from pipeline.db import open_db
from pipeline.log import get_logger, stage

log = get_logger("pipeline.history")

STEPS = ("fetch", "parse", "link", "batches", "merge")


def cmd_fetch(args) -> dict:
    from pipeline.history import enc2008, wiki
    with stage(log, "history.fetch") as st:
        st["pdf"] = str(enc2008.fetch())
        if not args.no_wiki:
            for lang in ("ka", "ru"):
                st[f"wiki_{lang}"] = str(wiki.fetch(lang))
        return st


def cmd_parse(args) -> dict:
    from pipeline.history import enc2008, wiki, wiki_parse
    from pipeline.history.store import replace_articles
    with stage(log, "history.parse") as st:
        if not enc2008.PDF_PATH.exists() and not enc2008.LINES_PATH.exists():
            raise SystemExit("encyclopedia PDF missing: run `uv run python -m pipeline.history fetch` first")
        if enc2008.PDF_PATH.exists():
            enc2008.extract(force=args.force_extract)
        recs = enc2008.parse()
        ka_pages, ru_pages = wiki.load("ka"), wiki.load("ru")
        ka_recs = [wiki_parse.parse_ka(t, p) for t, p in sorted(ka_pages.items())]
        ru_recs = [wiki_parse.parse_ru(t, p) for t, p in sorted(ru_pages.items())]
        con = open_db(args.db)
        try:
            replace_articles(con, recs, ("enc2008", "enc2008_bio"))
            replace_articles(con, ka_recs, ("kawiki",))
            replace_articles(con, ru_recs, ("ruwiki",))
        finally:
            con.close()
        arts = [r for r in recs if r["entry"] in ("article", "sub")]
        st.update({
            "enc_articles_found": len(arts),
            "enc_articles_parsed": sum(1 for r in arts if r["facts"].get("confidence", 0) >= 0.65),
            "enc_needs_llm": sum(1 for r in recs if r["needs_llm"]),
            "enc_bios": sum(1 for r in recs if r["entry"] == "bio"),
            "enc_former_names": sum(len(r["facts"].get("former", [])) for r in recs),
            "kawiki_articles": len(ka_recs),
            "kawiki_former_names": sum(len(r["facts"].get("former", [])) for r in ka_recs),
            "ruwiki_articles": len(ru_recs),
            "ruwiki_former_names": sum(len(r["facts"].get("former", [])) for r in ru_recs),
        })
        return st


def cmd_link(args) -> dict:
    from pipeline.history import link
    con = open_db(args.db)
    try:
        return link.run(con)
    finally:
        con.close()


def cmd_batches(args) -> dict:
    from pipeline.history import batches, enc2008
    con = open_db(args.db)
    try:
        return batches.run(con, enc2008.article_texts())
    finally:
        con.close()


def cmd_merge(args) -> dict:
    from pipeline.history import merge
    con = open_db(args.db)
    try:
        return merge.run(con)
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.history", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=STEPS + ("all",))
    ap.add_argument("--db", type=Path, default=DB_PATH, help="SQLite database path")
    ap.add_argument("--force-extract", action="store_true", help="re-extract the PDF text layer")
    ap.add_argument("--no-wiki", action="store_true", help="fetch: skip Wikipedia")
    args = ap.parse_args(argv)
    ensure_dirs()
    steps = STEPS if args.step == "all" else (args.step,)
    results = {}
    for s in steps:
        results[s] = globals()[f"cmd_{s}"](args)
    log.info("history %s: %s", args.step, json.dumps(results, ensure_ascii=False, default=str)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
