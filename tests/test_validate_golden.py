"""Golden counts (docs/dsl.md golden queries, zone off): SQL on the DB vs evaluation on the pack."""
import datetime as dt
import json
from pathlib import Path

import pytest

from pipeline.db import open_db
from pipeline.export.build import export_pack
from pipeline.validate.golden import QUERIES, golden_pack, golden_sql, resolve_who
from pipeline.validate.run import run_validation

FIX = Path(__file__).parent / "fixtures" / "export"

# Hand-counted on tests/fixtures/export/fixture.sql
EXPECTED = {
    "дом:3": 5,                                    # Tabidze st (3, 3ა), avenue (range 1-5), Eristavi st (3/16),
                                                   # Eristavi lane (list 1;3), Leselidze (3)
    "тип:улица* дом:3": 4,                         # minus the lane
    "тип:переулок x:(тип:улица* кто:поэт)": 3,     # Tabidze lane x avenue, Chavchavadze lane x Tabidze st,
                                                   # Asatiani lane x Tabidze st (Eristavi lane: writer only)
    "тип:тупик дом:нет": 2,                        # Jorjadze, Kakli (junk number only); Kazbegi has "2"
    "кто:писатель пол:ж": 3,                       # Eristavi st + lane, Jorjadze dead end
    "тип:улица -кто:персона": 2,                   # 26 May (event), Leselidze (no family); Zubalashvili excluded
    "тип:переулок*": 8,                            # 4 lanes + 3 dead ends + 1 passage
}


def make_db():
    con = open_db(":memory:")
    con.executescript((FIX / "fixture.sql").read_text(encoding="utf-8"))
    con.commit()
    return con


@pytest.fixture()
def built(tmp_path):
    con = make_db()
    out = tmp_path / "data"
    export_pack(con, out, tiles_manifest=None, now=dt.datetime(2026, 10, 24, 12, 0),
                taxonomy_paths=(FIX / "taxonomy.json",), stats_path=tmp_path / "build" / "export_stats.json")
    yield con, out, tmp_path / "build"
    con.close()


def _pack(out: Path):
    return {n: json.loads((out / f"{n}.json").read_text(encoding="utf-8")) for n in ("core", "houses", "eponyms")}


def test_query_list_matches_task():
    assert set(QUERIES) == set(EXPECTED)


def test_golden_sql_on_fixture(built):
    con, out, _ = built
    assert golden_sql(con, _pack(out)["core"]["kinds"]) == EXPECTED


def test_golden_pack_on_fixture(built):
    _, out, _ = built
    assert golden_pack(_pack(out)) == EXPECTED


def test_golden_file_written(built):
    con, out, build = built
    r = run_validation(out, con, build)
    assert r["check"].ok, r["check"].errors
    assert json.loads((build / "golden_counts.json").read_text(encoding="utf-8")) == EXPECTED


def test_sql_pack_disagreement_is_an_error(built):
    con, out, build = built
    con.execute("DELETE FROM house WHERE id = 14")     # Leselidze "3" (its duplicate 15 stays)
    con.execute("DELETE FROM house WHERE id = 15")
    con.commit()
    r = run_validation(out, con, build)
    assert any("SQL and pack disagree" in e for e in r["check"].errors)
    assert r["golden_sql"]["дом:3"] == 4 and r["golden_pack"]["дом:3"] == 5


def test_stale_pack_is_only_a_warning(built):
    con, out, build = built
    con.execute("INSERT INTO feature(id, kind, ka) VALUES (999, 'lane', 'ახალი შესახვევი')")
    con.commit()
    r = run_validation(out, con, build)
    assert r["check"].ok
    assert any("pack is stale" in w for w in r["check"].warnings)


def test_resolve_who(built):
    _, out, _ = built
    eps = _pack(out)["eponyms"]
    assert resolve_who("поэт", eps) == ({"lit.poet"}, set())
    assert resolve_who("Поэтесса", eps) == ({"lit.poet"}, set())
    assert resolve_who("писательница", eps) == ({"lit.writer"}, set())
    ids, _ = resolve_who("литература", eps)
    assert ids == {"lit", "lit.poet", "lit.writer"}
    assert resolve_who("lit.writer", eps) == ({"lit.writer"}, set())
    assert resolve_who("персона", eps) == (set(), {"person", "persons_multi"})
    assert resolve_who("хормейстер", eps) == (set(), set())
