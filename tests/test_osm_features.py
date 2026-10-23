"""Features stage on the synthetic fixture: merging, homonyms, kinds, POIs, families, parents."""
from pathlib import Path

import pytest

from pipeline.db import connect, migrate
from pipeline.osm.extract import read_overpass
from pipeline.osm.features import build_features, merge_components, write_features
from pipeline.osm.geo import unproj
from pipeline.osm.model import OsmWay

FIX = Path(__file__).parent / "fixtures" / "osm" / "overpass"


def _way(wid, pts, name="X ქუჩა"):
    nodes = [hash((round(x, 3), round(y, 3))) & 0xFFFFFFF for x, y in pts]
    return OsmWay(wid, {"highway": "residential", "name": name}, nodes, [unproj(x, y) for x, y in pts])


@pytest.fixture(scope="module")
def built():
    ex = read_overpass(FIX)
    feats, res = build_features(ex)
    return ex, feats, res


def by_ka(feats, ka):
    return [f for f in feats if f.ka == ka]


def test_merge_components_node_gap_and_far():
    ways = [
        _way(1, [(0, 0), (100, 0)]),
        _way(2, [(100, 0), (200, 0)]),        # shares node (100, 0)
        _way(3, [(450, 0), (600, 0)]),        # 250 m gap -> merged
        _way(4, [(0, 2000), (100, 2000)]),    # far -> own component
    ]
    comps = merge_components(ways, gap=300)
    assert [sorted(w.id for w in c) for c in comps] == [[1, 2, 3], [4]]
    comps = merge_components(ways, gap=200)
    assert [sorted(w.id for w in c) for c in comps] == [[1, 2], [3], [4]]


def test_streets_homonyms_and_excluded_highways(built):
    _ex, feats, res = built
    st = res["stats"]["streets"]
    assert st["skipped_highway"] == {"proposed": 1}
    asat = by_ka(feats, "ლადო ასათიანის ქუჩა")
    assert len(asat) == 2
    main, far = asat
    assert main.osm_ids == ["w1", "w2", "w3"] and far.osm_ids == ["w4"]
    assert main.homonym_group is not None and main.homonym_group == far.homonym_group
    assert main.len_m == pytest.approx(1450, abs=1)
    assert main.kind == "street" and main.highway == "residential"
    assert main.wikidata == "Q4380925"
    assert ("ka", "ძველი ასათიანის ქუჩა", "old", "osm:old_name") in main.names
    assert by_ka(feats, "კოსტავას ქუჩა")[0].homonym_group is None


def test_kinds_ru_and_ordinals(built):
    _ex, feats, _res = built
    lane = by_ka(feats, "ლადო ასათიანის II შესახვევი")[0]
    assert lane.kind == "lane" and lane.parsed.ord == 2
    assert lane.ru == "2-й переулок Ладо Асатиани" and lane.ru_generated == 0
    assert not any(n[0] == "ru" for n in lane.names)  # "2-ой ..." is the same name after normalization
    assert by_ka(feats, "კოსტავას I ჩიხი")[0].kind == "dead_end"
    assert by_ka(feats, "მშრალი ხიდი")[0].kind == "bridge"
    tia = by_ka(feats, "თიანეთის ქუჩა")[0]
    assert tia.ru_generated == 1 and tia.ru and tia.ru[0].isupper()


def test_pois_dedupe_and_kinds(built):
    _ex, feats, res = built
    sq = by_ka(feats, "თავისუფლების მოედანი")
    assert len(sq) == 1 and sq[0].kind == "square" and sq[0].osm_ids == ["w15", "w303"]
    metro = by_ka(feats, "რუსთაველი")
    assert len(metro) == 1 and metro[0].kind == "metro" and metro[0].osm_ids == ["n201", "n202"]
    assert by_ka(feats, "რიგის სკვერი")[0].kind == "garden"     # leisure=park, but the name says სკვერი
    assert by_ka(feats, "ვაკის პარკი")[0].kind == "park"
    assert by_ka(feats, "კუს ტბა")[0].kind == "lake"
    river = by_ka(feats, "ვერე")[0]
    assert river.kind == "river" and river.osm_ids == ["w101", "w102"] and river.len_m == pytest.approx(1000, abs=1)
    assert res["stats"]["pois"]["attached_square"] == 1


def test_families_and_parents(built):
    _ex, feats, res = built
    idx = {id(f): i for i, f in enumerate(feats)}
    asat_main = by_ka(feats, "ლადო ასათიანის ქუჩა")[0]
    lane = by_ka(feats, "ლადო ასათიანის II შესახვევი")[0]
    assert lane.family_key == asat_main.family_key
    assert lane.parent == idx[id(asat_main)] and lane.parent_how == "node"
    dead = by_ka(feats, "კოსტავას I ჩიხი")[0]
    kost = by_ka(feats, "კოსტავას ქუჩა")[0]
    assert dead.parent == idx[id(kost)] and dead.parent_how == "near"
    # syncope: გორგასალის / გორგასლის are one family
    assert by_ka(feats, "გორგასალის ქუჩა")[0].family_key == by_ka(feats, "გორგასლის ქუჩა")[0].family_key
    fam = res["families"][asat_main.family_key]
    assert fam["base_ka"] == "ლადო ასათიანის" and fam["base_ru"] == "Ладо Асатиани"
    assert fam["base_en"] == "Lado Asatiani" and fam["eponym_nom_ka"] == "ლადო ასათიანი"
    assert fam["kind_hint"] is None


def test_write_features_preserves_enrichment_by_family_key(built, tmp_path):
    _ex, feats, res = built
    con = connect(tmp_path / "f.sqlite")
    migrate(con)
    out = write_features(con, feats, res["families"])
    assert out["features"] == len(feats)
    key = by_ka(feats, "ლადო ასათიანის ქუჩა")[0].family_key
    con.execute("INSERT INTO eponym(id, kind, source) VALUES (77, 'person', 'manual')")
    con.execute("UPDATE family SET eponym_id = 77, kind_hint = 'person:full_name' WHERE key = ?", (key,))
    con.commit()
    out2 = write_features(con, feats, res["families"])
    assert out2["families_kept_eponym"] == 1
    row = con.execute("SELECT eponym_id, kind_hint FROM family WHERE key = ?", (key,)).fetchone()
    assert tuple(row) == (77, "person:full_name")
    # parents and families are written as ids
    lane = con.execute("SELECT parent_id, family_id FROM feature WHERE ka = 'ლადო ასათიანის II შესახვევი'").fetchone()
    par = con.execute("SELECT ka, family_id FROM feature WHERE id = ?", (lane["parent_id"],)).fetchone()
    assert par["ka"] == "ლადო ასათიანის ქუჩა" and par["family_id"] == lane["family_id"]
    con.close()
