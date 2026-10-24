"""Landmarks stage: tag classification, name refinement, merging, duplicates, parents and house flags.

Synthetic fixture: tests/fixtures/osm/overpass (``landmarks`` list in make_synthetic.py).
"""
import json
from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon

from pipeline.db import connect
from pipeline.names.parse import LANDMARK_KINDS
from pipeline.osm.extract import is_landmark_candidate, landmark_kind, read_overpass
from pipeline.osm.geo import unproj
from pipeline.osm.landmarks import classify, cluster, is_generic_name, refine_kind
from pipeline.osm.model import OsmObj
from pipeline.osm.run import build_parser, run

FIX = Path(__file__).parent / "fixtures" / "osm" / "overpass"


def _pt(x, y):
    lat, lon = unproj(x, y)
    return Point(lon, lat)


def _rect(x0, y0, x1, y1):
    return Polygon([(p.x, p.y) for p in (_pt(x0, y0), _pt(x1, y0), _pt(x1, y1), _pt(x0, y1))])


@pytest.mark.parametrize("tags, area, kind", [
    ({"tourism": "museum"}, 0, "museum"),
    ({"tourism": "gallery"}, 0, "museum"),
    ({"amenity": "theatre"}, 0, "theatre"),
    ({"building": "cinema", "tourism": "attraction", "historic": "building"}, 0, "cinema"),
    ({"amenity": "place_of_worship", "religion": "christian"}, 0, "church"),
    ({"amenity": "place_of_worship", "religion": "muslim"}, 0, "mosque"),
    ({"building": "synagogue"}, 0, "synagogue"),
    ({"historic": "memorial", "memorial": "statue"}, 0, "monument"),
    ({"tourism": "artwork", "artwork_type": "sculpture"}, 0, "monument"),
    ({"historic": "memorial", "memorial": "plaque"}, 0, "plaque"),
    ({"tourism": "artwork", "artwork_type": "star"}, 0, "plaque"),
    ({"tourism": "artwork", "artwork_type": "graffiti"}, 0, None),
    ({"historic": "castle"}, 0, "fortress"),
    ({"amenity": "public_bath"}, 0, "bath"),
    ({"leisure": "stadium"}, 0, "stadium"),
    ({"leisure": "sports_centre"}, 0, None),
    ({"leisure": "sports_centre"}, 8000, "stadium"),
    ({"leisure": "pitch"}, 0, None),
    ({"shop": "mall"}, 0, "mall"),
    ({"shop": "department_store"}, 0, "mall"),
    ({"landuse": "retail"}, 5000, "mall"),
    ({"landuse": "retail", "amenity": "fuel"}, 5000, None),
    ({"amenity": "marketplace", "landuse": "retail"}, 5000, "market"),
    ({"railway": "halt"}, 0, "station"),
    ({"railway": "station", "station": "subway"}, 0, None),   # metro is a POI of the features stage
    ({"railway": "stop", "public_transport": "stop_position"}, 0, None),
    ({"amenity": "bus_station"}, 0, "station"),
    ({"aerialway": "station"}, 0, "station"),
    ({"amenity": "university"}, 0, "university"),
    ({"amenity": "research_institute"}, 0, "university"),
    ({"amenity": "school"}, 0, "school"),
    ({"amenity": "library"}, 0, "library"),
    ({"landuse": "cemetery"}, 0, "cemetery"),
    ({"landuse": "industrial"}, 0, "factory"),
    ({"landuse": "residential"}, 0, None),
    ({"landuse": "residential"}, 9000, "residential"),
    ({"tourism": "attraction"}, 0, "landmark"),
    ({"tourism": "viewpoint"}, 0, "landmark"),
    ({"leisure": "park", "tourism": "attraction"}, 0, None),   # a park
    ({"man_made": "bridge", "tourism": "attraction"}, 0, None),  # a bridge
    ({"highway": "pedestrian", "tourism": "attraction"}, 0, None),
    ({"amenity": "cafe"}, 0, None),
])
def test_landmark_kind(tags, area, kind):
    assert landmark_kind({"name": "X", **tags}, area) == kind


def test_candidates_and_unnamed():
    assert landmark_kind({"tourism": "museum"}) is None
    assert is_landmark_candidate({"name": "X", "leisure": "sports_centre"})  # kind depends on the area
    assert not is_landmark_candidate({"name": "X", "amenity": "cafe"})
    assert {"museum", "church", "landmark", "residential"} <= LANDMARK_KINDS


def test_refine_kind_and_generic_names():
    assert refine_kind("landmark", {"name": "ორბელიანების სასახლე"}) == "palace"
    assert refine_kind("landmark", {"name": "X", "name:ru": "Музей Y"}) == "museum"
    assert refine_kind("fortress", {"name": "არშაკუნის სასახლე"}) == "palace"
    assert refine_kind("fortress", {"name": "ნარიყალა"}) == "fortress"
    assert refine_kind("museum", {"name": "სასახლე"}) == "museum"  # specific kinds are kept
    for n in ("ეკლესია", "Monument", "A ბლოკი", "Block C", "12ბ", "023", "Hall 3"):
        assert is_generic_name(n), n
    for n in ("ნარიყალა", "№90 საჯარო სკოლა", "2 კმ", "City Star", "EVE"):
        assert not is_generic_name(n), n


