"""Pack export on the synthetic fixture (tests/fixtures/export/fixture.sql)."""
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from pipeline.db import open_db
from pipeline.export.__main__ import main as export_main
from pipeline.export.build import DATA_FILES, dumps, export_pack
from pipeline.export.kinds import FALLBACK_KIND_TABLE, load_kinds
from pipeline.export.polyline import decode_ints
from pipeline.export.taxonomy import derive_aliases, eponym_kinds, feminine, load_taxonomy, plural

FIX = Path(__file__).parent / "fixtures" / "export"
TAXONOMY = (FIX / "taxonomy.json",)
NOW = dt.datetime(2026, 10, 24, 12, 0)


def make_db(path=":memory:"):
    con = open_db(path)
    con.executescript((FIX / "fixture.sql").read_text(encoding="utf-8"))
    con.commit()
    return con


def do_export(con, out: Path, **kw):
    kw.setdefault("tiles_manifest", None)
    kw.setdefault("now", NOW)
    kw.setdefault("taxonomy_paths", TAXONOMY)
    kw.setdefault("stats_path", out.parent / "build" / "export_stats.json")
    return export_pack(con, out, **kw)


@pytest.fixture()
def pack(tmp_path):
    con = make_db()
    out = tmp_path / "data"
    res = do_export(con, out)
    files = {n[:-5]: json.loads((out / n).read_text(encoding="utf-8")) for n in DATA_FILES + ("manifest.json",)}
    files["_dir"] = out
    files["_res"] = res
    yield files
    con.close()


def fidx(core, ka, kind=None):
    hits = [i for i, f in enumerate(core["f"]) if f["ka"] == ka
            and (kind is None or core["kinds"][f["k"]]["code"] == kind)]
    assert len(hits) == 1, (ka, hits)
    return hits[0]


# ------------------------------------------------------------------ files and manifest
def test_manifest_and_hashes(pack):
    m, out = pack["manifest"], pack["_dir"]
    assert m["v"] == 1 and m["version"] == "20261024-1" and m["built"] == "24/10/2026"
    assert m["osm"] == "2026-10-23T19:42:11Z"
    assert set(m["files"]) == set(DATA_FILES)
    for name, meta in m["files"].items():
        data = (out / name).read_bytes()
        assert meta["bytes"] == len(data)
        assert meta["sha256"] == hashlib.sha256(data).hexdigest()
    assert "tiles" not in m
    assert m["counts"]["features"] == 22
    assert m["counts"]["streets"] == 14          # street* + lane* kinds
    assert m["counts"]["crossings"] == 7
    assert m["counts"]["houses"] == 14           # 18 rows - 2 not exportable - 1 duplicate - 1 unattached
    assert m["counts"]["eponyms"] == 8           # unreferenced eponym dropped
    assert m["attribution"][0].startswith("© OpenStreetMap")
    assert any("Wikidata" in a for a in m["attribution"])
    assert any("Википедия" in a for a in m["attribution"])
    assert any("Энциклопедия" in a for a in m["attribution"])


def test_json_is_compact_utf8(pack):
    out = pack["_dir"]
    for name in DATA_FILES:
        raw = (out / name).read_bytes()
        assert raw == dumps(json.loads(raw.decode("utf-8")))
        assert b"\\u" not in raw
    assert "ქუჩა".encode() in (out / "core.json").read_bytes()


def test_export_is_deterministic_and_version_increments(tmp_path):
    con = make_db()
    out = tmp_path / "data"
    m1 = do_export(con, out)["manifest"]
    first = {n: (out / n).read_bytes() for n in DATA_FILES}
    m2 = do_export(con, out)["manifest"]
    assert {n: (out / n).read_bytes() for n in DATA_FILES} == first
    assert m1["version"] == "20261024-1" and m2["version"] == "20261024-2"
    m3 = do_export(con, out, now=dt.datetime(2026, 10, 25, 9, 0))["manifest"]
    assert m3["version"] == "20261025-1"


