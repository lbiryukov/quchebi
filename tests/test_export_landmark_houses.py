"""houses.json rows of landmark addresses: the 9th element is the place's feature index."""
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from pipeline.db import open_db
from pipeline.export.build import dumps, export_pack
from pipeline.validate.pack import validate_pack

FIX = Path(__file__).parent / "fixtures" / "export"
NOW = dt.datetime(2026, 10, 24, 12, 0)

# A museum on Galaktion Tabidze Street (feature 101 of the fixture): its building carries "7",
# a second object "9 korpus 2"; a plaque without an address; a flagged row whose object is unknown.
LANDMARK_SQL = """
INSERT INTO feature(id, kind, ka, ru, en, parent_id, len_m, lat, lon, geom_wkt, osm_ids) VALUES
 (900, 'museum', 'თბილისის ისტორიის მუზეუმი', 'Музей истории Тбилиси', 'Tbilisi History Museum', 101, 0,
  41.69506, 44.7920, 'POINT(44.7920 41.69506)', '["w900","n901"]'),
 (901, 'plaque', 'გალაკტიონ ტაბიძე', 'Галактион Табидзе', 'Galaktion Tabidze', 101, 0,
  41.69507, 44.7930, 'POINT(44.7930 41.69507)', '["n902"]');
INSERT INTO house(id, feature_id, area_id, raw, n, lit, sub, n_to, corp, kind, lat, lon, addr_street, match,
                  flags, osm_type, osm_id) VALUES
 (900, 101, NULL, '7', 7, NULL, NULL, NULL, NULL, 'int', 41.69506, 44.7920, 'გალაკტიონ ტაბიძის ქუჩა', 'exact',
  'landmark:museum', 'w', 900),
 (901, 101, NULL, '9 კორპ. 2', 9, NULL, NULL, NULL, '2', 'corp', 41.69506, 44.7921, 'გალაკტიონ ტაბიძის ქუჩა',
  'exact', 'far:120,landmark:museum', 'n', 901),
 (902, 101, NULL, '11', 11, NULL, NULL, NULL, NULL, 'int', 41.69506, 44.7922, 'გალაკტიონ ტაბიძის ქუჩა', 'exact',
  'landmark:theatre', 'w', 12345),
 (903, 101, NULL, '13', 13, NULL, NULL, NULL, NULL, 'int', 41.69506, 44.7923, 'გალაკტიონ ტაბიძის ქუჩა', 'exact',
  NULL, 'n', 902);
"""


def make_db():
    con = open_db(":memory:")
    con.executescript((FIX / "fixture.sql").read_text(encoding="utf-8"))
    con.executescript(LANDMARK_SQL)
    con.commit()
    return con


@pytest.fixture()
def exported(tmp_path):
    con = make_db()
    out = tmp_path / "data"
    res = export_pack(con, out, tiles_manifest=None, now=NOW, taxonomy_paths=(FIX / "taxonomy.json",),
                      stats_path=tmp_path / "build" / "export_stats.json")
    con.close()
    files = {n: json.loads((out / f"{n}.json").read_text(encoding="utf-8")) for n in ("core", "houses")}
    return out, files["core"], files["houses"], res


def fidx(core, ka):
    hits = [i for i, f in enumerate(core["f"]) if f["ka"] == ka]
    assert len(hits) == 1, (ka, hits)
    return hits[0]


def test_landmark_row_has_place_index(exported):
    _, core, houses, res = exported
    street = fidx(core, "გალაკტიონ ტაბიძის ქუჩა")
    museum = fidx(core, "თბილისის ისტორიის მუზეუმი")
    assert core["kinds"][core["f"][museum]["k"]]["code"] == "museum"
    assert core["f"][museum]["par"] == street
    rows = {r[0]: r for r in houses["h"][str(street)]}
    # no range, no korpus: both padded with null before the 9th element
    assert rows["7"] == ["7", 7, "", "", 4169506, 4479200, None, None, museum]
    # korpus kept as 8th, null range
    assert rows["9 კორპ. 2"] == ["9 კორპ. 2", 9, "", "", 4169506, 4479210, None, "2", museum]
    # a flagged row whose OSM object is not a landmark feature: no 9th element, counted as a problem
    assert rows["11"] == ["11", 11, "", "", 4169506, 4479220]
    # an unflagged row keeps the plain shape even if its object is a landmark
    assert rows["13"] == ["13", 13, "", "", 4169506, 4479230]
    # older rows are unchanged
    assert rows["1"] == ["1", 1, "", "", 4169505, 4479050]
    assert core["f"][street]["hn"] == len(houses["h"][str(street)])
    stats = res["stats"]
    assert stats["houses_landmark"] == 2
    assert stats["problems"]["house_landmark_unresolved"] == 1


def test_landmark_rows_validate(exported):
    out = exported[0]
    res = validate_pack(out)
    assert not res.errors, res.errors
    assert res.stats["houses_landmark"] == 2


def test_validator_rejects_a_9th_element_that_is_not_a_place(exported):
    out, core, houses, _ = exported
    street = fidx(core, "გალაკტიონ ტაბიძის ქუჩა")
    rows = houses["h"][str(street)]
    k = next(j for j, r in enumerate(rows) if len(r) > 8)
    rows[k][8] = street                                    # a street, not a landmark
    data = dumps(houses)
    (out / "houses.json").write_bytes(data)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    m["files"]["houses.json"] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    (out / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    errs = " | ".join(validate_pack(out).errors)
    assert "9th element is not a landmark" in errs
    rows[k] = rows[k] + [1]                                 # 10 elements: schema error
    data = dumps(houses)
    (out / "houses.json").write_bytes(data)
    m["files"]["houses.json"] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    (out / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    assert any(e.startswith("schema houses.json") for e in validate_pack(out).errors)
