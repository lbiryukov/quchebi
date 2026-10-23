"""CLI: ``uv run python -m pipeline.validate [--pack DIR] [--db PATH] [--build DIR] [--no-db] [--no-report]``.

Exit code 0 when the pack has no errors, 1 otherwise.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.config import APP_DATA, BUILD, DB_PATH
from pipeline.log import get_logger
from pipeline.validate.run import run_validation

log = get_logger("pipeline.validate")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.validate",
                                 description="Validate the phone pack, write golden counts and the coverage report.")
    ap.add_argument("--pack", type=Path, default=APP_DATA, help="pack directory (default: app/public/data)")
    ap.add_argument("--db", type=Path, default=DB_PATH, help="SQLite database (default: data/runcity.sqlite)")
    ap.add_argument("--build", type=Path, default=BUILD, help="output directory for report.html, golden_counts.json")
    ap.add_argument("--no-db", action="store_true", help="skip SQL golden counts and DB problem checks")
    ap.add_argument("--no-report", action="store_true", help="do not write report.html / last_counts.json")
    args = ap.parse_args(argv)
    out = run_validation(args.pack, None if args.no_db else args.db, args.build, write_report=not args.no_report)
    res = out["check"]
    status = "OK" if res.ok else "FAILED"
    log.info("validation %s: %d errors, %d warnings; report: %s", status, len(res.errors), len(res.warnings),
             out["report"])
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