def test_no_nulls_and_optional_fields_omitted(pack):
    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                assert v is not None, f"null at {path}/{k}"
                walk(v, f"{path}/{k}")
        elif isinstance(o, list) and not path.startswith("/h/"):
            for i, v in enumerate(o):
                walk(v, f"{path}/{i}")
    walk(pack["core"])
    walk(pack["eponyms"])
    core = pack["core"]
    les = core["f"][fidx(core, "ლესელიძის ქუჩა")]
    assert "ru" not in les and "fam" not in les and "o" not in les and "par" not in les
    for f in core["f"]:
        assert {"ka", "k", "len", "c", "hn"} <= set(f)


# ------------------------------------------------------------------ features
def test_kinds_and_feature_order(pack):
    core = pack["core"]
    assert [k["code"] for k in core["kinds"]] == [k["code"] for k in load_kinds()]
    assert [k["code"] for k in FALLBACK_KIND_TABLE][:3] == ["street", "avenue", "highway"]
    ks = [f["k"] for f in core["f"]]
    assert ks == sorted(ks)
    fold = lambda f: (f.get("ru") or f["ka"]).casefold()  # noqa: E731
    for a, b in zip(core["f"], core["f"][1:]):
        if a["k"] == b["k"]:
            assert fold(a) <= fold(b)
    code = {i: core["kinds"][f["k"]]["code"] for i, f in enumerate(core["f"])}
    assert code[fidx(core, "უცნაური სახეობა")] == "other"   # unknown kind 'foo'


def test_references_remapped(pack):
    core, eps = pack["core"], pack["eponyms"]
    street = fidx(core, "გალაკტიონ ტაბიძის ქუჩა")
    lane = fidx(core, "გალაკტიონ ტაბიძის I შესახვევი")
    f = core["f"][lane]
    assert f["par"] == street and f["o"] == 1
    assert f["fam"] == core["f"][street]["fam"]
    fam = core["fam"][f["fam"]]
    assert fam == {"ka": "გალაკტიონ ტაბიძის", "ru": "Галактиона Табидзе", "e": fam["e"]}
    assert eps["e"][fam["e"]]["ru"] == "Галактион Табидзе"
    asat = core["fam"][core["f"][fidx(core, "ლადო ასათიანის II შესახვევი")]["fam"]]
    assert "e" not in asat
    assert len(core["fam"]) == 8                  # unused family dropped


def test_feature_fields(pack):
    core = pack["core"]
    d = {x.get("ru"): j for j, x in enumerate(core["districts"])}
    av = core["f"][fidx(core, "ილია ჭავჭავაძის გამზირი")]
    assert av["d"] == [d["Мтацминда"], d["Ваке"]]          # by share, descending
    assert av["len"] == 1112 and av["c"] == [4169500, 4479500]
    assert av["bb"] == [4169000, 4479500, 4170000, 4479500]  # derived from geometry
    st = core["f"][fidx(core, "გალაკტიონ ტაბიძის ქუჩა")]
    assert st["bb"] == [4169500, 4479000, 4169500, 4480000] and st["q"] == "Q144086"
    assert st["alt"] == ["გ. ტაბიძის ქუჩა", "Galaktion Tabidze St", "Tabidze street"]  # ru duplicate dropped
    lane = core["f"][fidx(core, "გალაკტიონ ტაბიძის I შესახვევი")]
    assert lane["len"] == 249 and "c" in lane                # len and point computed from geometry
    sq = core["f"][fidx(core, "თავისუფლების მოედანი", "square")]
    assert sq["len"] == 0
    metro = core["f"][fidx(core, "თავისუფლების მოედანი", "metro")]
    assert "bb" not in metro                                  # degenerate bbox of a point omitted
    p = {x.get("ru"): j for j, x in enumerate(core["places"])}
    assert core["f"][fidx(core, "მთაწმინდის რაიონი")]["ar"] == d["Мтацминда"]
    assert core["f"][fidx(core, "სოლოლაკი")]["ar"] == p["Сололаки"]
    sol = core["places"][p["Сололаки"]]
    assert sol == {"ka": "სოლოლაკი", "ru": "Сололаки", "en": "Sololaki", "kind": "neighbourhood",
                   "parent": d["Мтацминда"], "c": [4168850, 4479000], "km2": 0.49}
    assert core["places"][p["Вера"]]["parent"] == d["Ваке"]