def test_cluster_merges_node_and_building_only_for_compatible_kinds():
    objs = [
        OsmObj("n", 1, {"name": "მუზეუმი X", "tourism": "museum"}, _pt(10, 10)),
        OsmObj("w", 2, {"name": "მუზეუმი X", "building": "yes", "tourism": "attraction"}, _rect(0, 0, 30, 30)),
        OsmObj("n", 3, {"name": "მუზეუმი X", "tourism": "museum"}, _pt(500, 500)),     # far: separate
        OsmObj("n", 4, {"name": "კავეა", "amenity": "cinema"}, _pt(15, 15)),
        OsmObj("n", 5, {"name": "კავეა", "amenity": "theatre"}, _pt(16, 16)),          # other kind: separate
    ]
    cands, st = classify(objs)
    assert [c.kind for c in cands] == ["museum", "museum", "museum", "cinema", "theatre"]  # w2 refined by name
    groups = sorted(sorted(c.obj.ref for c in g) for g in cluster(cands))
    assert groups == [["n1", "w2"], ["n3"], ["n4"], ["n5"]]


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("lm") / "s.sqlite"
    s = run(build_parser().parse_args(["--source", "overpass", "--overpass-dir", str(FIX), "--db", str(path)]))
    con = connect(path)
    yield con, s
    con.close()


def _lm(con, ka):
    rows = con.execute("SELECT * FROM feature WHERE ka = ?", (ka,)).fetchall()
    assert len(rows) == 1, (ka, len(rows))
    return rows[0]


def test_extract_reads_landmarks():
    ex = read_overpass(FIX)
    refs = {o.ref for o in ex.landmarks}
    assert {"n601", "w602", "n606", "w608", "w611"} <= refs
    assert "n201" not in refs  # metro


def test_stage_kinds_merge_and_duplicates(db):
    con, s = db
    st = s["landmarks"]
    assert s["stages"] == ["areas", "features", "landmarks", "feature_area", "crossings", "houses"]
    museum = _lm(con, "ხელოვნების მუზეუმი")
    assert museum["kind"] == "museum" and json.loads(museum["osm_ids"]) == ["n601", "w602"]
    assert museum["ru"] == "Музей искусств" and museum["en"] == "Art Museum" and museum["ru_generated"] == 0
    assert museum["geom_wkt"].startswith("POINT")          # a small building is shipped as a point
    assert museum["family_id"] is None and museum["len_m"] == 0
    assert _lm(con, "სიონის ტაძარი")["kind"] == "church"
    assert _lm(con, "ჯუმა მეჩეთი")["kind"] == "mosque"
    assert _lm(con, "დიდი სინაგოგა")["kind"] == "synagogue"
    assert _lm(con, "ლადო ასათიანი")["kind"] == "monument"
    assert _lm(con, "ილია ჭავჭავაძე")["kind"] == "plaque"
    assert _lm(con, "ორბელიანების სასახლე")["kind"] == "palace"
    assert _lm(con, "თბილისი არენა")["kind"] == "stadium"
    mall = _lm(con, "გალერია")
    assert mall["kind"] == "mall" and mall["geom_wkt"].startswith("POLYGON")  # 1 ha: outline kept
    for ka in ("ეკლესია", "ფიტნესი", "A ბლოკი"):
        assert con.execute("SELECT COUNT(*) FROM feature WHERE ka = ?", (ka,)).fetchone()[0] == 0, ka
    # the park itself stays, the attraction of the same name is a duplicate
    parks = con.execute("SELECT kind FROM feature WHERE ka = 'ვაკის პარკი'").fetchall()
    assert [r["kind"] for r in parks] == ["park"] and st["dup_park"] == 1
    alt = {r["name"] for r in con.execute("SELECT name FROM feature_name WHERE feature_id = ?",
                                           (_lm(con, "სიონის ტაძარი")["id"],))}
    assert "სიონი" in alt
    assert s["tables"]["landmarks"] == 9


def test_parents_and_house_flags(db):
    con, s = db
    kostava = _lm(con, "კოსტავას ქუჩა")
    museum = _lm(con, "ხელოვნების მუზეუმი")
    assert museum["parent_id"] == kostava["id"]                       # by addr:street
    asat = con.execute("SELECT id FROM feature WHERE ka = 'ლადო ასათიანის ქუჩა'").fetchall()
    assert _lm(con, "ლადო ასათიანი")["parent_id"] in {r["id"] for r in asat}  # nearest street <= 50 m
    assert _lm(con, "გალერია")["parent_id"] is None                     # no street within 50 m
    assert s["landmarks"]["parents"]["parent_addr"] == 1
    h = con.execute("SELECT * FROM house WHERE osm_type = 'w' AND osm_id = 602").fetchall()
    assert len(h) == 1 and h[0]["feature_id"] == kostava["id"] and h[0]["raw"] == "10"
    assert "landmark:museum" in (h[0]["flags"] or "")
    # landmarks are never street candidates of the houses cascade
    assert con.execute("SELECT COUNT(*) FROM house h JOIN feature f ON f.id = h.feature_id WHERE f.kind IN "
                       f"({','.join('?' * len(LANDMARK_KINDS))})", sorted(LANDMARK_KINDS)).fetchone()[0] == 0
    # feature_area like any point
    assert con.execute("SELECT COUNT(*) FROM feature_area WHERE feature_id = ?", (museum["id"],)).fetchone()[0] >= 1


def test_landmarks_stage_alone_is_idempotent(db, tmp_path):
    con, _ = db
    before = [tuple(r) for r in con.execute("SELECT kind, ka, parent_id, osm_ids FROM feature ORDER BY ka, kind")]
    path = Path(con.execute("PRAGMA database_list").fetchone()[2])
    run(build_parser().parse_args(["--source", "overpass", "--overpass-dir", str(FIX), "--db", str(path),
                                   "--stages", "landmarks"]))
    after = [tuple(r) for r in con.execute("SELECT kind, ka, parent_id, osm_ids FROM feature ORDER BY ka, kind")]
    assert after == before
