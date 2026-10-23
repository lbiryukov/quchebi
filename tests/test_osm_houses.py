"""Houses stage: number parsing rows and the street matching cascade."""
from pathlib import Path

import pytest

from pipeline.db import connect
from pipeline.osm.houses import kinds_compatible, name_key
from pipeline.osm.run import build_parser, run

FIX = Path(__file__).parent / "fixtures" / "osm" / "overpass"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("osm_h") / "syn.sqlite"
    run(build_parser().parse_args(["--source", "overpass", "--overpass-dir", str(FIX), "--db", str(path)]))
    con = connect(path)
    yield con
    con.close()


def test_name_key_across_scripts():
    ka = name_key("ლადო ასათიანის ქუჩა")
    assert ka.kind == "street" and ka.ord is None
    assert name_key("Lado Asatiani Street").core == ka.core
    assert name_key("улица Ладо Асатиани").core == ka.core
    assert name_key("ᲚᲐᲓᲝ ᲐᲡᲐᲗᲘᲐᲜᲘᲡ ᲥᲣᲩᲐ").norm == ka.norm
    assert name_key("ლებანიძეს ქუჩა").core == name_key("ლებანიძის ქუჩა").core
    assert name_key("ლადო ასათიანის II შესახვევი").ord == 2
    assert name_key("Lado Asatiani 2nd Lane").ord == 2
    assert name_key("2-й переулок Ладо Асатиани").ord == 2
    assert name_key("Tsinanauri Str., 4th Lane").ord == 4
    assert name_key("") is None


def test_kinds_compatible():
    assert kinds_compatible("street", "street", strict=True)
    assert kinds_compatible("other", "lane", strict=True)
    assert not kinds_compatible("street", "avenue", strict=True)
    assert kinds_compatible("street", "avenue", strict=False)
    assert not kinds_compatible("street", "lane", strict=False)


def houses(db):
    q = ("SELECT h.osm_id, h.raw, h.n, h.lit, h.kind, h.match, h.flags, f.ka, f.id AS fid, a.ka AS area "
         "FROM house h LEFT JOIN feature f ON f.id = h.feature_id LEFT JOIN area a ON a.id = h.area_id "
         "ORDER BY h.osm_id, h.n")
    return db.execute(q).fetchall()


def test_cascade(db):
    rows = {r["osm_id"]: r for r in houses(db)}
    main_id = db.execute("SELECT MIN(id) FROM feature WHERE ka = 'ლადო ასათიანის ქუჩა'").fetchone()[0]
    far_id = db.execute("SELECT MAX(id) FROM feature WHERE ka = 'ლადო ასათიანის ქუჩა'").fetchone()[0]
    expect = {
        501: ("exact", "ლადო ასათიანის ქუჩა"),
        502: ("exact", "ლადო ასათიანის ქუჩა"),
        503: ("variant", "ლადო ასათიანის ქუჩა"),        # name:en
        504: ("normalized", "ლადო ასათიანის ქუჩა"),     # "ქ." abbreviation
        505: ("normalized", "ლადო ასათიანის ქუჩა"),     # Mtavruli
        506: ("no_type", "ლადო ასათიანის ქუჩა"),
        507: ("fuzzy", "ლადო ასათიანის ქუჩა"),          # typo "ასათინის"
        508: ("homonym_swap", "გორგასლის ქუჩა"),        # far exact name, similar street next door
        509: ("none", None),                           # only street of that name is 5 km away
        510: ("place", None),
        511: ("place", None),
        514: ("exact", "კოსტავას ქუჩა"),               # street written into addr:place
        515: ("none", None),
        516: ("exact", "ლადო ასათიანის II შესახვევი"),
    }
    for oid, (how, ka) in expect.items():
        assert (rows[oid]["match"], rows[oid]["ka"]) == (how, ka), oid
    assert rows[501]["fid"] == main_id and rows[502]["fid"] == far_id   # nearest homonym component
    assert rows[509]["flags"].startswith("far_rejected:")
    assert rows[510]["area"] == "სოლოლაკი" and rows[510]["flags"] == "place_by_name"
    assert rows[511]["area"] == "სოლოლაკი" and rows[511]["flags"] == "place_by_location"
    assert rows[514]["flags"] == "place_is_street"


def test_house_number_rows(db):
    rows = houses(db)
    junk = [r for r in rows if r["osm_id"] == 512]
    assert len(junk) == 1 and junk[0]["kind"] == "junk" and junk[0]["n"] is None and junk[0]["raw"] == "office 208"
    lst = [r for r in rows if r["osm_id"] == 513]
    assert [(r["n"], r["kind"]) for r in lst] == [(27, "list"), (29, "list")]
    let = [r for r in rows if r["osm_id"] == 503][0]
    assert (let["n"], let["lit"], let["kind"]) == (7, "ა", "letter")
    plain = [r for r in rows if r["osm_id"] == 501][0]
    assert plain["lit"] is None and plain["kind"] == "int"


def test_addr_variants(db):
    v = {r[0] for r in db.execute("SELECT name FROM feature_name WHERE kind = 'addr_variant' AND source = 'addr'")}
    assert "ლადო ასათინის ქუჩა" in v and "ლადო ასათიანის" in v
    assert "ᲚᲐᲓᲝ ᲐᲡᲐᲗᲘᲐᲜᲘᲡ ᲥᲣᲩᲐ" not in v and "ლადო ასათიანის ქ." not in v   # same skeleton as ka
