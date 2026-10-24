"""Whole OSM import: synthetic run, idempotency, CLI, and a smoke test on the real PBF (skipped if absent)."""
from pathlib import Path

import pytest

from pipeline import cli
from pipeline.db import connect
from pipeline.osm.extract import read_overpass
from pipeline.osm.fetch import find_pbfs
from pipeline.osm.model import Extract
from pipeline.osm.run import build_parser, parse_stages, run, table_counts

FIX = Path(__file__).parent / "fixtures" / "osm" / "overpass"


def _args(db, *extra):
    return build_parser().parse_args(["--source", "overpass", "--overpass-dir", str(FIX), "--db", str(db), *extra])


def _dump(con):
    out = {}
    for t, cols in (("crossing", "a, b, lat, lon, kind, dir_a, dir_b"), ("house", "feature_id, raw, n, match"),
                    ("feature", "id, kind, ka, parent_id, family_id"), ("feature_area", "feature_id, area_id, share")):
        out[t] = [tuple(r) for r in con.execute(f"SELECT {cols} FROM {t} ORDER BY {cols}")]
    return out


def test_parse_stages():
    assert parse_stages("all") == ["areas", "features", "landmarks", "feature_area", "crossings", "houses"]
    assert parse_stages("houses,areas") == ["areas", "houses"]
    assert parse_stages("features") == ["features", "landmarks"]  # features removes the landmarks
    assert parse_stages("landmarks,houses") == ["landmarks", "houses"]
    with pytest.raises(SystemExit):
        parse_stages("bogus")


def test_extract_json_roundtrip(tmp_path):
    ex = read_overpass(FIX)
    p = tmp_path / "ex.json"
    ex.save(p)
    ex2 = Extract.load(p)
    assert ex2.counts() == ex.counts()
    assert ex2.highways[0].nodes == ex.highways[0].nodes
    assert ex2.boundary.equals_exact(ex.boundary, 1e-6)


def test_synthetic_run_and_idempotency(tmp_path):
    db = tmp_path / "s.sqlite"
    s1 = run(_args(db))
    assert s1["tables"]["area"] == 5 and s1["tables"]["crossing"] == 3 and s1["tables"]["streets"] == 13
    con = connect(db)
    first = _dump(con)
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    assert meta["osm_snapshot"] == "2026-10-20T00:00:00Z" and meta["osm_source"] == "overpass"
    con.close()
    run(_args(db))
    con = connect(db)
    assert _dump(con) == first
    # re-running one stage keeps the others
    run(_args(db, "--stages", "houses"))
    assert _dump(con) == first
    assert table_counts(con)["house"] == s1["tables"]["house"]
    con.close()


def test_cli_osm_and_all(tmp_path, monkeypatch):
    db = tmp_path / "c.sqlite"
    assert cli.main(["osm", "--source", "overpass", "--overpass-dir", str(FIX), "--db", str(db)]) == 0
    monkeypatch.setattr(cli, "AFTER_OSM", ("does_not_exist_pkg",))
    rc = cli.main(["all", "--source", "overpass", "--overpass-dir", str(FIX), "--db", str(db)])
    assert rc == 0
    called = []
    monkeypatch.setattr(cli, "AFTER_OSM", ("fakepkg",))
    monkeypatch.setattr(cli, "_find_main", lambda pkg: (lambda argv: called.append(argv) or 0))
    assert cli.main(["all", "--offline", "--source", "overpass", "--overpass-dir", str(FIX), "--db", str(db)]) == 0
    assert called == [["--offline"]]


@pytest.mark.skipif(not find_pbfs(), reason="no Geofabrik PBF in data/raw/osm (run: uv run python -m pipeline.cli osm)")
def test_smoke_full_import_on_real_pbf(tmp_path):
    db = tmp_path / "real.sqlite"
    args = build_parser().parse_args(["--offline", "--db", str(db)])  # uses data/interim extract cache if fresh
    s = run(args)
    t = s["tables"]
    assert t["area"] > 100 and s["areas"]["districts"] == 10
    assert abs(t["streets"] - 5178) <= 0.01 * 5178
    assert abs(t["crossing_pairs"] - 8700) <= 0.01 * 8700
    assert s["houses"]["street_matched_pct"] >= 97.0
    assert s["features"]["kinds"]["metro"] >= 20
