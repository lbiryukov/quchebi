"""CLI: ``uv run python -m pipeline.export [--db PATH] [--out DIR] [--tiles-manifest PATH]``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.config import APP_DATA, DB_PATH, ensure_dirs
from pipeline.db import open_db
from pipeline.export.build import EXPORT_STATS, TILES_MANIFEST, export_pack
from pipeline.log import get_logger

log = get_logger("pipeline.export")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.export", description="Export the phone pack from SQLite.")
    ap.add_argument("--db", type=Path, default=DB_PATH, help="SQLite database (default: data/runcity.sqlite)")
    ap.add_argument("--out", type=Path, default=APP_DATA, help="output directory (default: app/public/data)")
    ap.add_argument("--tiles-manifest", type=Path, default=TILES_MANIFEST, help="basemap tiles-manifest.json")
    ap.add_argument("--stats", type=Path, default=EXPORT_STATS, help="export stats JSON (not shipped)")
    args = ap.parse_args(argv)
    ensure_dirs()
    if not args.db.exists():
        log.error("database %s does not exist", args.db)
        return 2
    con = open_db(args.db)
    try:
        res = export_pack(con, args.out, tiles_manifest=args.tiles_manifest, stats_path=args.stats)
    finally:
        con.close()
    m = res["manifest"]
    log.info("pack %s written to %s: %s", m["version"], args.out, m["counts"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
