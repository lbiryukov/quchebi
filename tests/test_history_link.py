"""Linking history articles to features and building name_history (fixture database)."""
import json
from pathlib import Path

import pytest

from pipeline.db import open_db
from pipeline.history import link, wiki_parse
from pipeline.history.store import replace_articles

FIX = json.loads((Path(__file__).parent / "fixtures" / "history" / "wiki_pages.json").read_text(encoding="utf-8"))


def enc(title, facts, page="10"):
    f = {"title": title, "kind": "street", **facts}
    return {"source": "enc2008", "title": title, "page": page, "text_hash": "h" + str(abs(hash(title)) % 10**8),
            "entry": "article", "facts": f, "needs_llm": False}


@pytest.fixture()
def db(tmp_path):
    con = open_db(tmp_path / "t.sqlite")
    areas = [(1, "district", "გლდანის რაიონი"), (2, "district", "მთაწმინდის რაიონი"),
             (3, "neighbourhood", "ავჭალა"), (4, "neighbourhood", "სოლოლაკი")]
    con.executemany("INSERT INTO area(id, kind, ka) VALUES (?,?,?)", areas)
    con.execute("INSERT INTO family(id, key, base_ka) VALUES (1, 'ბერიძ ზაალ', 'ზაალ ბერიძის')")
    feats = [
        (1, "street", "ზაალ ბერიძის ქუჩა", "улица Заала Беридзе", 1, "41.79,44.80,41.80,44.81"),
        (2, "street", "ვაშლის ქუჩა", None, None, "41.69,44.78,41.70,44.79"),
        (3, "street", "ვაშლის ქუჩა", None, None, "41.78,44.80,41.785,44.805"),
        (4, "street", "ჯანსუღ კახიძის ქუჩა", None, None, "41.700,44.770,41.702,44.775"),
        (5, "street", "გია ყანჩელის ქუჩა", None, None, "41.690,44.790,41.692,44.795"),
        (6, "street", "ბაღის ქუჩა", None, None, "41.70,44.80,41.71,44.81"),
        (7, "street", "ბაღის ქუჩა", None, None, "41.75,44.75,41.76,44.76"),
    ]
    for fid, kind, ka, ru, fam, bbox in feats:
        a, b, c, d = (float(x) for x in bbox.split(","))
        con.execute("INSERT INTO feature(id, kind, ka, ru, family_id, bbox, lat, lon) VALUES (?,?,?,?,?,?,?,?)",
                    (fid, kind, ka, ru, fam, bbox, (a + c) / 2, (b + d) / 2))
    con.executemany("INSERT INTO feature_area(feature_id, area_id) VALUES (?,?)",
                    [(1, 1), (1, 3), (2, 2), (2, 4), (3, 1), (3, 3), (4, 2), (5, 2), (5, 4)])
    con.execute("INSERT INTO feature_name(feature_id, lang, name, kind, source) VALUES "
                "(5, 'ka', 'კონსტიტუციის ქუჩა', 'old', 'osm')")
    houses = [(None, "ივანე კრილოვის ქუჩა", 41.7010, 44.7720), (None, "ივანე კრილოვის ქუჩა", 41.7012, 44.7740),
              (None, "ფეიერვერკის ქუჩა", 41.6910, 44.7920), (5, "გია ყანჩელის ქუჩა", 41.6911, 44.7921)]
    con.executemany("INSERT INTO house(feature_id, raw, addr_street, lat, lon, match) VALUES (?, '1', ?, ?, ?, 'none')",
                    houses)
    con.commit()
    recs = [
        enc("ზაალ ბერიძის ქუჩა", {"district_2008": "გლდანი-ნაძალადევის", "place": "ავჭალა",
                                  "former": [{"name_ka": "ვ. ლენინის ქუჩა", "confidence": 0.85},
                                             {"name_ka": "ს. ზალიკაშვილის ქუჩა", "from_year": 1994, "to_year": 1995,
                                              "confidence": 0.85}]}),
        enc("ვაშლის ქუჩა", {"district_2008": "გლდანი-ნაძალადევის", "place": "ავჭალა",
                            "former": [{"name_ka": "ხეხილის ქუჩა", "to_year": 1950, "confidence": 0.85}]}),
        enc("ივანე კრილოვის ქუჩა", {"district_2008": "ძველი თბილისის", "named_year": {"year": 1940},
                                    "former": [{"name_ka": "ზედა ქუჩა", "to_year": 1940, "confidence": 0.85}]}),
        enc("კონსტიტუციის ქუჩა", {"district_2008": "ძველი თბილისის", "named_year": {"year": 1938},
                                  "former": [{"name_ka": "ფეიერვერკის ქუჩა", "to_year": 1923, "attested": [1903],
                                              "confidence": 0.85}]}),
        enc("ბაღის ქუჩა", {"former": [{"name_ka": "ვენახის ქუჩა", "confidence": 0.85}]}),
    ]
    replace_articles(con, recs, ("enc2008", "enc2008_bio"))
    replace_articles(con, [wiki_parse.parse_ka("ზაალ ბერიძის ქუჩა", FIX["ka"]["ზაალ ბერიძის ქუჩა"])], ("kawiki",))
    replace_articles(con, [wiki_parse.parse_ru("Улица Заала Беридзе", FIX["ru"]["Улица Заала Беридзе"])], ("ruwiki",))
    yield con
    con.close()