def test_old_names_merged_newest_first(pack):
    core, eps = pack["core"], pack["eponyms"]
    old = core["f"][fidx(core, "ლესელიძის ქუჩა")]["old"]
    assert [o.get("ru") or o.get("ka") for o in old] == ["улица Ленина", "Армянская", "სომხის ქუჩა"]
    assert old[0]["from"] == 1921 and old[0]["to"] == 1990 and old[0]["src"] == "enc2008"
    assert eps["e"][old[0]["e"]]["ru"] == "Ленин"
    assert old[2] == {"ka": "სომხის ქუჩა", "src": "osm"}
    assert all("Пушкина" not in json.dumps(o, ensure_ascii=False) for o in old)   # confidence < 0.5
    assert "Люксембург" not in json.dumps(core, ensure_ascii=False)             # unlinked history row
    av_old = core["f"][fidx(core, "ილია ჭავჭავაძის გამზირი")]["old"]
    assert av_old == [{"ka": "ჭავჭავაძის ქუჩა", "lat": "Chavchavadze st", "from": 1950, "to": 1960,
                       "e": av_old[0]["e"], "src": "kawiki"}]


def test_crossings_and_near(pack):
    core = pack["core"]
    x, xp, xd = core["x"], core["xp"], core["xd"]
    pairs = {(x[2 * t], x[2 * t + 1]): (xp[2 * t], xp[2 * t + 1], xd[2 * t], xd[2 * t + 1]) for t in range(len(x) // 2)}
    assert all(a < b for a, b in pairs)
    street = fidx(core, "გალაკტიონ ტაბიძის ქუჩა")
    av = fidx(core, "ილია ჭავჭავაძის გამზირი")
    lane = fidx(core, "გალაკტიონ ტაბიძის I შესახვევი")
    assert pairs[(min(street, av), max(street, av))][:2] == (4169500, 4479500)
    # db (102 lane, 103 avenue) had dir_a=68 for the lane; the avenue sorts first, so masks swap
    assert av < lane and pairs[(av, lane)][2:] == (17, 68)
    asat = fidx(core, "ლადო ასათიანის II შესახვევი")
    near = core["near"]
    assert (min(lane, asat), max(lane, asat)) in {(near[2 * t], near[2 * t + 1]) for t in range(len(near) // 2)}


# ------------------------------------------------------------------ houses
def test_houses(pack):
    core, houses = pack["core"], pack["houses"]
    h = houses["h"]
    for i, f in enumerate(core["f"]):
        assert f["hn"] == len(h.get(str(i), []))
    er = h[str(fidx(core, "ანასტასია ერისთავის ქუჩა"))]
    assert ["3/16", 3, "", "16", 4170505, 4478100] in er
    assert ["16 კორპ. 3", 16, "", "", 4170505, 4478500, None, "3"] in er   # corp without range: null n_to
    av = h[str(fidx(core, "ილია ჭავჭავაძის გამზირი"))]
    assert av[0] == ["1-5", 1, "", "", 4169050, 4479505, 5]
    assert len(h[str(fidx(core, "ლესელიძის ქუჩა"))]) == 1                   # duplicate collapsed
    assert len(h[str(fidx(core, "ანასტასია ერისთავის შესახვევი"))]) == 2     # list "1;3" -> 2 rows
    tab = h[str(fidx(core, "გალაკტიონ ტაბიძის ქუჩა"))]
    assert [r[0] for r in tab] == ["1", "3", "3ა", "5"]                      # "9" has no point
    assert str(fidx(core, "კაკლის ჩიხი")) not in h                          # junk only
    p = {x.get("ru"): j for j, x in enumerate(core["places"])}
    assert houses["place"] == {str(p["Вера"]): [["12", 12, "", "", 4170810, 4478410]]}
    stats = pack["_res"]["stats"]
    assert stats["problems"]["house_unattached"] == 1
    assert stats["problems"]["house_not_exportable"] == 2


# ------------------------------------------------------------------ geometry and areas
def test_geometry(pack):
    core, g = pack["core"], pack["geom"]["g"]
    assert pack["geom"]["prec"] == 5 and len(g) == len(core["f"])
    st = g[fidx(core, "გალაკტიონ ტაბიძის ქუჩა")]
    assert len(st) == 1 and decode_ints(st[0]) == [(4169500, 4479000), (4169500, 4480000)]  # merged + DP 3 m
    river = g[fidx(core, "მტკვარი")]
    assert len(river) == 2
    assert decode_ints(g[fidx(core, "თავისუფლების მოედანი", "metro")][0]) == [(4169350, 4480150)]
    ring = decode_ints(g[fidx(core, "თავისუფლების მოედანი", "square")][0])
    assert ring[0] == ring[-1] and len(ring) == 5
    unk = core["f"][fidx(core, "უცნობი ობიექტი")]
    assert decode_ints(g[fidx(core, "უცნობი ობიექტი")][0]) == [tuple(unk["c"])]  # no geometry -> point


def test_areas(pack):
    core, areas = pack["core"], pack["areas"]
    assert len(areas["districts"]) == len(core["districts"]) == 2
    d = {x.get("ru"): j for j, x in enumerate(core["districts"])}
    vake = areas["districts"][d["Ваке"]]["poly"]
    assert len(vake) == 1 and len(vake[0]) == 2                       # outer + hole
    for ring in vake[0]:
        pts = decode_ints(ring)
        assert pts[0] == pts[-1] and len(pts) >= 4
    p = {x.get("ru"): j for j, x in enumerate(core["places"])}
    assert areas["places"][p["Вера"]]["poly"] == []


# ------------------------------------------------------------------ eponyms
def test_eponyms(pack):
    eps = pack["eponyms"]
    by_ru = {e["ru"]: e for e in eps["e"]}
    assert "Никто" not in by_ru
    assert by_ru["Анастасия Эристави-Хоштария"]["ai"] == 1        # eponym source llm
    assert by_ru["Барбаре Джорджадзе"]["ai"] == 1                  # categories from llm
    assert by_ru["Галактион Табидзе"]["ai"] == 0
    assert by_ru["Ленин"]["cat"] == ["power.revolutionary"] and by_ru["Ленин"]["o"] == "fo"
    il = by_ru["Илья Чавчавадзе"]
    assert il["cat"] == ["lit.writer", "lit.poet", "power.public"] and il["occ"] == ["писатель", "поэт", "публицист"]
    assert il["g"] == "m" and il["b"] == 1837 and il["d"] == 1907 and il["o"] == "ge" and il["q"] == "Q166232"
    assert "g" not in by_ru["Казбек"] and by_ru["Казбек"]["k"] == "toponym"
    assert pack["_res"]["stats"]["problems"]["category_not_in_taxonomy"] == 1
    assert set(eps["kinds"]["persons_multi"]) >= {"персона"}
    lit = next(t for t in eps["taxonomy"] if t["id"] == "lit")
    poet = next(c for c in lit["children"] if c["id"] == "lit.poet")
    assert {"поэт", "поэты", "поэтесса"} <= set(poet["aliases"])
    place = next(t for t in eps["taxonomy"] if t["id"] == "place")
    assert place["aliases"] == ["топонимы", "место"]                 # given aliases kept


def test_eponym_kinds_merge():
    k = eponym_kinds({"person": ["персона", "Люди"], "persons_multi": ["несколько человек"], "toponym": "топоним"})
    assert k["person"] == ["персона", "люди"]
    assert k["persons_multi"] == ["несколько человек", "персона", "люди"]   # docs/dsl.md: персона = both
    assert k["toponym"] == ["топоним"]
    assert "персона" in eponym_kinds()["persons_multi"]


def test_default_taxonomy_sources_load():
    tax, src, kinds = load_taxonomy()
    assert tax and all(g["aliases"] for g in tax) and src
    assert "персона" in kinds["person"]


def test_alias_rules():
    assert derive_aliases("писатель, прозаик") == ["писатель", "писатели", "писательница", "писательницы",
                                                     "прозаик", "прозаики"]
    assert derive_aliases("памятная дата (26 мая, 9 апреля)") == ["памятная дата"]
    assert plural("святой") == "святые" and feminine("святой") == "святая" and plural("святая") == "святые"
    assert feminine("актёр") == "актриса" and plural("врач") == "врачи" and plural("царь") == "цари"
    assert derive_aliases("Актёр") == ["актер", "актеры", "актриса", "актрисы"]


# ------------------------------------------------------------------ tiles, empty DB, CLI
def test_tiles_manifest_is_copied(tmp_path):
    con = make_db()
    tm = tmp_path / "tiles-manifest.json"
    tm.write_text(json.dumps({
        "v": 1, "basemap": "tbilisi.pmtiles", "minzoom": 0, "maxzoom": 15, "bbox": [44.6, 41.6, 45.1, 41.87],
        "glyphs": "fonts/{fontstack}/{range}.pbf", "sprites": {"light": "sprites/light"},
        "attribution": "Protomaps © OpenStreetMap contributors, ODbL", "tiles_by_zoom": {"15": 1598},
        "files": {"tbilisi.pmtiles": {"bytes": 123, "sha256": "a" * 64},
                  "fonts/Noto Sans Regular/0-255.pbf": {"bytes": 7, "sha256": "b" * 64},
                  "tbilisi-center.pmtiles": {"bytes": 9, "sha256": "c" * 64},
                  "bad": {"bytes": 1}}}), encoding="utf-8")
    m = do_export(con, tmp_path / "data", tiles_manifest=tm)["manifest"]
    t = m["tiles"]
    assert t["files"] == {"tiles/fonts/Noto Sans Regular/0-255.pbf": {"bytes": 7, "sha256": "b" * 64},
                          "tiles/tbilisi.pmtiles": {"bytes": 123, "sha256": "a" * 64}}
    assert t["basemap"] == "tiles/tbilisi.pmtiles" and t["bytes"] == 130
    assert t["glyphs"] == "tiles/fonts/{fontstack}/{range}.pbf" and t["sprites"] == {"light": "tiles/sprites/light"}
    assert t["maxzoom"] == 15 and "tiles_by_zoom" not in t
    assert "Подложка: Protomaps © OpenStreetMap contributors, ODbL" in m["attribution"]


def test_empty_database_exports(tmp_path):
    con = open_db(":memory:")
    res = do_export(con, tmp_path / "data")
    m = res["manifest"]
    assert m["counts"]["features"] == 0 and "osm" not in m
    core = json.loads((tmp_path / "data" / "core.json").read_text(encoding="utf-8"))
    assert core["f"] == [] and core["x"] == [] and len(core["kinds"]) >= 26


def test_cli_main(tmp_path):
    db = tmp_path / "runcity.sqlite"
    make_db(db).close()
    out = tmp_path / "data"
    rc = export_main(["--db", str(db), "--out", str(out), "--tiles-manifest", str(tmp_path / "none.json"),
                      "--stats", str(tmp_path / "stats.json")])
    assert rc == 0
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8"))["counts"]["features"] == 22
    assert (tmp_path / "stats.json").exists()
    assert export_main(["--db", str(tmp_path / "missing.sqlite"), "--out", str(out)]) == 2
