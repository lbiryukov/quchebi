"""``python -m pipeline.ru fetch|batches|merge|notes|all [--offline]`` (see the package docstring)."""
from __future__ import annotations

import argparse
import sys

from pipeline.config import CITY, DB_PATH, ensure_dirs
from pipeline.db import open_db
from pipeline.log import get_logger

log = get_logger("pipeline.ru")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.ru", description="Reference stage of Russian-language cities")
    ap.add_argument("cmd", choices=("fetch", "batches", "merge", "all"))
    ap.add_argument("--offline", action="store_true", help="use cached sources only")
    ap.add_argument("--role", action="append", choices=("researcher", "classifier"),
                    help="batches: only these roles (default: every role whose input is ready)")
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args(argv)
    if CITY.lang != "ru":
        log.error("pipeline.ru is for Russian-language cities; QUCHEBI_CITY=%s has lang=%s", CITY.id, CITY.lang)
        return 2
    ensure_dirs()
    con = open_db(args.db)
    try:
        if args.cmd in ("fetch", "all"):
            from pipeline.ru import fetch

            fetch.run(con, offline=args.offline)
        if args.cmd in ("batches", "all"):
            from pipeline.ru import batches

            batches.run(con, roles=args.role)
        if args.cmd in ("merge", "all"):
            from pipeline.ru import merge

            merge.run(con)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
