"""LLM queue (batches), JSON Schema and merge of history-extractor answers."""
import json
from pathlib import Path

import jsonschema
import pytest

from pipeline.db import open_db
from pipeline.history import batches, link, merge
from pipeline.history.store import replace_articles

FIX = json.loads((Path(__file__).parent / "fixtures" / "history" / "llm_batch_article.json").read_text(encoding="utf-8"))


@pytest.fixture()
def env(tmp_path):
    con = open_db(tmp_path / "t.sqlite")
    con.execute("INSERT INTO area(id, kind, ka) VALUES (1, 'district', 'ვაკის რაიონი')")
    con.execute("INSERT INTO feature(id, kind, ka, bbox) VALUES (1, 'street', 'მზიურის ქუჩა', '41.70,44.75,41.71,44.76')")
    con.execute("INSERT INTO feature_area(feature_id, area_id) VALUES (1, 1)")
    con.commit()
    rec = FIX["record"]
    replace_articles(con, [rec], ("enc2008", "enc2008_bio"))
    texts = {(rec["title"], rec["text_hash"]): FIX["text"]}
    bdir, rdir = tmp_path / "batches", tmp_path / "results"
    rdir.mkdir()
    yield con, texts, bdir, rdir
    con.close()


def test_schema_is_valid_and_strict():
    schema = merge.load_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(FIX["answer"], schema)
    bad = json.loads(json.dumps(FIX["answer"]))
    bad["results"][0]["former_names"][0]["extra"] = 1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
    bad = json.loads(json.dumps(FIX["answer"]))
    bad["results"][0]["evidence"] = ["x" * 101]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_abridge_keeps_naming_sentences():
    long = "ქუჩა, ვაკის რ-ნი; X-დან Y-მდე. " + "ქუჩაზე დგას სახლი. " * 120 + "1923 ბაღის ქუჩა ერქვა."
    out = batches.abridge(long, 300)
    assert len(out) <= 300 and out.startswith("ქუჩა, ვაკის რ-ნი") and "1923 ბაღის ქუჩა ერქვა." in out


def test_batches_then_merge(env):
    con, texts, bdir, rdir = env
    link.run(con)
    st = batches.run(con, texts, out_dir=bdir)
    assert st["queued"] == 1 and st["files"] == 1
    item = json.loads((bdir / "h001.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert item["id"] == FIX["answer"]["results"][0]["id"]
    assert item["candidates"][0]["feature_id"] == 1 and item["text"] == FIX["text"]

    (rdir / "h001.json").write_text(json.dumps(FIX["answer"], ensure_ascii=False), encoding="utf-8")
    ms = merge.run(con, results_dir=rdir, batch_dir=bdir)
    assert ms["accepted"] == 1 and ms["names"] == 3          # the invented name is dropped
    payload = json.loads(con.execute("SELECT payload FROM llm_result WHERE role='history'").fetchone()[0])
    assert [f["name_ka"] for f in payload["former_names"]] == ["ბაღნარის ქუჩა", "ტყის შესახვევი", "მუზეუმის ქუჩა"]
    rows = {r["name_ka"]: dict(r) for r in con.execute("SELECT * FROM name_history WHERE feature_id=1")}
    assert set(rows) == {"ბაღნარის ქუჩა", "ტყის შესახვევი", "მუზეუმის ქუჩა"}
    assert all(r["source"] == "enc2008_llm" for r in rows.values())
    assert (rows["მუზეუმის ქუჩა"]["from_year"], rows["მუზეუმის ქუჩა"]["to_year"]) == (1922, 1953)
    # re-running link keeps the LLM answer (stored in llm_result)
    link.run(con)
    assert con.execute("SELECT COUNT(*) FROM name_history WHERE source='enc2008_llm'").fetchone()[0] == 3


def test_merge_rejects_bad_files(env):
    con, texts, bdir, rdir = env
    link.run(con)
    batches.run(con, texts, out_dir=bdir)
    bad = json.loads(json.dumps(FIX["answer"]))
    bad["results"][0]["confidence"] = 2
    (rdir / "h001.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    st = merge.run(con, results_dir=rdir, batch_dir=bdir, relink=False)
    assert st["accepted"] == 0 and st["problems"] == 1
    other = json.loads(json.dumps(FIX["answer"]))
    other["results"][0]["id"] = "enc2008:1:zzz:უცნობი"
    (rdir / "h001.json").write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
    st = merge.run(con, results_dir=rdir, batch_dir=bdir, relink=False)
    assert st["accepted"] == 0 and st["rejected"] == 1
    assert con.execute("SELECT COUNT(*) FROM llm_result").fetchone()[0] == 0


def test_check_result_evidence_penalty():
    res = dict(FIX["answer"]["results"][0])
    res["evidence"] = ["this quote is not in the text"]
    clean, probs = merge.check_result(res, FIX["text"])
    assert clean["confidence"] == pytest.approx(0.65)
    assert any(p.startswith("evidence_not_found") for p in probs)
    assert any(p.startswith("name_not_in_text") for p in probs)
