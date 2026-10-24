"""facts -> dossiers -> batches on the fixture (offline fake Wikidata)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "enrich"))
from enrich_fake import FakeClient, make_db, prepare  # noqa: E402

from pipeline.config import BUILD, ENRICH
from pipeline.enrich import batches as B
from pipeline.enrich import facts as FA
from pipeline.enrich.common import load_families, paths, read_jsonl


@pytest.fixture()
def env(tmp_path):
    out = prepare(tmp_path)
    yield out
    paths.set(ENRICH, BUILD)


def eponym_of(con, key):
    row = con.execute("SELECT e.* FROM family f JOIN eponym e ON e.id=f.eponym_id WHERE f.key=?", (key,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["cats"] = [c for (c,) in con.execute("SELECT category FROM eponym_category WHERE eponym_id=? ORDER BY rank",
                                           (d["id"],))]
    return d


def test_facts_from_osm_qid(env):
    con = env["con"]
    e = eponym_of(con, "ალექსანდრ პუშკინ")
    assert e["qid"] == "Q7200" and e["source"] == "osm" and e["kind"] == "person"
    assert e["ru"] == "Александр Сергеевич Пушкин" and e["gender"] == "m"
    assert (e["birth_year"], e["death_year"]) == (1799, 1837)
    assert e["origin"] == "foreign"
    assert "поэт" in json.loads(e["occupations_ru"])
    assert "lit.poet" in e["cats"] and "lit.writer" not in e["cats"]
    assert e["needs_review"] == 0


def test_display_name_and_origin(env):
    e = eponym_of(env["con"], "ანდრ აფაქიძ")
    assert e["ru"] == "Андрия Мелитонович Апакидзе"
    assert e["origin"] == "georgian"
    assert FA.display_name("Пушкин, Александр", True) == "Александр Пушкин"
    assert FA.display_name("Москва, Россия", False) == "Москва, Россия"


def test_multi_qid_family_and_rules_eponyms(env):
    con = env["con"]
    e = eponym_of(con, "გაბუნიებ ლეო ნოდრ")
    assert e["kind"] == "persons_multi" and e["key"] == "multi:Q12866286|Q338218" and e["qid"] is None
    assert "group.family" in e["cats"]
    d = eponym_of(con, "26 მაის")
    assert d["source"] == "rules" and d["kind"] == "event_date" and d["cats"] == ["event.date"]
    n = eponym_of(con, "#21-ე ქუჩა")
    assert n["kind"] == "no_eponym" and n["cats"] == ["none.numbered"]
    t = eponym_of(con, "ატოც")
    assert t["kind"] == "toponym" and t["cats"] == ["place.ge_city"]


def test_wd_match_eponym_and_queue_reasons(env):
    con, st = env["con"], env["facts"]
    e = eponym_of(con, "გივ კარტოზ")
    assert e["qid"] == "Q1371376" and e["source"] == "wd_match"
    assert st["გიორგ შარვაშიძ"] == {"status": "queue", "reasons": ["person_ambiguous"]}
    assert st["გელოვნ მარშლ"]["reasons"] == ["person_single_token"]
    assert st["მარიამ ნან"]["reasons"] == ["persons_multi"]
    assert eponym_of(con, "გიორგ შარვაშიძ") is None
    # "9 აპრილის" carries an OSM QID of the 1989 events but stays a memorial date
    e = eponym_of(con, "9 აპრილ")
    assert e["qid"] == "Q1194400" and e["kind"] == "event_date" and e["cats"][0] == "event.date"
    assert st["9 აპრილ"]["status"] == "decided"


def test_facts_is_idempotent(env):
    con = env["con"]
    before = con.execute("SELECT COUNT(*) FROM eponym").fetchone()[0]
    cats = con.execute("SELECT COUNT(*) FROM eponym_category").fetchone()[0]
    FA.run(con, load_families(con), env["rules"], env["qids"], env["match"], env["client"])
    assert con.execute("SELECT COUNT(*) FROM eponym").fetchone()[0] == before
    assert con.execute("SELECT COUNT(*) FROM eponym_category").fetchone()[0] == cats


def test_stale_link_is_removed_when_qid_disappears(env, tmp_path):
    con = env["con"]
    con.execute("UPDATE feature SET wikidata=NULL WHERE family_id=(SELECT id FROM family WHERE key='ლიპტოვ')")
    con.commit()
    from pipeline.enrich import qids as Q
    fams = load_families(con)
    qids = Q.run(con, fams, None, street_rows=[])
    FA.run(con, fams, env["rules"], qids, env["match"], env["client"])
    assert eponym_of(con, "ლიპტოვ") is None
    assert con.execute("SELECT COUNT(*) FROM eponym WHERE qid='Q1521564'").fetchone()[0] == 0


def test_dossiers_content(env):
    rows = env["dossiers"]
    kinds = {r["meta"]["kind"] for r in rows}
    assert {"queue", "gold"} <= kinds
    for r in rows:
        d = r["dossier"]
        if r["meta"]["kind"] in ("queue", "gold", "verify"):
            assert d["family_id"].startswith("F") and len(d["family_id"]) == 9
            assert len(d.get("candidates", [])) <= 5
        if r["meta"]["kind"] == "gold":
            assert "queue_reasons" not in d
            assert all("found_by" not in c for c in d.get("candidates", []))
            assert r["meta"]["gold_qid"]
    sh = next(r for r in rows if r["meta"].get("family_key") == "გიორგ შარვაშიძ" and r["meta"]["kind"] == "queue")
    assert sh["meta"]["n_georgian_candidates"] >= 2
    assert sh["dossier"]["candidates"][0]["is_human"] and sh["dossier"]["queue_reasons"] == ["person_ambiguous"]
    assert sh["dossier"]["candidates"][0]["found_by"] == "label_match"
    # meta (gold answer etc.) is never part of a dossier
    assert all("gold_qid" not in json.dumps(r["dossier"]) for r in rows)


def test_batches_written_and_cached_skipped(env):
    man = env["manifest"]
    c = man["classifier"]["batches"]
    assert "c001" in c and "k001" in c
    lines = read_jsonl(paths.batches / "classifier" / "c001.jsonl")
    assert len(lines) == c["c001"]["n"] <= 40
    assert man["classifier"]["model"] == "haiku" and len(man["classifier"]["prompt_version"]) == 10
    # simulate answers cached for every c001 line -> the next run has no c-batch
    from pipeline.enrich.common import agent_prompt_version, llm_cache_key
    pv, model = agent_prompt_version("classifier")
    con = env["con"]
    for d in lines:
        con.execute("INSERT INTO llm_result(cache_key, role, model, family_key, batch, payload) VALUES (?,?,?,?,?,?)",
                    (llm_cache_key(pv, model, d), "classifier", model, d["family_id"], "c001", "{}"))
    con.commit()
    man2 = B.run(con, env["dossiers"], env["client"], roles=("classifier",))
    assert "c001" not in man2["classifier"]["batches"]
    assert not (paths.batches / "classifier" / "c001.jsonl").exists()  # unanswered old batch replaced


def test_small_batch_size(tmp_path):
    out = prepare(tmp_path, with_batches=False)
    try:
        man = B.run(out["con"], out["dossiers"], out["client"], size=5)
        assert all(b["n"] <= 5 for b in man["classifier"]["batches"].values())
        assert len([n for n in man["classifier"]["batches"] if n.startswith("c")]) >= 4
    finally:
        paths.set(ENRICH, BUILD)


def test_candidates_from_search_and_extracts(tmp_path):
    client = FakeClient(search_map={"დავით ბერიაშვილი": ["Q7200"]},
                        extracts_map={("ru", "Пушкин, Александр Сергеевич"): "Александр Сергеевич Пушкин — поэт" * 50})
    out = prepare(tmp_path, client=client, con=make_db(), with_batches=False)
    try:
        d = next(r["dossier"] for r in out["dossiers"]
                 if r["meta"].get("family_key") == "ბერიაშვილ დავით" and r["meta"]["kind"] == "queue")
        c = d["candidates"][0]
        assert c["qid"] == "Q7200" and c["found_by"] == "search"
        if "ruwiki_intro" in c:
            assert len(c["ruwiki_intro"]) <= 600
    finally:
        paths.set(ENRICH, BUILD)


def test_short_ru_mentions_works_and_groups():
    s = FA._short_ru("грузинский педагог", [], "georgian", ["Деда эна"], ["Месаме даси", "Академия наук"])
    assert s == "грузинский педагог; известен: «Деда эна»; участник: «Месаме даси»"
    assert FA._short_ru("", ["хормейстер", "дирижёр"], "georgian") == "грузинский хормейстер, дирижёр"
    assert len(FA._short_ru("x" * 150, [], "georgian", ["y" * 100])) <= FA.SHORT_MAX


def test_occupations_keep_narrow_labels(tmp_path):
    from enrich_fake import ent
    hm = ent("Q901001", ru="хормейстер", claims={"P279": ["Q158852"]})
    cond = ent("Q158852", ru="дирижёр", claims={"P279": ["Q639669"]})
    person = ent("Q901002", ka="ტესტ ტესტაძე", ru="Тест Тестадзе", claims={"P31": ["Q5"], "P106": ["Q901001"],
                 "P800": ["Q901003"]}, born=1900, died=1980, desc_ru="грузинский хормейстер")
    work = ent("Q901003", ru="Песнь о Тбилиси")
    b = FA.EponymBuilder(FakeClient(entities={x["id"]: x for x in (hm, cond, person, work)}))
    f = b.build(["Q901002"])["Q901002"]
    assert f.occupations_ru == ["хормейстер"]
    assert f.categories == ["art.musician"]
    assert "«Песнь о Тбилиси»" in f.short_ru


def test_tsinandali_kaspi_siemens(tmp_path):
    from enrich_fake import fixture
    from pipeline.enrich import gazetteer as G
    from pipeline.enrich import rules as R
    gz = fixture("gazetteer.json")
    gz["geo_georgia"] = gz["geo_georgia"] + [
        {"item": {"value": "http://www.wikidata.org/entity/Q1013960"}, "ka": {"value": "წინანდალი"},
         "cls": {"value": "http://www.wikidata.org/entity/Q532"}},
        {"item": {"value": "http://www.wikidata.org/entity/Q1018584"}, "ka": {"value": "კასპი"},
         "cls": {"value": "http://www.wikidata.org/entity/Q3957"}}]
    extra = [
        {"key": "წინანდლ", "base_ka": "წინანდლის", "base_ru": "Цинандали", "base_en": "Tsinandali",
         "nom_ka": "წინანდალი", "members": [{"ka": "წინანდლის ქუჩა", "ru": "улица Цинандали", "kind": "street"}]},
        {"key": "კასპ", "base_ka": "კასპის", "base_ru": "Каспи", "base_en": "Kaspi", "nom_ka": "კასპი",
         "members": [{"ka": "კასპის ქუჩა", "ru": "улица Каспи", "kind": "street"}]},
        {"key": "სიმენსებ ძმებ", "base_ka": "ძმები სიმენსების", "base_ru": "Братьев Сименс", "base_en": "",
         "nom_ka": "", "members": [{"ka": "ძმები სიმენსების ქუჩა", "ru": "улица Братьев Сименс", "kind": "street"}]},
    ]
    con = make_db(fixture("families.json") + extra)
    fams = load_families(con)
    gaz = G.build(con, None, rows_by_name=gz)
    rules = R.run(con, fams, gaz, out_dir=tmp_path)
    assert rules["წინანდლ"]["hint"] == "toponym:georgia_settlement"
    assert rules["კასპ"]["hint"] == "toponym:georgia_settlement"
    assert rules["სიმენსებ ძმებ"]["hint"] == "persons_multi:multi"
    from pipeline.enrich import qids as Q, match as M
    paths.set(tmp_path / "enrich", tmp_path / "build")
    try:
        client = FakeClient(entities={
            "Q1013960": {"id": "Q1013960", "labels": {"ru": {"value": "Цинандали"}}, "claims": {}, "sitelinks": {}},
            "Q1018584": {"id": "Q1018584", "labels": {"ru": {"value": "Каспи"}}, "claims": {}, "sitelinks": {}}})
        qids = Q.run(con, fams, None, street_rows=[])
        match = M.run(con, fams, rules, qids, client)
        FA.run(con, fams, rules, qids, match, client)
        for key in ("წინანდლ", "კასპ"):
            e = eponym_of(con, key)
            assert e["kind"] == "toponym" and e["cats"] == ["place.ge_city"], (key, e)
    finally:
        paths.set(ENRICH, BUILD)
