"""Person matching: label variants and preference rules (Georgian > kawiki > deceased, guards)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "enrich"))
from enrich_fake import FakeClient, ent, fixture, make_db  # noqa: E402

from pipeline.config import BUILD, ENRICH
from pipeline.enrich import gazetteer as G
from pipeline.enrich import ka as K
from pipeline.enrich import match as M
from pipeline.enrich import qids as Q
from pipeline.enrich import rules as R
from pipeline.enrich.common import Family, load_families, paths

GEORGIA = {"Q230"}


@pytest.fixture(autouse=True)
def tmp_paths(tmp_path):
    paths.set(tmp_path / "enrich", tmp_path / "build")
    yield
    paths.set(ENRICH, BUILD)


def fam(base, en=""):
    return Family(id=1, key=base, base_ka=base, base_ru="", base_en=en, nom_ka=K.nominative(base), kind_hint=None,
                  eponym_id=None)


def test_label_variants():
    v = K.ka_label_variants("ილია ჭავჭავაძის")
    assert v[0] == "ილია ჭავჭავაძე"
    assert "აკაკი წერეთელი" in K.ka_label_variants("აკაკი წერეთლის")       # syncope
    assert "ვახტანგ VI" in K.ka_label_variants("ვახტანგ მეექვსის")          # regnal word -> roman
    assert "ფარნავაზი" in K.ka_label_variants("მეფე ფარნავაზის")           # title dropped
    pairs = M.label_pairs(fam("ილია ჭავჭავაძის", "Ilia Chavchavadze"))
    assert ("ილია ჭავჭავაძე", "ka") in pairs and ("Ilia Chavchavadze", "en") in pairs


def humans(*es):
    return {e["id"]: e for e in es}


def run_decide(base, *es, extra=None):
    f = fam(base)
    ents = humans(*es)
    ents.update(extra or {})
    return M.decide(f, M.label_pairs(f), {e["id"] for e in es}, ents, GEORGIA)


NAME = "გიორგი ბერიძის"
LAB = "გიორგი ბერიძე"


def test_single_match_is_unique():
    d = run_decide(NAME, ent("Q11", ka=LAB, claims={"P31": ["Q5"]}, born=1900, died=1970))
    assert d["status"] == "unique" and d["chosen"] == "Q11" and d["reason"] == "single name match"


def test_only_georgian_wins():
    d = run_decide(NAME, ent("Q11", ka=LAB, claims={"P31": ["Q5"], "P27": ["Q159"]}, died=1950),
                   ent("Q12", ka=LAB, claims={"P31": ["Q5"], "P27": ["Q230"]}, died=1960))
    assert d["status"] == "unique" and d["chosen"] == "Q12" and "Georgian" in d["reason"]


def test_only_kawiki_wins_among_georgians():
    d = run_decide(NAME, ent("Q11", ka=LAB, claims={"P31": ["Q5"], "P27": ["Q230"]}),
                   ent("Q12", ka=LAB, claims={"P31": ["Q5"], "P27": ["Q230"]}, died=1960,
                       sitelinks={"kawiki": "გიორგი ბერიძე"}))
    assert d["status"] == "unique" and d["chosen"] == "Q12" and "kawiki" in d["reason"]


def test_kawiki_does_not_beat_a_deceased_georgian_namesake():
    # real case: ნიკოლოზ ყიფშიძე, physician (1887-1954, no kawiki) vs politician (1952-2024, kawiki)
    d = run_decide(NAME, ent("Q11", ka=LAB, claims={"P31": ["Q5"], "P27": ["Q230"]}, born=1887, died=1954),
                   ent("Q12", ka=LAB, claims={"P31": ["Q5"], "P27": ["Q230"]}, born=1952, died=2024,
                       sitelinks={"kawiki": "x"}))
    assert d["status"] == "ambiguous"


def test_only_deceased_wins_among_foreigners():
    d = run_decide(NAME, ent("Q11", ka=LAB, claims={"P31": ["Q5"]}, born=1950),
                   ent("Q12", ka=LAB, claims={"P31": ["Q5"]}, born=1880, died=1940))
    assert d["status"] == "unique" and d["chosen"] == "Q12" and "deceased" in d["reason"]


def test_two_georgian_namesakes_stay_ambiguous():
    kw = {"kawiki": "x"}
    d = run_decide(NAME, ent("Q11", ka=LAB, claims={"P31": ["Q5"], "P27": ["Q230"]}, died=1950, sitelinks=kw),
                   ent("Q12", ka=LAB, claims={"P31": ["Q5"], "P27": ["Q230"]}, died=1960, sitelinks=kw))
    assert d["status"] == "ambiguous" and d["n_georgian"] == 2 and d["chosen"] is None


def test_one_word_name_is_never_auto_resolved():
    d = run_decide("მარშალ გელოვანის", ent("Q11", ka="გელოვანი", claims={"P31": ["Q5"]}, died=1944))
    assert d["status"] == "ambiguous" and "single-token" in d["reason"]


def test_living_young_namesake_is_suspicious_and_too_young_dropped():
    d = run_decide(NAME, ent("Q11", ka=LAB, claims={"P31": ["Q5"]}, born=1975))
    assert d["status"] == "suspicious"
    d = run_decide(NAME, ent("Q11", ka=LAB, claims={"P31": ["Q5"]}, born=2001))
    assert d["status"] == "not_found"


def test_birthplace_in_georgia_counts_as_georgian():
    place = ent("Q500", ka="ქუთაისი", claims={"P17": ["Q230"]})
    d = run_decide(NAME, ent("Q11", ka=LAB, claims={"P31": ["Q5"], "P19": ["Q500"]}, died=1950),
                   ent("Q12", ka=LAB, claims={"P31": ["Q5"], "P27": ["Q30"]}, died=1960), extra={"Q500": place})
    assert d["chosen"] == "Q11"


def test_non_humans_and_label_mismatch_are_ignored():
    d = run_decide(NAME, ent("Q11", ka=LAB, claims={"P31": ["Q515"]}), ent("Q12", ka="სხვა კაცი", claims={"P31": ["Q5"]}))
    assert d["status"] == "not_found"


def test_run_on_fixture_with_control_set():
    con = make_db()
    fams = load_families(con)
    gaz = G.build(con, None, rows_by_name=fixture("gazetteer.json"))
    rules = R.run(con, fams, gaz)
    qids = Q.run(con, fams, None, street_rows=[])
    client = FakeClient()
    out = M.run(con, fams, rules, qids, client)
    assert out["გიორგ შარვაშიძ"]["status"] == "ambiguous"
    assert out["გიორგ შარვაშიძ"]["n_georgian"] >= 2
    assert out["გელოვნ მარშლ"]["status"] == "ambiguous"
    assert out["გივ კარტოზ"]["status"] == "unique" and out["გივ კარტოზ"]["chosen"] == "Q1371376"
    ctl = [d for d in out.values() if "control" in d]
    assert len(ctl) >= 10
    assert all(d["chosen"] == d["control"] for d in ctl if d["status"] == "unique")
    prov = con.execute("SELECT COUNT(*) FROM provenance WHERE source='wd_match' AND field='qid'").fetchone()[0]
    assert prov == sum(1 for d in out.values() if d["status"] == "unique" and "control" not in d)
    # idempotent: a second run replaces, not duplicates, provenance
    M.run(con, fams, rules, qids, client)
    assert con.execute("SELECT COUNT(*) FROM provenance WHERE source='wd_match'").fetchone()[0] == prov
