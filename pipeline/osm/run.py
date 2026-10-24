"""Run the OSM import stages in order: fetch -> extract -> areas -> features -> landmarks ->
feature_area -> crossings -> houses. ``main(argv)`` is used by ``pipeline.cli osm``.

``features`` deletes every non-area feature (landmarks included), so asking for ``features`` always
runs ``landmarks`` too."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from pipeline.config import DB_PATH, ensure_dirs
from pipeline.db import open_db, set_meta, tx
from pipeline.log import RUN_ID, get_logger, stage
from pipeline.osm import areas, crossings, feature_area, features, houses, landmarks
from pipeline.osm.extract import EXTRACT_CACHE, load_extract
from pipeline.osm.fetch import DEFAULT_MAX_AGE_DAYS, OVERPASS_CACHE, fetch

log = get_logger(__name__)

STAGES = ("areas", "features", "landmarks", "feature_area", "crossings", "houses")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pipeline.cli osm", description="Import OSM data for Tbilisi into SQLite.")
    ap.add_argument("--offline", action="store_true", help="never download; use the newest local PBF or the Overpass cache")
    ap.add_argument("--source", choices=("auto", "pbf", "overpass"), default="auto",
                    help="data source (auto: PBF, Overpass cache only when no PBF is available)")
    ap.add_argument("--stages", default="all", help="comma list of " + ",".join(STAGES) + " (default: all)")
    ap.add_argument("--db", type=Path, default=DB_PATH, help="SQLite database path")
    ap.add_argument("--no-cache", action="store_true", help="re-read the PBF instead of the JSON extract cache")
    ap.add_argument("--max-age-days", type=float, default=DEFAULT_MAX_AGE_DAYS,
                    help="re-download the PBF when the newest local one is older than this")
    ap.add_argument("--overpass-dir", type=Path, default=OVERPASS_CACHE, help="Overpass cache folder (fallback)")
    ap.add_argument("--cache-path", type=Path, default=EXTRACT_CACHE, help=argparse.SUPPRESS)
    return ap


def parse_stages(spec: str) -> list[str]:
    if spec in ("", "all"):
        return list(STAGES)
    out = [s.strip() for s in spec.split(",") if s.strip()]
    bad = [s for s in out if s not in STAGES]
    if bad:
        raise SystemExit(f"unknown stage(s): {bad}; known: {list(STAGES)}")
    if "features" in out and "landmarks" not in out:
        log.info("stage 'features' removes landmarks: adding stage 'landmarks'")
        out.append("landmarks")
    return [s for s in STAGES if s in out]


def clear_all(con) -> None:
    """Empty every OSM-owned table except ``family`` (the features stage carries family enrichment
    over by key) so that a full rebuild numbers areas and features from 1 again."""
    with tx(con):
        con.execute("DELETE FROM house")
        con.execute("DELETE FROM crossing")
        con.execute("DELETE FROM near_pair")
        con.execute("DELETE FROM feature_area")
        con.execute("DELETE FROM feature_name")
        con.execute("UPDATE feature SET parent_id = NULL")
        con.execute("DELETE FROM feature")
        con.execute("UPDATE area SET parent_id = NULL")
        con.execute("DELETE FROM area")


def table_counts(con) -> dict[str, int]:
    out = {}
    for t in ("area", "family", "feature", "feature_name", "feature_area", "crossing", "near_pair", "house"):
        out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    # "streets" = features built from named highway ways (any kind: street, lane, square, bridge ...)
    out["streets"] = con.execute("SELECT COUNT(*) FROM feature WHERE highway IS NOT NULL").fetchone()[0]
    ph = ",".join("?" * len(landmarks.LANDMARK_KINDS))
    out["landmarks"] = con.execute(f"SELECT COUNT(*) FROM feature WHERE kind IN ({ph})",
                                   sorted(landmarks.LANDMARK_KINDS)).fetchone()[0]
    out["crossing_pairs"] = con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT a, b FROM crossing)").fetchone()[0]
    out["houses_matched_objects"] = con.execute(
        "SELECT COUNT(DISTINCT osm_type || osm_id) FROM house WHERE feature_id IS NOT NULL").fetchone()[0]
    out["houses_objects"] = con.execute("SELECT COUNT(DISTINCT osm_type || osm_id) FROM house").fetchone()[0]
    return out


def run(args: argparse.Namespace) -> dict:
    ensure_dirs()
    t0 = time.perf_counter()
    stages = parse_stages(args.stages)
    summary: dict = {"run_id": RUN_ID, "stages": stages}
    with stage(log, "osm", offline=args.offline, source=args.source, stages=",".join(stages), db=str(args.db)) as out:
        pbf, snapshot = None, None
        if args.source in ("auto", "pbf"):
            info = fetch(offline=args.offline, max_age_days=args.max_age_days)
            if info is None and args.source == "pbf":
                raise SystemExit("no PBF available (offline) and --source pbf was requested")
            if info is not None:
                pbf, snapshot = info.path, info.snapshot
        ex = load_extract(pbf, snapshot, args.overpass_dir, use_cache=not args.no_cache, cache_path=args.cache_path)
        summary["extract"] = ex.counts()
        con = open_db(args.db)
        try:
            with tx(con):
                set_meta(con, "osm_snapshot", ex.snapshot or "")
                set_meta(con, "osm_source", "geofabrik-pbf" if ex.source == "pbf" else ex.source)
                set_meta(con, "osm_file", (pbf.name if pbf else str(args.overpass_dir)))
                set_meta(con, "osm_imported_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            if "areas" in stages and "features" in stages:
                clear_all(con)
            if "areas" in stages:
                summary["areas"] = areas.run(con, ex)
            if "features" in stages:
                summary["features"] = features.run(con, ex)
            if "landmarks" in stages:
                summary["landmarks"] = landmarks.run(con, ex)
            if "feature_area" in stages:
                summary["feature_area"] = feature_area.run(con)
            if "crossings" in stages:
                summary["crossings"] = crossings.run(con, ex)
            if "houses" in stages:
                summary["houses"] = houses.run(con, ex)
            counts = table_counts(con)
            summary["tables"] = counts
            with tx(con):
                set_meta(con, "osm_counts", json.dumps(counts, ensure_ascii=False))
        finally:
            con.close()
        out.update(counts)
    summary["seconds"] = round(time.perf_counter() - t0, 1)
    log.info("OSM import summary: %s", json.dumps(summary, ensure_ascii=False, default=str))
    return summary


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
