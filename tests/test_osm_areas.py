"""Areas and feature_area stages on the synthetic fixture."""
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import LineString

from pipeline.db import connect
from pipeline.osm.areas import build_areas
from pipeline.osm.extract import read_overpass
from pipeline.osm.feature_area import sample_line
from pipeline.osm.run import build_parser, run

FIX = Path(__file__).parent / "fixtures" / "osm" / "overpass"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("osm_areas") / "syn.sqlite"
    run(build_parser().parse_args(["--source", "overpass", "--overpass-dir", str(FIX), "--db", str(path)]))
    con = connect(path)
    yield con
    con.close()


def test_build_areas_dedupe_and_parents():
    areas, st = build_areas(read_overpass(FIX))
    names = [(a.kind, a.ka) for a in areas]
    assert names[:2] == [("district", "ვაკის რაიონი"), ("district", "მთაწმინდის რაიონი")]
    assert sorted(names[2:]) == [("neighbourhood", "აბანოთუბანი"), ("neighbourhood", "სოლოლაკი"),
                                 ("quarter", "მთაწმინდა, სოლოლაკი")]
    assert st["skip_admin_dup"] == 2 and st["skip_point_has_polygon"] == 1
    by = {a.ka: a for a in areas}
    idx = {a.ka: i for i, a in enumerate(areas)}
    assert by["სოლოლაკი"].parent == idx["მთაწმინდა, სოლოლაკი"]       # smallest containing polygon
    assert by["მთაწმინდა, სოლოლაკი"].parent == idx["მთაწმინდის რაიონი"]
    assert by["აბანოთუბანი"].parent == idx["ვაკის რაიონი"] and by["აბანოთუბანი"].area_km2 is None
    assert by["სოლოლაკი"].area_km2 == pytest.approx(0.36, rel=0.01)


def test_area_rows_and_features(db):
    rows = db.execute("SELECT a.kind, a.ka, p.ka AS parent FROM area a LEFT JOIN area p ON p.id = a.parent_id "
                      "ORDER BY a.id").fetchall()
    assert [tuple(r) for r in rows][2:] == [
        ("quarter", "მთაწმინდა, სოლოლაკი", "მთაწმინდის რაიონი"),
        ("neighbourhood", "სოლოლაკი", "მთაწმინდა, სოლოლაკი"),
        ("neighbourhood", "აბანოთუბანი", "ვაკის რაიონი"),
    ]
    f = db.execute("SELECT f.kind, f.area_id, a.ka FROM feature f JOIN area a ON a.id = f.area_id "
                   "WHERE f.ka = 'სოლოლაკი'").fetchone()
    assert f["kind"] == "neighbourhood" and f["ka"] == "სოლოლაკი"
    assert db.execute("SELECT geom_wkt FROM area WHERE ka = 'აბანოთუბანი'").fetchone()[0] is None
    assert db.execute("SELECT ru FROM area WHERE ka = 'მთაწმინდის რაიონი'").fetchone()[0] == "Мтацминда"


def test_sample_line_pieces():
    pts, w = sample_line(LineString([(0, 0), (120, 0)]), step=50)
    assert len(pts) == 3 and np.allclose(w, 40) and np.allclose(pts[:, 0], [20, 60, 100])
    pts, w = sample_line(LineString([(0, 0), (30, 0), (30, 30)]), step=50)
    assert w.sum() == pytest.approx(60)


def shares(db, ka):
    q = ("SELECT a.ka, fa.share FROM feature_area fa JOIN area a ON a.id = fa.area_id JOIN feature f ON f.id = fa.feature_id "
         "WHERE f.ka = ? AND f.id = (SELECT MIN(id) FROM feature WHERE ka = ?)")
    return {r[0]: r[1] for r in db.execute(q, (ka, ka))}


def test_feature_area_line_shares(db):
    s = shares(db, "ლადო ასათიანის ქუჩა")   # x -600..900 minus a 50 m gap: 600 m west of x=0, 850 m east
    assert s["მთაწმინდის რაიონი"] == pytest.approx(600 / 1450, abs=1e-3)
    assert s["ვაკის რაიონი"] == pytest.approx(850 / 1450, abs=1e-3)
    assert s["სოლოლაკი"] == pytest.approx(400 / 1450, abs=1e-3)               # smallest polygon wins
    assert s["მთაწმინდა, სოლოლაკი"] == pytest.approx(100 / 1450, abs=1e-3)
    assert shares(db, "ბესიკის ქუჩა")["აბანოთუბანი"] == pytest.approx(1.0)    # point place within 300 m


def test_feature_area_points_polygons_and_areas(db):
    assert shares(db, "რუსთაველი") == {"მთაწმინდის რაიონი": 1.0}
    assert shares(db, "რიგის სკვერი") == {"მთაწმინდის რაიონი": 1.0, "სოლოლაკი": 1.0}
    assert shares(db, "სოლოლაკი") == {"სოლოლაკი": 1.0, "მთაწმინდის რაიონი": 1.0}
    assert shares(db, "ვაკის რაიონი") == {"ვაკის რაიონი": 1.0}
