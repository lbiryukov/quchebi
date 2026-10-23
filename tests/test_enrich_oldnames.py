"""Former names: reuse of family eponyms, Wikidata match, LLM queue and merge; calibration; CLI."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "enrich"))
from enrich_fake import FakeClient, answer, ent, prepare, write_results  # noqa: E402

from pipeline.config import BUILD, ENRICH
from pipeline.enrich import batches as B
from pipeline.enrich import calibrate as C
from pipeline.enrich import merge as MG
from pipeline.enrich import oldnames as O
from pipeline.enrich.__main__ import main
from pipeline.enrich.common import agent_prompt_version, llm_cache_key, load_families, paths, read_jsonl
from pipeline.enrich.facts import EponymBuilder

ORJ = ent("Q193321", ka="სერგო ორჯონიკიძე", ru="Серго Орджоникидзе", claims={"P31": ["Q5"], "P27": ["Q15180"],
          "P106": ["Q82955"]}, born=1886, died=1937, desc_ru="советский государственный деятель")


@pytest.fixture()
def env(tmp_path):
    out = prepare(tmp_path, client=FakeClient(entities={"Q193321": ORJ}), with_batches=False)
    con = out["con"]
    pid = con.execute("SELECT f.id FROM feature f JOIN family m ON m.id=f.family_id WHERE m.key='ალექსანდრ პუშკინ'"
                      ).fetchone()[0]
    rows = [(pid, "სერგო ორჯონიკიძის ქუჩა", "улица Орджоникидзе", 1937, 1990, "enc2008"),
            (pid, "ალექსანდრე პუშკინის ქუჩა", None, 1900, 1937, "enc2008"),
            (pid, None, "улица Ленина", 1921, 1990, "ruwiki"),
            (pid, "იუნკერთა ქუჩა", None, 1918, 1921, "enc2008")]
    con.executemany("INSERT INTO name_history(feature_id, name_ka, name_ru, from_year, to_year, source) "
                    "VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    out["old"] = O.run(con, load_families(con), out["gaz"], out["client"])
    yield out
    paths.set(ENRICH, BUILD)


def nh(con, name_ka=None, name_ru=None):
    q = "SELECT e.qid, e.kind, e.source FROM name_history nh JOIN eponym e ON e.id=nh.eponym_id WHERE "
    row = con.execute(q + ("nh.name_ka=?" if name_ka else "nh.name_ru=?"), (name_ka or name_ru,)).fetchone()
    return tuple(row) if row else None


def test_old_names_linked_by_family_and_match(env):
    con = env["con"]
    assert nh(con, "ალექსანდრე პუშკინის ქუჩა") == ("Q7200", "person", "osm")
    assert nh(con, "სერგო ორჯონიკიძის ქუჩა") == ("Q193321", "person", "wd_match")
    assert nh(con, name_ru="улица Ленина") is None
    st = env["old"]
    assert any(v["status"] == "queue" for v in st.values())


def test_old_name_queue_dossier_and_merge(env):
    con = env["con"]
    rows = read_jsonl(paths.dossiers_old)
    lenin = next(r for r in rows if r["meta"]["old_key"].startswith("old-ru:"))
    assert lenin["meta"]["kind"] == "old_name" and lenin["meta"]["nh_ids"]
    assert lenin["dossier"]["entity"] == "old_name" and lenin["dossier"]["eponym_ru"] == "Ленина"
    allrows = env["dossiers"] + rows
    man = B.run(con, allrows, env["client"], roles=("classifier",))
    ob = [n for n in man["classifier"]["batches"] if n.startswith("o")]
    assert ob
    write_results(paths, "classifier", ob[0], [answer(lenin["dossier"]["family_id"], "person", None, 0.9, None,
                                                      ["power.revolutionary"], name_ru="Владимир Ленин")])
    st = MG.run(con, load_families(con), allrows, EponymBuilder(env["client"]))
    info = st["families"][lenin["dossier"]["family_id"]]
    # one word ("Ленина") is never accepted automatically, but the answer is stored for review
    assert info["entity"] == "old_name" and info["route"] == "manual" and "surname_only" in info["triggers"]
    row = con.execute("SELECT e.ru, e.source, e.key, e.needs_review FROM name_history nh "
                      "JOIN eponym e ON e.id=nh.eponym_id WHERE nh.name_ru='улица Ленина'").fetchone()
    assert tuple(row) == ("Владимир Ленин", "llm", lenin["meta"]["old_key"], 1)


def test_calibration_on_gold(env, tmp_path):
    con = env["con"]
    pv, model = agent_prompt_version("classifier")
    gold = [r for r in env["dossiers"] if r["meta"]["kind"] == "gold"]
    assert gold
    import json
    for i, r in enumerate(gold):
        d = r["dossier"]
        right = r["meta"]["gold_qid"] if any(c["qid"] == r["meta"]["gold_qid"] for c in d.get("candidates", [])) else None
        a = answer(d["family_id"], "person", right, 0.95 if right else 0.6, 0.95 if right else None, ["lit.poet"])
        con.execute("INSERT INTO llm_result(cache_key, role, model, family_key, batch, payload) VALUES (?,?,?,?,?,?)",
                    (llm_cache_key(pv, model, d), "classifier", model, r["meta"]["family_key"], "k001",
                     json.dumps({"answer": a})))
    con.commit()
    rep = C.run(con, env["dossiers"], apply=True)
    assert rep["gold_answered"] == len(gold)
    assert rep["default_eval"]["precision"] == 1.0
    assert rep["recommended"] is not None and paths.thresholds.exists()
    assert paths.calibration.exists()


def test_cli_missing_db_and_stats(tmp_path, capsys):
    assert main(["stats", "--db", str(tmp_path / "nope.sqlite")]) == 2
    from pipeline.db import open_db
    db = tmp_path / "x.sqlite"
    open_db(db).close()
    assert main(["merge", "--db", str(db), "--offline"]) == 2          # no families yet
    assert main(["stats", "--db", str(db), "--offline"]) == 0
    assert '"families": 0' in capsys.readouterr().out
