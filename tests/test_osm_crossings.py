"""Crossings stage: shared nodes, geometric fallback, bridges / layers, direction masks, near pairs."""
from pathlib import Path

import pytest
from shapely.geometry import LineString

from pipeline.db import connect
from pipeline.osm.crossings import WayInfo, compute, eff_layer
from pipeline.osm.geo import unproj
from pipeline.osm.model import OsmWay
from pipeline.osm.run import build_parser, run

FIX = Path(__file__).parent / "fixtures" / "osm" / "overpass"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("osm_x") / "syn.sqlite"
    run(build_parser().parse_args(["--source", "overpass", "--overpass-dir", str(FIX), "--db", str(path)]))
    con = connect(path)
    yield con
    con.close()


def crossings(db):
    q = ("SELECT fa.ka AS a, fb.ka AS b, c.kind, c.dir_a, c.dir_b FROM crossing c "
         "JOIN feature fa ON fa.id = c.a JOIN feature fb ON fb.id = c.b")
    out = {}
    for r in db.execute(q):
        out[frozenset((r["a"], r["b"]))] = (r["kind"], {r["a"]: r["dir_a"], r["b"]: r["dir_b"]})
    return out


def test_eff_layer():
    assert eff_layer({}) == (0, False, False)
    assert eff_layer({"bridge": "yes"}) == (1, True, False)
    assert eff_layer({"tunnel": "yes"}) == (-1, False, True)
    assert eff_layer({"bridge": "viaduct", "layer": "2"}) == (2, True, False)
    assert eff_layer({"layer": "x"}) == (0, False, False)
    assert eff_layer({"tunnel": "building_passage"}) == (0, False, False)


def test_synthetic_crossings_and_masks(db):
    x = crossings(db)
    N, E, S, W = 1, 4, 16, 64
    asat, kost = "ლადო ასათიანის ქუჩა", "კოსტავას ქუჩა"
    kind, d = x[frozenset((asat, kost))]
    assert kind == "node" and d[kost] == N | S and d[asat] == E | W
    kind, d = x[frozenset((asat, "ლადო ასათიანის II შესახვევი"))]
    assert kind == "node" and d["ლადო ასათიანის II შესახვევი"] == N
    kind, d = x[frozenset((kost, "ბესიკის ქუჩა"))]          # end 5 m from the street, no shared node
    assert kind == "geom" and d["ბესიკის ქუჩა"] == E and d[kost] == N | S
    assert frozenset((kost, "მშრალი ხიდი")) not in x            # bridge over the street: grade separated
    assert frozenset((kost, "გვირაბის ქუჩა")) not in x          # tunnel end 3 m away (> 2 m limit)
    assert frozenset((kost, "ჭავჭავაძის ქუჩა")) not in x        # 15 m: only "near"
    assert len(x) == 3


def test_synthetic_near_pairs(db):
    rows = db.execute("SELECT fa.ka, fb.ka, n.dist_m FROM near_pair n JOIN feature fa ON fa.id = n.a "
                      "JOIN feature fb ON fb.id = n.b").fetchall()
    assert [(frozenset(r[:2]), r[2]) for r in rows] == [(frozenset(("კოსტავას ქუჩა", "ჭავჭავაძის ქუჩა")), 15.0)]


def _wi(wid, fid, pts, tags=None, nodes=None):
    tags = tags or {}
    layer, br, tu = eff_layer(tags)
    nodes = nodes or [wid * 100 + i for i in range(len(pts))]
    w = OsmWay(wid, {"highway": "residential", "name": str(fid), **tags}, nodes, [unproj(x, y) for x, y in pts])
    return WayInfo(w, fid, LineString(pts), layer, br or tu)


def test_bridge_touch_limit_and_layers():
    street = _wi(1, 1, [(0, -100), (0, 100)])
    lines = {1: [street.xy]}
    near_bridge = _wi(2, 2, [(1.5, 0), (100, 0)], {"bridge": "yes", "layer": "0"})
    far_bridge = _wi(3, 3, [(3.0, 50), (100, 50)], {"bridge": "yes", "layer": "0"})
    other_layer = _wi(4, 4, [(1.0, -50), (100, -50)], {"layer": "1"})
    for w in (near_bridge, far_bridge, other_layer):
        lines[w.fid] = [w.xy]
    rows, near, st = compute([street, near_bridge, far_bridge, other_layer], lines)
    assert {(r[0], r[1]) for r in rows} == {(1, 2)}
    assert rows[0][4] == "geom"
    assert near == []  # bridges / other layers never give near pairs


def test_shared_nodes_clustered_per_location():
    # two streets sharing two nodes 200 m apart (a loop) -> two crossings; 20 m apart -> one
    a = _wi(1, 1, [(0, 0), (200, 0)], nodes=[1, 2])
    b = _wi(2, 2, [(0, 0), (100, 80), (200, 0)], nodes=[1, 3, 2])
    c = _wi(3, 3, [(500, 0), (520, 0)], nodes=[10, 11])
    d = _wi(4, 4, [(500, 0), (510, 30), (520, 0)], nodes=[10, 12, 11])
    lines = {w.fid: [w.xy] for w in (a, b, c, d)}
    rows, _near, st = compute([a, b, c, d], lines)
    per_pair = {}
    for r in rows:
        per_pair[(r[0], r[1])] = per_pair.get((r[0], r[1]), 0) + 1
    assert per_pair == {(1, 2): 2, (3, 4): 1}
    assert st["node_pairs"] == 2 and st["crossing_rows"] == 3
