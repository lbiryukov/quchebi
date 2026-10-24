"""History articles as evidence: named_after, encyclopedia years, kawiki links, biographies."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "enrich"))
from enrich_fake import FakeClient, ent, fixture, make_db  # noqa: E402

from pipeline.config import BUILD, ENRICH
from pipeline.enrich import articles as A
from pipeline.enrich import dossiers as D
from pipeline.enrich import facts as FA
from pipeline.enrich import gazetteer as G
from pipeline.enrich import match as M
from pipeline.enrich import qids as Q
from pipeline.enrich import rules as R
from pipeline.enrich.common import load_families, paths


@pytest.fixture(autouse=True)
def tmp_paths(tmp_path):
    paths.set(tmp_path / "enrich", tmp_path / "build")
    yield
    paths.set(ENRICH, BUILD)


def test_consistent_name_and_bio_index():
    assert A.consistent_name("ჭავჭავაძის", "ილია ჭავჭავაძე")
    assert not A.consistent_name("26 მაისის", "სერგო ორჯონიკიძე")
    con = make_db([])
    con.executemany("INSERT INTO history_article(source, title, parsed_json) VALUES (?,?,?)", [
        ("enc2008_bio", "აბაშელი", json.dumps({"surname": "აბაშელი", "given": "ალექსანდრე (ისაკ)", "birth": 1884,
                                               "death": 1954, "occupation_ka": "პოეტი"})),
        ("enc2008_bio", "აბაშელი", json.dumps({"surname": "აბაშელი", "given": "ნინო", "birth": 1900})),
    ])
    idx = A.BioIndex(con)
    assert [b["given"] for b in idx.lookup("ალექსანდრე აბაშელის")] == ["ალექსანდრე (ისაკ)"]
    assert len(idx.lookup("აბაშელის")) == 2
    assert idx.lookup("გიორგი აბაშელის") == []


def test_namesakes_resolved_by_encyclopedia_years_and_kawiki_link():
    con = make_db()
    sh_id = con.execute("SELECT id FROM family WHERE key='გიორგ შარვაშიძ'").fetchone()[0]
    mel_id = con.execute("SELECT id FROM family WHERE key='გიორგ მელიქიშვილ'").fetchone()[0]
    con.executemany("INSERT INTO history_article(source, title, family_id, parsed_json) VALUES (?,?,?,?)", [
        ("enc2008", "გიორგი შარვაშიძის ქუჩა", sh_id, json.dumps(
            {"named_after": {"name_ka": "გიორგი შარვაშიძე", "birth": 1846, "death": 1918, "src": "enc2008_bio"}})),
        ("kawiki", "გიორგი მელიქიშვილის ქუჩა", mel_id, json.dumps(
            {"named_after": {"name_ka": "გიორგი მელიქიშვილი", "src": "kawiki_link"}})),
    ])
    con.commit()
    fams = load_families(con)
    gaz = G.build(con, None, rows_by_name=fixture("gazetteer.json"))
    rules = R.run(con, fams, gaz)
    qids = Q.run(con, fams, None, street_rows=[])
    base = FakeClient()
    mel = dict(base.ents["Q1046833"])
    mel["sitelinks"] = {"kawiki": {"site": "kawiki", "title": "გიორგი მელიქიშვილი"}}
    client = FakeClient(entities={"Q1046833": mel})
    out = M.run(con, fams, rules, qids, client)
    sh = out["გიორგ შარვაშიძ"]
    # two candidates born 1846 and died 1918 exist in Wikidata -> years alone cannot decide
    assert sh["status"] == "ambiguous" and sh["named_after"][0]["birth"] == 1846
    m = out["გიორგ მელიქიშვილ"]
    assert m["status"] == "unique" and m["chosen"] == "Q1046833" and "links" in m["reason"]
    # the article facts reach the dossier
    st = FA.run(con, load_families(con), rules, qids, out, client)
    rows = D.run(con, load_families(con), rules, qids, out, st, client, search=False)
    d = next(r["dossier"] for r in rows if r["meta"].get("family_key") == "გიორგ შარვაშიძ" and r["meta"]["kind"] == "queue")
    assert d["encyclopedia"][0]["named_after"]["death"] == 1918


def test_resolve_by_years_unit():
    d = {"status": "ambiguous", "chosen": None, "strong": [
        {"qid": "Q1", "born": 1850, "died": 1900}, {"qid": "Q2", "born": 1950, "died": 2010}]}
    M.resolve_by_articles(d, [{"name_ka": "x", "birth": 1851, "death": 1900}], set())
    assert d["status"] == "unique" and d["chosen"] == "Q1" and "years" in d["reason"]
