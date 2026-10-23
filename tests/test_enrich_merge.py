"""merge: schema validation, thresholds, routing, cache, writing eponyms (hand-written answers)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "enrich"))
from enrich_fake import answer, dossier_of, prepare, write_results  # noqa: E402

from pipeline.config import BUILD, ENRICH
from pipeline.enrich import batches as B
from pipeline.enrich import merge as MG
from pipeline.enrich.common import load_families, paths, read_jsonl, write_json
from pipeline.enrich.facts import EponymBuilder


@pytest.fixture()
def env(tmp_path):
    out = prepare(tmp_path)
    yield out
    paths.set(ENRICH, BUILD)


def batch_of(fid_: str) -> str:
    for p in sorted((paths.batches / "classifier").glob("*.jsonl")):
        if any(d["family_id"] == fid_ for d in read_jsonl(p)):
            return p.stem
    raise KeyError(fid_)


def merge(env):
    con = env["con"]
    return MG.run(con, load_families(con), env["dossiers"], EponymBuilder(env["client"]))


def family_eponym(con, key):
    row = con.execute("SELECT e.* FROM family f JOIN eponym e ON e.id=f.eponym_id WHERE f.key=?", (key,)).fetchone()
    return dict(row) if row else None


def test_routes_and_written_eponyms(env):
    rows, con = env["dossiers"], env["con"]
    sh = dossier_of(rows, "გიორგ შარვაშიძ")
    br = dossier_of(rows, "ბრილებ")
    ge = dossier_of(rows, "გელოვნ მარშლ")
    ju = dossier_of(rows, "იუნკერთ")
    ber = dossier_of(rows, "ბერიაშვილ დავით")
    mo = dossier_of(rows, "მოჭიქულ")
    q_sh = sh["candidates"][0]["qid"]
    res = [
        answer(sh["family_id"], "person", q_sh, 0.92, 0.85, ["power.noble"]),            # namesakes -> manual
        answer(br["family_id"], "nature", None, 0.9, None, ["nature.plant"],
               eponyms=[]),                                                                 # accept
        answer(ge["family_id"], "person", ge["candidates"][0]["qid"], 0.9, 0.9, ["lit.poet"]),  # surname only
        answer(ju["family_id"], "group", None, 0.95, None, ["group.heroes"], eponyms=[]),  # accept
        answer(ber["family_id"], "person", "Q999999", 0.9, 0.9, ["sci.natural"]),          # qid not in candidates
        answer(mo["family_id"], "object", None, 0.7, None, ["concept.object"], eponyms=[]),  # second opinion
    ]
    for r in res:
        assert batch_of(r["family_id"]) == "c001"
    write_results(paths, "classifier", "c001", res)
    st = merge(env)
    fam = st["families"]
    assert fam[sh["family_id"]]["route"] == "manual" and "georgian_namesakes" in fam[sh["family_id"]]["triggers"]
    assert fam[br["family_id"]]["route"] == "accept"
    assert fam[ge["family_id"]]["route"] == "manual" and "surname_only" in fam[ge["family_id"]]["triggers"]
    assert fam[ju["family_id"]]["route"] == "accept"
    assert fam[ber["family_id"]]["route"] == "manual" and "qid_not_in_candidates" in fam[ber["family_id"]]["triggers"]
    assert fam[mo["family_id"]]["route"] == "second_opinion"
    # written rows
    e = family_eponym(con, "ბრილებ")
    assert e["source"] == "llm" and e["kind"] == "nature" and e["needs_review"] == 0 and e["key"] == "fam:ბრილებ"
    cats = [c for (c,) in con.execute("SELECT category FROM eponym_category WHERE eponym_id=?", (e["id"],))]
    assert cats == ["nature.plant"]
    e = family_eponym(con, "გიორგ შარვაშიძ")
    assert e["qid"] == q_sh and e["needs_review"] == 1 and e["source"] == "llm"
    assert e["ru"]                                   # facts come from Wikidata for the chosen QID
    e = family_eponym(con, "ბერიაშვილ დავით")
    assert e["qid"] is None and e["needs_review"] == 1       # foreign QID dropped
    assert family_eponym(con, "მოჭიქულ")["needs_review"] == 1  # written provisionally
    assert con.execute("SELECT COUNT(*) FROM llm_result WHERE role='classifier'").fetchone()[0] == 6
    assert con.execute("SELECT COUNT(*) FROM provenance WHERE source='llm'").fetchone()[0] == 6
    assert sh["family_id"] in st["review"]

    # disambiguator batch for the mid-confidence family, then its agreeing answer -> accept
    man = B.run(con, env["dossiers"], env["client"], roles=("disambiguator",))
    dis = man["disambiguator"]["batches"]
    assert len(dis) == 1 and all(b["n"] == 1 for b in dis.values())
    name = next(iter(dis))
    line = read_jsonl(paths.batches / "disambiguator" / f"{name}.jsonl")[0]
    assert line["classifier_answer"]["confidence"] == 0.7
    write_results(paths, "disambiguator", name,
                  [answer(mo["family_id"], "object", None, 0.9, None, ["concept.object"], eponyms=[])])
    st = merge(env)
    assert st["families"][mo["family_id"]]["route"] == "accept"
    assert family_eponym(con, "მოჭიქულ")["needs_review"] == 0


def test_rules_conflict_goes_to_verifier(env):
    rows, con = env["dossiers"], env["con"]
    d = dossier_of(rows, "ბერიაშვილ დავით")       # rules: person:full_name
    write_results(paths, "classifier", batch_of(d["family_id"]),
                  [answer(d["family_id"], "toponym", None, 0.95, None, ["place.ge_city"], eponyms=[],
                          toponym={"name_ru": "Бериашвили", "scope": "georgia", "wikidata_qid": None})])
    st = merge(env)
    assert st["families"][d["family_id"]]["route"] == "verify_conflict"
    man = B.run(con, env["dossiers"], env["client"], roles=("verifier",))
    vb = [n for n, b in man["verifier"]["batches"].items() if d["family_id"] in b["ids"]]
    assert vb
    vline = next(x for x in read_jsonl(paths.batches / "verifier" / f"{vb[0]}.jsonl") if x["family_id"] == d["family_id"])
    assert "queue_reasons" not in vline and "classifier_answer" not in vline
    # verifier disagrees (says person) -> manual
    write_results(paths, "verifier", vb[0], [answer(d["family_id"], "person", None, 0.8, None, ["none.unknown"],
                                                    evidence=[{"source": "name", "quote": "დავით"}])])
    st = merge(env)
    assert st["families"][d["family_id"]]["route"] == "manual"
    assert st["families"][d["family_id"]]["reason"] == "verifier_disagrees"


def test_invalid_results_are_rejected_item_by_item(env):
    rows = env["dossiers"]
    br = dossier_of(rows, "ბრილებ")
    ju = dossier_of(rows, "იუნკერთ")
    good = answer(ju["family_id"], "group", None, 0.95, None, ["group.heroes"], eponyms=[])
    bad = answer(br["family_id"], "nature", None, 1.5, None, ["nature.plant"], eponyms=[])   # confidence > 1
    unknown_cat = answer(br["family_id"], "nature", None, 0.9, None, ["nature.tree"], eponyms=[])
    stranger = answer("Fdeadbeef", "group", None, 0.9, None, ["group.heroes"], eponyms=[])
    write_results(paths, "classifier", "c001", [good, bad, unknown_cat, stranger])
    stats = MG.ingest(env["con"], "classifier", keys=MG.dossier_keys(rows))
    assert stats["items_ok"] == 1 and stats["items_invalid"] == 2 and stats["items_unknown_id"] == 1
    assert stats["files_with_errors"] == 1
    write_results(paths, "classifier", "c002", [], raw="{not json")
    (paths.batches / "classifier" / "c002.jsonl").write_text(json.dumps({"family_id": "F00000000"}) + "\n",
                                                             encoding="utf-8")
    assert MG.ingest(env["con"], "classifier")["bad_files"] == 1
    # the stored family_key is the real family key, not the dossier id
    assert env["con"].execute("SELECT family_key FROM llm_result").fetchone()[0] == "იუნკერთ"


def test_cache_survives_rerun_and_batches_skip_answered(env):
    rows, con = env["dossiers"], env["con"]
    ju = dossier_of(rows, "იუნკერთ")
    write_results(paths, "classifier", "c001",
                  [answer(ju["family_id"], "group", None, 0.95, None, ["group.heroes"], eponyms=[])])
    merge(env)
    man = B.run(con, rows, env["client"], roles=("classifier",))      # archives result files
    assert all(ju["family_id"] not in b["ids"] for b in man["classifier"]["batches"].values())
    st = merge(env)                                                   # answer still known from llm_result
    assert st["families"][ju["family_id"]]["route"] == "accept"
    assert family_eponym(con, "იუნკერთ")["kind"] == "group"


def test_thresholds_file_overrides_defaults(env):
    rows = env["dossiers"]
    ju = dossier_of(rows, "იუნკერთ")
    write_results(paths, "classifier", "c001",
                  [answer(ju["family_id"], "group", None, 0.88, None, ["group.heroes"], eponyms=[])])
    assert merge(env)["families"][ju["family_id"]]["route"] == "accept"
    write_json(paths.thresholds, {"accept": 0.9, "qid_accept": 0.8, "second_low": 0.6})
    assert merge(env)["families"][ju["family_id"]]["route"] == "second_opinion"


def test_route_unit_rules():
    t = MG.DEFAULT_THRESHOLDS
    d = {"candidates": [{"qid": "Q1"}, {"qid": "Q2"}]}
    meta = {"family_key": "x", "rules_hint": "person:full_name", "n_georgian_candidates": 0}
    a = answer("F00000001", "person", "Q1", 0.95, 0.9, ["lit.poet"])
    assert MG.route(meta, a, d, t, None, None)["route"] == "accept"
    a = answer("F00000001", "person", "Q1", 0.95, 0.7, ["lit.poet"])
    assert MG.route(meta, a, d, t, None, None)["route"] == "second_opinion"       # weak QID
    a = answer("F00000001", "person", "Q1", 0.5, 0.9, ["lit.poet"])
    assert MG.route(meta, a, d, t, None, None)["route"] == "manual"
    a = answer("F00000001", "person", None, 0.9, None, ["lit.poet"], evidence=[{"source": "knowledge", "quote": "x"}])
    r = MG.route(meta, a, d, t, None, None)
    assert r["route"] == "manual" and "knowledge_without_qid" in r["triggers"]
    a = answer("F00000001", "person", "Q1", 0.95, 0.9, ["place.ge_city"])
    assert "kind_category_mismatch" in MG.route(meta, a, d, t, None, None)["triggers"]
    a = answer("F00000001", "person", "Q1", 0.95, 0.9, ["lit.poet"], needs_review=True,
               review_reason="namesake_ambiguity")
    assert MG.route(meta, a, d, t, None, None)["route"] == "manual"
    meta2 = dict(meta, flags=["ka_ru_en_mismatch"])
    a = answer("F00000001", "person", "Q1", 0.95, 0.9, ["lit.poet"])
    assert MG.route(meta2, a, d, t, None, None)["reason"] == "ka_ru_en_mismatch"
    # disambiguator disagreeing -> manual
    a = answer("F00000001", "person", "Q1", 0.7, 0.9, ["lit.poet"])
    dis = answer("F00000001", "person", "Q2", 0.9, 0.9, ["lit.poet"])
    assert MG.route(meta, a, d, t, dis, None)["reason"] == "disambiguator_disagrees"


def test_translation_ru_is_stored_in_provenance(env):
    rows, con = env["dossiers"], env["con"]
    mo = dossier_of(rows, "მოჭიქულ")
    a = answer(mo["family_id"], "object", None, 0.9, None, ["concept.object"], eponyms=[])
    a["translation_ru"] = "Глазурованная"
    write_results(paths, "classifier", batch_of(mo["family_id"]), [a])
    merge(env)
    row = con.execute("SELECT value, source FROM provenance WHERE entity='family' AND entity_key=? "
                      "AND field='translation_ru'", ("მოჭიქულ",)).fetchone()
    assert tuple(row) == ("Глазурованная", "llm")
    merge(env)                                       # idempotent
    assert con.execute("SELECT COUNT(*) FROM provenance WHERE field='translation_ru'").fetchone()[0] == 1
