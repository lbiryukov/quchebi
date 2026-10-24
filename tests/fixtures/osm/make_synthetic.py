"""Generate the synthetic Overpass-format fixture used by tests/test_osm_*.py.

    uv run python tests/fixtures/osm/make_synthetic.py

Writes ``tests/fixtures/osm/overpass/{highways,addresses,misc,place_polygons}/synthetic.json``.
Coordinates are designed in local metres (x east, y north) around the projection origin and
converted with :func:`pipeline.osm.geo.unproj`. Layout (metres):

* districts: ``მთაწმინდის რაიონი`` x in [-2000, 0], ``ვაკის რაიონი`` x in [0, 2000], y in [-2000, 2000];
* ``ლადო ასათიანის ქუჩა``: y=0, x -600..600 (two ways sharing node x=0) + x 650..900 (50 m gap,
  merged) + a far homonym at y=900, x -900..-700;
* ``ლადო ასათიანის II შესახვევი``: x=100, y 0..200 (shares node (100, 0));
* ``კოსტავას ქუჩა``: x=300, y -500..500 (shares node (300, 0) with Asatiani);
* ``მშრალი ხიდი``: bridge=yes layer=1, y=300, x 200..400 (crosses Kostava, no node);
* ``ბესიკის ქუჩა``: y=-300, x 305..500 (end 5 m from Kostava -> geom crossing);
* ``ჭავჭავაძის ქუჩა``: y=-400, x 315..500 (end 15 m from Kostava -> near pair);
* ``გვირაბის ქუჩა``: tunnel=yes layer=0, y=-450, x 303..500 (3 m > 2 m bridge/tunnel limit);
* ``კოსტავას I ჩიხი``: y=-200, x 400..500 (100 m from Kostava -> parent 'near');
* ``გორგასლის ქუჩა`` near (-300, 600); ``გორგასალის ქუჩა`` far at (900, -900) (homonym swap);
* ``თიანეთის ქუჩა`` at x 1800..1900, y=1800 (far reject for an address at (-1800, -1800));
* ``თავისუფლების მოედანი`` pedestrian way + place=square polygon (attached);
* river ``ვერე`` (2 ways), metro ``რუსთაველი`` (2 nodes 100 m apart), parks, places.
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.osm.geo import unproj

OUT = Path(__file__).resolve().parent / "overpass"

_nodes: dict[tuple[float, float], int] = {}
_next = [1000]


def nid(x: float, y: float) -> int:
    k = (round(x, 3), round(y, 3))
    if k not in _nodes:
        _next[0] += 1
        _nodes[k] = _next[0]
    return _nodes[k]


def ll(x: float, y: float) -> dict:
    lat, lon = unproj(x, y)
    return {"lat": round(lat, 7), "lon": round(lon, 7)}


def way(wid: int, pts: list[tuple[float, float]], tags: dict) -> dict:
    return {"type": "way", "id": wid, "nodes": [nid(x, y) for x, y in pts], "geometry": [ll(x, y) for x, y in pts],
            "tags": tags}


def line(x0, y0, x1, y1, step=100.0) -> list[tuple[float, float]]:
    import math

    n = max(1, int(math.ceil(math.hypot(x1 - x0, y1 - y0) / step)))
    return [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n + 1)]


def rect(x0, y0, x1, y1) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def rel(rid: int, rings: list[list[tuple[float, float]]], tags: dict, inner=()) -> dict:
    members = [{"type": "way", "ref": rid * 10 + i, "role": "outer", "geometry": [ll(x, y) for x, y in r]}
               for i, r in enumerate(rings)]
    members += [{"type": "way", "ref": rid * 10 + 5 + i, "role": "inner", "geometry": [ll(x, y) for x, y in r]}
                for i, r in enumerate(inner)]
    return {"type": "relation", "id": rid, "members": members, "tags": tags}


def node(id_: int, x: float, y: float, tags: dict) -> dict:
    return {"type": "node", "id": id_, **ll(x, y), "tags": tags}


def center(type_: str, id_: int, x: float, y: float, tags: dict) -> dict:
    return {"type": type_, "id": id_, "center": ll(x, y), "tags": tags}


def street(name, ru=None, en=None, **extra):
    t = {"highway": extra.pop("highway", "residential"), "name": name}
    if ru:
        t["name:ru"] = ru
    if en:
        t["name:en"] = en
    t.update(extra)
    return t


def build() -> dict[str, list[dict]]:
    asat = street("ლადო ასათიანის ქუჩა", "улица Ладо Асатиани", "Lado Asatiani Street",
                  **{"name:etymology:wikidata": "Q4380925", "old_name": "ძველი ასათიანის ქუჩა"})
    hw = [
        way(1, line(-600, 0, 0, 0), asat),
        way(2, line(0, 0, 600, 0), asat),
        way(3, line(650, 0, 900, 0), asat),
        way(4, line(-900, 900, -700, 900), asat),
        way(5, line(100, 0, 100, 200), street("ლადო ასათიანის II შესახვევი", "2-ой переулок Ладо Асатиани")),
        way(6, line(300, -500, 300, 500), street("კოსტავას ქუჩა", "улица Костава", highway="secondary")),
        way(7, [(200, 300), (400, 300)], street("მშრალი ხიდი", "Сухой мост", bridge="yes", layer="1")),
        way(8, line(305, -300, 500, -300), street("ბესიკის ქუჩა", "улица Бесики")),
        way(9, line(315, -400, 500, -400), street("ჭავჭავაძის ქუჩა", "улица Чавчавадзе")),
        way(10, line(303, -450, 500, -450), street("გვირაბის ქუჩა", tunnel="yes", layer="0")),
        way(11, line(400, -200, 500, -200), street("კოსტავას I ჩიხი")),
        way(12, line(-400, 600, -200, 600), street("გორგასლის ქუჩა", "улица Горгасали")),
        way(13, line(800, -900, 1000, -900), street("გორგასალის ქუჩა", "улица Горгасали")),
        way(14, line(1800, 1800, 1900, 1800), street("თიანეთის ქუჩა")),
        way(15, line(-500, -600, -300, -600), street("თავისუფლების მოედანი", highway="pedestrian")),
        way(16, line(-1500, -1500, -1400, -1500), street("ძველი გზა", highway="proposed")),
    ]
    misc = [
        way(101, line(-1000, -1000, -1000, -500), {"waterway": "river", "name": "ვერე", "name:ru": "Вере"}),
        way(102, line(-1000, -500, -1000, 0), {"waterway": "river", "name": "ვერე", "name:ru": "Вере"}),
        node(201, -100, -100, {"railway": "station", "station": "subway", "name": "რუსთაველი", "name:ru": "Руставели"}),
        node(202, -100, -200, {"railway": "station", "station": "subway", "name": "რუსთაველი"}),
        way(301, rect(500, 500, 700, 700), {"leisure": "park", "name": "ვაკის პარკი", "name:ru": "Ваке парк"}),
        way(302, rect(-700, 300, -650, 350), {"leisure": "park", "name": "რიგის სკვერი"}),
        way(303, rect(-520, -620, -280, -580), {"place": "square", "name": "თავისუფლების მოედანი"}),
        way(304, rect(1200, 1200, 1400, 1300), {"natural": "water", "name": "კუს ტბა"}),
        rel(1, [rect(-2000, -2000, 0, 2000)], {"boundary": "administrative", "admin_level": "10",
                                                "name": "მთაწმინდის რაიონი", "name:ru": "Мтацминда",
                                                "name:en": "Mtatsminda District"}),
        rel(2, [rect(0, -2000, 2000, 2000)], {"boundary": "administrative", "admin_level": "10",
                                               "name": "ვაკის რაიონი", "name:en": "Vake District"}),
        node(401, 500, -500, {"place": "neighbourhood", "name": "აბანოთუბანი", "name:ru": "Абанотубани"}),
        node(402, -500, 100, {"place": "neighbourhood", "name": "სოლოლაკი"}),  # duplicate of the polygon
        node(403, 1500, -1500, {"place": "suburb", "name": "ვაკის რაიონი"}),   # admin duplicate
    ]
    polys = [
        rel(11, [rect(-800, -200, -200, 400)], {"place": "neighbourhood", "name": "სოლოლაკი", "name:ru": "Сололаки"}),
        rel(12, [rect(-900, -500, -100, 800)], {"place": "quarter", "name": "მთაწმინდა, სოლოლაკი"}),
        way(113, rect(-1990, -1990, -10, 1990), {"place": "suburb", "name": "მთაწმინდის რაიონი"}),  # admin dup
    ]
    addr = [
        center("way", 501, 50, 20, {"addr:housenumber": "3", "addr:street": "ლადო ასათიანის ქუჩა"}),
        center("way", 502, -800, 920, {"addr:housenumber": "5", "addr:street": "ლადო ასათიანის ქუჩა"}),
        center("way", 503, 200, -20, {"addr:housenumber": "7ა", "addr:street": "Lado Asatiani Street"}),
        center("way", 504, 400, 15, {"addr:housenumber": "9", "addr:street": "ლადო ასათიანის ქ."}),
        center("way", 505, 450, 15, {"addr:housenumber": "11", "addr:street": "ᲚᲐᲓᲝ ᲐᲡᲐᲗᲘᲐᲜᲘᲡ ᲥᲣᲩᲐ"}),
        center("way", 506, 500, 15, {"addr:housenumber": "13", "addr:street": "ლადო ასათიანის"}),
        center("way", 507, 550, 15, {"addr:housenumber": "15", "addr:street": "ლადო ასათინის ქუჩა"}),
        center("way", 508, -300, 620, {"addr:housenumber": "2", "addr:street": "გორგასალის ქუჩა"}),
        center("way", 509, -1800, -1800, {"addr:housenumber": "27", "addr:street": "თიანეთის ქუჩა"}),
        center("way", 510, -500, 100, {"addr:housenumber": "4", "addr:place": "სოლოლაკი"}),
        center("way", 511, -400, 0, {"addr:housenumber": "6", "addr:place": "გლდანის მე-7 მიკრო რაიონი"}),
        center("way", 512, 320, 100, {"addr:housenumber": "office 208", "addr:street": "კოსტავას ქუჩა"}),
        center("way", 513, 320, -100, {"addr:housenumber": "27;29", "addr:street": "კოსტავას ქუჩა"}),
        center("way", 514, 320, 200, {"addr:housenumber": "1", "addr:place": "ლილო. კოსტავას ქუჩა"}),
        node(515, 0, -1900, {"addr:housenumber": "1"}),
        center("relation", 516, 120, 150, {"addr:housenumber": "2", "addr:street": "ლადო ასათიანის II შესახვევი"}),
    ]
    # landmarks (pipeline/osm/landmarks.py); built last so that the node ids above do not move
    museum_bld = {"building": "yes", "tourism": "museum", "name": "ხელოვნების მუზეუმი", "name:en": "Art Museum",
                  "addr:street": "კოსტავას ქუჩა", "addr:housenumber": "10"}
    landmarks = [
        node(601, 318, 42, {"tourism": "museum", "name": "ხელოვნების მუზეუმი", "name:ru": "Музей искусств"}),
        way(602, rect(305, 30, 330, 55), museum_bld),                       # same museum as a building
        node(603, -300, 200, {"amenity": "place_of_worship", "religion": "christian", "name": "სიონის ტაძარი",
                              "name:ru": "Сионский собор", "alt_name": "სიონი"}),
        node(604, -350, 250, {"amenity": "place_of_worship", "religion": "muslim", "name": "ჯუმა მეჩეთი",
                              "name:ru": "Джума-мечеть"}),
        node(605, -400, 300, {"amenity": "place_of_worship", "religion": "jewish", "name": "დიდი სინაგოგა"}),
        node(606, 50, 30, {"tourism": "artwork", "artwork_type": "sculpture", "name": "ლადო ასათიანი",
                           "name:ru": "Ладо Асатиани"}),                     # 30 m from Asatiani street
        node(607, 60, -25, {"historic": "memorial", "memorial": "plaque", "name": "ილია ჭავჭავაძე"}),
        way(608, rect(800, 200, 900, 300), {"shop": "mall", "name": "გალერია", "name:en": "Galleria"}),
        node(609, 200, 100, {"amenity": "place_of_worship", "name": "ეკლესია"}),   # a type word, not a name
        node(610, -600, -100, {"tourism": "attraction", "name": "ორბელიანების სასახლე"}),  # -> palace
        way(611, rect(520, 520, 680, 680), {"tourism": "attraction", "name": "ვაკის პარკი"}),  # duplicate of the park
        node(612, 700, -700, {"leisure": "sports_centre", "name": "ფიტნესი"}),      # small sports centre: skipped
        node(613, 720, -720, {"leisure": "sports_centre", "name": "თბილისი არენა"}),  # stadium by its name
        way(614, rect(-1500, 1000, -1480, 1020), {"building": "apartments", "name": "A ბლოკი"}),  # part label
    ]
    addr.append(center("way", 602, 318, 42, museum_bld))
    return {"highways": hw, "misc": misc + landmarks, "place_polygons": polys, "addresses": addr}


def main() -> None:
    data = build()
    for folder, els in data.items():
        d = OUT / folder
        d.mkdir(parents=True, exist_ok=True)
        payload = {"version": 0.6, "generator": "synthetic", "osm3s": {"timestamp_osm_base": "2026-10-20T00:00:00Z"},
                   "elements": els}
        (d / "synthetic.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(folder, len(els))


if __name__ == "__main__":
    main()
