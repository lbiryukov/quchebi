"""Pack validator: green on a clean export, catches corruption, writes the coverage report."""
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from pipeline.db import open_db
from pipeline.export.build import dumps, export_pack
from pipeline.validate.__main__ import main as validate_main
from pipeline.validate.pack import load_schema, validate_pack
from pipeline.validate.run import run_validation

FIX = Path(__file__).parent / "fixtures" / "export"
NOW = dt.datetime(2026, 10, 24, 12, 0)


def make_db(path=":memory:"):
    con = open_db(path)
    con.executescript((FIX / "fixture.sql").read_text(encoding="utf-8"))
    con.commit()
    return con


def export(con, out: Path, now=NOW):
    return export_pack(con, out, tiles_manifest=None, now=now, taxonomy_paths=(FIX / "taxonomy.json",),
                       stats_path=out.parent / "build" / "export_stats.json")


@pytest.fixture()
def packdir(tmp_path):
    con = make_db()
    out = tmp_path / "data"
    export(con, out)
    con.close()
    return out


def load(d: Path, name: str):
    return json.loads((d / name).read_text(encoding="utf-8"))


def rewrite(d: Path, name: str, obj) -> None:
    """Write a modified pack file and fix its manifest entry, so only the invariant under test fails."""
    data = dumps(obj)
    (d / name).write_bytes(data)
    m = load(d, "manifest.json")
    m["files"][name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")


def test_schemas_are_valid_json_schema():
    import jsonschema
    for name in ("manifest", "core", "geom", "houses", "areas", "eponyms"):
        jsonschema.Draft202012Validator.check_schema(load_schema(name))


def test_clean_export_is_green(packdir):
    res = validate_pack(packdir)
    assert res.errors == []
    assert res.warnings == []
    assert res.stats["actual_counts"]["features"] == 22


def test_tampered_file_breaks_hash(packdir):
    p = packdir / "houses.json"
    p.write_bytes(p.read_bytes().replace(b'"12"', b'"13"'))
    errs = validate_pack(packdir).errors
    assert any("houses.json: sha256" in e for e in errs)
    assert not any("houses.json: size" in e for e in errs)


def test_truncated_file_breaks_size_and_json(packdir):
    p = packdir / "core.json"
    p.write_bytes(p.read_bytes()[:-10])
    errs = validate_pack(packdir).errors
    assert any("core.json: size" in e for e in errs)
    assert any("core.json: not valid UTF-8 JSON" in e for e in errs)


def test_missing_file(packdir):
    (packdir / "areas.json").unlink()
    assert any("areas.json missing" in e for e in validate_pack(packdir).errors)


def test_index_out_of_range(packdir):
    core = load(packdir, "core.json")
    core["f"][5]["par"] = 999
    core["f"][6]["fam"] = 999
    core["f"][7]["d"] = [42]
    rewrite(packdir, "core.json", core)
    errs = " | ".join(validate_pack(packdir).errors)
    assert "par out of range" in errs and "fam out of range" in errs and "d out of range" in errs


def test_crossing_pair_order(packdir):
    core = load(packdir, "core.json")
    core["x"][0], core["x"][1] = core["x"][1], core["x"][0]
    rewrite(packdir, "core.json", core)
    assert any("not a < b" in e for e in validate_pack(packdir).errors)


def test_crossing_point_far_from_streets_warns(packdir):
    core = load(packdir, "core.json")
    core["xp"][0] += 500   # ~550 m north
    rewrite(packdir, "core.json", core)
    res = validate_pack(packdir)
    assert res.errors == []
    assert any("farther than 30 m" in w for w in res.warnings)


def test_schema_violation_null_field(packdir):
    core = load(packdir, "core.json")
    core["f"][0]["ru"] = None
    rewrite(packdir, "core.json", core)
    assert any(e.startswith("schema core.json: f/0/ru") for e in validate_pack(packdir).errors)


def test_house_without_feature_and_hn_mismatch(packdir):
    houses = load(packdir, "houses.json")
    houses["h"]["999"] = [["1", 1, "", "", 4169000, 4479000]]
    rewrite(packdir, "houses.json", houses)
    errs = " | ".join(validate_pack(packdir).errors)
    assert "not feature indices" in errs
    core = load(packdir, "core.json")
    i = next(i for i, f in enumerate(core["f"]) if f["hn"] > 0)
    core["f"][i]["hn"] += 1
    rewrite(packdir, "core.json", core)
    assert any("hn differs" in e for e in validate_pack(packdir).errors)


def test_bad_house_row_shape(packdir):
    houses = load(packdir, "houses.json")
    k = next(iter(houses["h"]))
    houses["h"][k][0] = houses["h"][k][0][:5]
    rewrite(packdir, "houses.json", houses)
    assert any(e.startswith("schema houses.json") for e in validate_pack(packdir).errors)


def test_category_not_in_taxonomy(packdir):
    eps = load(packdir, "eponyms.json")
    eps["e"][0]["cat"] = ["nope.cat"]
    rewrite(packdir, "eponyms.json", eps)
    assert any("categories not in taxonomy" in e for e in validate_pack(packdir).errors)


def test_manifest_counts_checked(packdir):
    m = load(packdir, "manifest.json")
    m["counts"]["houses"] += 1
    (packdir / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    assert any("manifest.counts.houses" in e for e in validate_pack(packdir).errors)


def test_broken_polyline(packdir):
    geom = load(packdir, "geom.json")
    geom["g"][0] = ["_p~iF~ps|U_ulLnnqC_mqNvxq`"]
    rewrite(packdir, "geom.json", geom)
    assert any("undecodable polyline" in e for e in validate_pack(packdir).errors)


def test_open_area_ring(packdir):
    areas = load(packdir, "areas.json")
    areas["districts"][0]["poly"][0][0] = "_p~iF~ps|U_ulLnnqC"
    rewrite(packdir, "areas.json", areas)
    assert any("ring not closed" in e for e in validate_pack(packdir).errors)


def test_tiles_checked_against_files(tmp_path):
    root = tmp_path                                   # app root: data/ and tiles/ side by side
    (root / "tiles").mkdir()
    blob = b"PMTiles fake payload"
    (root / "tiles" / "tbilisi.pmtiles").write_bytes(blob)
    tm = root / "tiles" / "tiles-manifest.json"
    tm.write_text(json.dumps({"basemap": "tbilisi.pmtiles", "files": {"tbilisi.pmtiles": {
        "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}}}), encoding="utf-8")
    con = make_db()
    export_pack(con, root / "data", tiles_manifest=tm, now=NOW, taxonomy_paths=(FIX / "taxonomy.json",),
                stats_path=None)
    con.close()
    res = validate_pack(root / "data")
    assert res.errors == [] and res.stats["tiles_files"] == 1
    (root / "tiles" / "tbilisi.pmtiles").write_bytes(blob[::-1])
    assert any("differ from manifest.tiles" in e for e in validate_pack(root / "data").errors)
    (root / "tiles" / "tbilisi.pmtiles").unlink()
    assert any("are missing" in e for e in validate_pack(root / "data").errors)


# ------------------------------------------------------------------ report, last_counts, CLI
def test_report_and_baseline_diff(tmp_path):
    con = make_db()
    out, build = tmp_path / "data", tmp_path / "build"
    export(con, out)
    r1 = run_validation(out, con, build, now=NOW)
    assert r1["check"].ok
    html = (build / "report.html").read_text(encoding="utf-8")
    assert "Отчёт покрытия пакета" in html and "По видам объектов" in html and "По районам" in html
    assert "Топ проблем" in html and "Неприсоединённые адреса" in html and "Мтацминда" in html
    assert "Прошлой сборки для сравнения нет" in html
    for bad in ("<script", "<link", "src=", "http://", "https://"):
        assert bad not in html
    last = json.loads((build / "last_counts.json").read_text(encoding="utf-8"))
    assert last["current"]["version"] == "20261024-1" and last["previous"] is None

    con.execute("DELETE FROM house WHERE id = 1")
    con.commit()
    export(con, out, now=dt.datetime(2026, 10, 25, 8, 0))
    run_validation(out, con, build, now=NOW)
    html = (build / "report.html").read_text(encoding="utf-8")
    assert "Сравнение со сборкой 20261024-1 от 24/10/2026" in html
    assert '<span class="down">-1 (-7.1%)</span>' in html          # houses 14 -> 13
    last = json.loads((build / "last_counts.json").read_text(encoding="utf-8"))
    assert last["current"]["version"] == "20261025-1" and last["previous"]["version"] == "20261024-1"
    # re-validating the same build keeps diffing against its predecessor
    run_validation(out, con, build, now=NOW)
    assert "Сравнение со сборкой 20261024-1" in (build / "report.html").read_text(encoding="utf-8")
    con.close()


def test_report_without_db(packdir, tmp_path):
    r = run_validation(packdir, None, tmp_path / "b2", now=NOW)
    assert r["check"].ok and r["golden_sql"] is None and r["golden_pack"] is not None
    assert not (tmp_path / "b2" / "golden_counts.json").exists()
    assert "Эталонные запросы" in (tmp_path / "b2" / "report.html").read_text(encoding="utf-8")


def test_broken_pack_does_not_update_baseline(packdir, tmp_path):
    core = load(packdir, "core.json")
    core["f"][0]["k"] = 999
    rewrite(packdir, "core.json", core)
    r = run_validation(packdir, None, tmp_path / "b3", now=NOW)
    assert not r["check"].ok
    assert (tmp_path / "b3" / "report.html").exists()
    assert not (tmp_path / "b3" / "last_counts.json").exists()


def test_cli_exit_codes(tmp_path):
    db = tmp_path / "runcity.sqlite"
    con = make_db(db)
    out = tmp_path / "data"
    export(con, out)
    con.close()
    assert validate_main(["--pack", str(out), "--db", str(db), "--build", str(tmp_path / "build")]) == 0
    assert (tmp_path / "build" / "golden_counts.json").exists()
    p = out / "geom.json"
    p.write_bytes(p.read_bytes() + b" ")
    assert validate_main(["--pack", str(out), "--no-db", "--no-report", "--build", str(tmp_path / "b")]) == 1
