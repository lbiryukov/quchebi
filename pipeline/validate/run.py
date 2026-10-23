"""Validation stage: pack invariants, golden counts, coverage report, last-build diff."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from pipeline.config import APP_DATA, BUILD, DB_PATH
from pipeline.db import connect
from pipeline.log import get_logger, stage
from pipeline.validate import report as R
from pipeline.validate.golden import golden_pack, golden_sql
from pipeline.validate.pack import DATA_FILES, PackCheck, validate_pack

log = get_logger(__name__)


def _db_has_features(con: sqlite3.Connection) -> bool:
    try:
        return con.execute("SELECT COUNT(*) FROM feature").fetchone()[0] > 0
    except sqlite3.Error:
        return False


def run_validation(
    pack_dir: Path | str = APP_DATA,
    db: Path | str | sqlite3.Connection | None = DB_PATH,
    build_dir: Path | str = BUILD,
    *,
    write_report: bool = True,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Validate the pack, write golden counts and the coverage report.

    Returns ``{"check": PackCheck, "golden_sql": ..., "golden_pack": ..., "report": path | None}``.
    """
    pack_dir, build_dir = Path(pack_dir), Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"golden_sql": None, "golden_pack": None, "report": None}
    with stage(log, "validate", pack=str(pack_dir)) as st:
        res: PackCheck = validate_pack(pack_dir)
        out["check"] = res
        res.stats["sizes_gzip"] = R.gzip_sizes(pack_dir, list(DATA_FILES))
        have_pack = all(k in res.pack for k in ("core", "houses", "eponyms"))

        con = None
        own = False
        if isinstance(db, sqlite3.Connection):
            con = db
        elif db is not None and Path(db).exists():
            con, own = connect(db), True
        if con is not None and not _db_has_features(con):
            log.warning("database has no features: skipping SQL golden counts and DB problem checks")
            if own:
                con.close()
            con, own = None, False
        try:
            gp = None
            if have_pack:
                try:
                    gp = golden_pack(res.pack)
                except Exception as exc:  # broken indices: already reported by validate_pack
                    log.exception("golden counts on the pack failed")
                    res.err(f"golden counts on the pack failed: {type(exc).__name__}: {exc}")
            gs = golden_sql(con, res.pack["core"]["kinds"]) if con is not None and have_pack else None
            out["golden_pack"], out["golden_sql"] = gp, gs
            if gs is not None:
                (build_dir / "golden_counts.json").write_text(json.dumps(gs, ensure_ascii=False, indent=1),
                                                              encoding="utf-8")
                log.info("golden counts (SQL): %s", gs)
            if gs is not None and gp is not None and gs != gp:
                db_n = con.execute("SELECT COUNT(*) FROM feature").fetchone()[0]
                pack_n = len(res.pack["core"]["f"])
                diff = {q: (gs[q], gp.get(q)) for q in gs if gs[q] != gp.get(q)}
                if db_n != pack_n:
                    res.warn(f"golden counts differ, but the pack is stale ({pack_n} features vs {db_n} in DB): {diff}")
                else:
                    res.err(f"golden counts: SQL and pack disagree (sql, pack): {diff}")
            dbp = R.db_problems(con, res.pack["core"]["kinds"]) if con is not None and have_pack else None
        finally:
            if own and con is not None:
                con.close()

        export_stats = None
        try:
            export_stats = json.loads((build_dir / "export_stats.json").read_text(encoding="utf-8"))
            if export_stats.get("version") != res.pack.get("manifest", {}).get("version"):
                export_stats = None  # stats of another build
        except (OSError, ValueError):
            pass

        if write_report and have_pack:
            try:
                m = R.pack_metrics(res)
            except Exception:
                log.exception("coverage metrics failed on a broken pack")
                m = None
            last = build_dir / "last_counts.json"
            version = res.pack.get("manifest", {}).get("version")
            baseline = R.load_baseline(last, version)
            html_text = R.render_report(res, m, golden_sql=gs, golden_pack=gp, dbp=dbp, export_stats=export_stats,
                                        baseline=baseline, now=now)
            rpath = build_dir / "report.html"
            rpath.write_text(html_text, encoding="utf-8")
            out["report"] = rpath
            if res.ok and m is not None:
                R.save_baseline(last, R.snapshot(res, m, gs), baseline)
            else:
                log.warning("pack has errors: last_counts.json not updated")
            log.info("coverage report written to %s", rpath)
        for e in res.errors:
            log.error("pack error: %s", e)
        for w in res.warnings:
            log.warning("pack warning: %s", w)
        st["errors"] = len(res.errors)
        st["warnings"] = len(res.warnings)
    return out
