"""Geometry helpers of the OSM import: projection, sectors, direction masks, rings, name variants."""
import math

import pytest
from shapely.geometry import LineString, MultiLineString

from pipeline.osm.features import name_variants, script_lang
from pipeline.osm.geo import (bbox_str, direction_mask, mask_names, polygon_from_rings, proj, representative_point,
                              sector_of, to_ll, to_xy, unproj)


def test_proj_roundtrip_and_scale():
    lat, lon = 41.7151, 44.8271
    x, y = proj(lat, lon)
    lat2, lon2 = unproj(x, y)
    assert lat2 == pytest.approx(lat, abs=1e-9) and lon2 == pytest.approx(lon, abs=1e-9)
    # 0.001 deg of latitude is ~110.5 m, of longitude at 41.7N ~83 m
    assert proj(41.721, 44.79)[1] - proj(41.720, 44.79)[1] == pytest.approx(110.54, rel=1e-3)
    assert proj(41.72, 44.791)[0] - proj(41.72, 44.79)[0] == pytest.approx(83.1, rel=1e-2)


def test_shapely_transform_roundtrip():
    g = LineString([(44.80, 41.70), (44.81, 41.71)])
    back = to_ll(to_xy(g))
    assert back.equals_exact(g, 1e-9)
    assert to_xy(g).length == pytest.approx(math.hypot(0.01 * 83107, 0.01 * 110540), rel=2e-3)


@pytest.mark.parametrize("bearing,sector", [(0, 0), (22.4, 0), (22.6, 1), (45, 1), (90, 2), (135, 3), (180, 4),
                                            (225, 5), (270, 6), (315, 7), (337.4, 7), (337.6, 0), (359.9, 0)])
def test_sector_of(bearing, sector):
    assert sector_of(bearing) == sector


def test_direction_mask_through_street_and_end():
    through = LineString([(-100, 0), (100, 0)])
    assert mask_names(direction_mask([through], (0, 0))) == ["E", "W"]
    end = LineString([(0, 0), (0, 100)])
    assert mask_names(direction_mask([end], (0, 0))) == ["N"]
    diag = LineString([(0, 0), (50, 50)])
    assert mask_names(direction_mask([diag], (0, 0))) == ["NE"]
    # a line farther than `near` from the point is ignored
    assert direction_mask([LineString([(0, 20), (100, 20)])], (0, 0), near=5) == 0
    # a bend after 10 m: the chord to the point 30 m along the line decides
    bent = LineString([(0, 0), (10, 0), (10, 100)])
    assert mask_names(direction_mask([bent], (0, 0))) == ["NE"]
    # two parts meeting at the point (fork of one street)
    fork = [LineString([(0, 0), (0, -80)]), LineString([(0, 0), (-80, 0)])]
    assert mask_names(direction_mask(fork, (0, 0))) == ["S", "W"]


def test_polygon_from_rings_assembles_open_members_and_holes():
    outer = [[(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)], [(1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]]  # (lat, lon)
    inner = [[(0.4, 0.4), (0.4, 0.6), (0.6, 0.6), (0.6, 0.4), (0.4, 0.4)]]
    g, bad = polygon_from_rings(outer, inner)
    assert bad == 0
    assert g.area == pytest.approx(1.0 - 0.04)
    g2, bad2 = polygon_from_rings([[(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)]], [])
    assert g2 is None and bad2 == 1


def test_representative_point_and_bbox():
    g = MultiLineString([[(44.80, 41.70), (44.80, 41.71)], [(44.90, 41.70), (44.90, 41.70001)]])
    lat, lon = representative_point(g)
    assert lon == pytest.approx(44.80) and lat == pytest.approx(41.705, abs=1e-6)
    assert bbox_str(g) == "41.700000,44.800000,41.710000,44.900000"


def test_name_variants_languages_kinds_and_sources():
    tags = {
        "name": "ლადო ასათიანის ქუჩა", "name:ru": "улица Ладо Асатиани", "name:en": "Lado Asatiani Street",
        "old_name:ru:1923-1990": "улица Карла Маркса", "alt_name": "ასათიანის ქ.;Asatiani St",
        "official_name": "ლადო ასათიანის სახელობის ქუჩა", "name:de": "Lado-Assatiani-Straße",
        "name:uk": "вулиця Ладо Асатіані", "name:etymology:wikidata": "Q1", "name:ka-Latn": "Lado Asatianis kucha",
    }
    v = {(lang, nm, kind) for lang, nm, kind, _src in name_variants(tags)}
    assert ("ru", "улица Карла Маркса", "old") in v
    assert ("ka", "ასათიანის ქ.", "alt") in v and ("lat", "Asatiani St", "alt") in v
    assert ("ka", "ლადო ასათიანის სახელობის ქუჩა", "official") in v
    assert ("lat", "Lado-Assatiani-Straße", "alt") in v
    assert ("lat", "Lado Asatianis kucha", "alt") in v
    assert ("ru", "улица Ладо Асатиани", "alt") in v and ("en", "Lado Asatiani Street", "alt") in v
    assert not any("вулиця" in nm for _l, nm, _k in v)          # Cyrillic of another language
    assert not any(nm == "Q1" for _l, nm, _k in v)
    assert all(not (k == "alt" and nm == tags["name"]) for _l, nm, k in v)
    src = {nm: s for _l, nm, _k, s in name_variants(tags)}
    assert src["улица Карла Маркса"] == "osm:old_name:ru:1923-1990"
    assert script_lang("ქუჩა") == "ka" and script_lang("улица") == "ru" and script_lang("Street", "en") == "en"