def rows(con, fid):
    return [dict(r) for r in con.execute("SELECT * FROM name_history WHERE feature_id IS ? ORDER BY id", (fid,))]


def test_link_and_name_history(db):
    st = link.run(db)
    assert st["enc2008_articles"] == 5 and st["enc2008_linked"] == 4
    assert st["kawiki_linked"] == 1 and st["ruwiki_linked"] == 1
    assert st["how"]["ruwiki:qid"] == 1 and st["how"]["enc2008:addr"] == 1 and st["how"]["enc2008:old"] == 1

    r1 = {r["name_ka"]: r for r in rows(db, 1)}
    z = r1["ს. ზალიკაშვილის ქუჩა"]
    assert (z["from_year"], z["to_year"]) == (1994, 1995)
    assert z["name_ru"] == "Заликашвили"                       # Russian form merged from ru.wikipedia
    assert z["source"] == "enc2008" and "kawiki:101" in z["ref"] and "ruwiki:201" in z["ref"]
    assert z["family_id"] == 1 and z["name_lat"]
    assert r1["ვ. ლენინის ქუჩა"]["name_ru"] == "Ленина"
    assert r1["ვ. ლენინის ქუჩა"]["to_year"] == 1994              # year from the ru infobox
    assert "ურთის ქუჩა" in r1                                    # ka infobox only
    assert r1["ურთის ქუჩა"]["name_ru"]                           # ka_to_ru fallback

    # homonyms: district/place evidence picks the Gldani one
    assert [r["name_ka"] for r in rows(db, 3)] == ["ხეხილის ქუჩა"]
    assert rows(db, 2) == []
    # renamed after 2008, found by addresses still using the 2008 name
    r4 = {r["name_ka"]: r for r in rows(db, 4)}
    assert r4["ივანე კრილოვის ქუჩა"]["from_year"] == 1940
    assert "ზედა ქუჩა" in r4
    # renamed after 2008, found by OSM old_name; address evidence merged into the old name row
    r5 = {r["name_ka"]: r for r in rows(db, 5)}
    assert r5["კონსტიტუციის ქუჩა"]["from_year"] == 1938
    assert "osm" in r5["კონსტიტუციის ქუჩა"]["ref"]
    assert r5["ფეიერვერკის ქუჩა"]["to_year"] == 1923 and "addr:1" in r5["ფეიერვერკის ქუჩა"]["ref"]
    # ambiguous homonyms without district: unlinked rows only
    assert rows(db, 6) == [] and rows(db, 7) == []
    un = rows(db, None)
    assert [r["name_ka"] for r in un] == ["ვენახის ქუჩა"] and un[0]["confidence"] <= 0.5
    # article links
    fa = dict(db.execute("SELECT title, feature_id FROM history_article WHERE source='enc2008'").fetchall())
    assert fa["ზაალ ბერიძის ქუჩა"] == 1 and fa["ვაშლის ქუჩა"] == 3 and fa["ბაღის ქუჩა"] is None


def test_link_is_idempotent(db):
    link.run(db)
    n1 = db.execute("SELECT COUNT(*) FROM name_history").fetchone()[0]
    link.run(db)
    assert db.execute("SELECT COUNT(*) FROM name_history").fetchone()[0] == n1


def test_link_without_features_is_skipped(tmp_path):
    con = open_db(tmp_path / "empty.sqlite")
    assert link.run(con)["skipped"] == "no features"
    con.close()


def test_given_name_compatibility_for_loose_links():
    assert link._given_compatible("კონსტანტინე მაყაშვილის ქუჩა", "კოტე მაყაშვილის ქუჩა")
    assert link._given_compatible("ს. ზალიკაშვილის ქუჩა", "სოლომონ ზალიკაშვილის ქუჩა")
    assert link._given_compatible("გერგეტის ქუჩა", "გერგეთის ქუჩა")
    assert not link._given_compatible("მერაბ ქავთარაძის ქუჩა", "მიხეილ ქავთარაძის ქუჩა")
    assert not link._given_compatible("ვეძისის ქუჩა", "ზემო ვეძისის ქუჩა")


def test_ru_matches_ka():
    assert link._ru_matches_ka("Воронцовская", "ვორონცოვის ქუჩა")
    assert link._ru_matches_ka("Ленина", "ვ. ლენინის ქუჩა")
    assert not link._ru_matches_ka("Ленина", "ბაღის ქუჩა")
