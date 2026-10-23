"""Review page and application of review decisions."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "enrich"))
from enrich_fake import answer, dossier_of, prepare, write_results  # noqa: E402

from pipeline.config import BUILD, ENRICH
from pipeline.enrich import merge as MG
from pipeline.enrich import review as RV
from pipeline.enrich.common import load_families, paths, write_json
from pipeline.enrich.facts import EponymBuilder


@pytest.fixture()
def env(tmp_path):
    out = prepare(tmp_path)
    rows = out["dossiers"]
    sh = dossier_of(rows, "გიორგ შარვაშიძ")
    ge = dossier_of(rows, "გელოვნ მარშლ")
    br = dossier_of(rows, "ბრილებ")
    write_results(paths, "classifier", "c001", [
        answer(sh["family_id"], "person", sh["candidates"][0]["qid"], 0.92, 0.85, ["power.noble"]),
        answer(ge["family_id"], "person", ge["candidates"][0]["qid"], 0.9, 0.9, ["lit.poet"]),
        answer(br["family_id"], "nature", None, 0.9, None, ["nature.plant"], eponyms=[]),
    ])
    out["builder"] = EponymBuilder(out["client"])
    out["merge"] = MG.run(out["con"], load_families(out["con"]), rows, out["builder"])
    out["sh"], out["ge"], out["br"] = sh, ge, br
    yield out
    paths.set(ENRICH, BUILD)


def fe(con, key):
    row = con.execute("SELECT e.* FROM family f JOIN eponym e ON e.id=f.eponym_id WHERE f.key=?", (key,)).fetchone()
    return dict(row) if row else None


def rerun_merge(env):
    return MG.run(env["con"], load_families(env["con"]), env["dossiers"], env["builder"])


def test_review_page_is_self_contained(env):
    r = RV.run(env["con"], [paths.dossiers, paths.dossiers_old])
    html = Path(r["path"]).read_text(encoding="utf-8")
    assert r["items"] >= 2
    assert "Скопировать JSON" in html and "Проверка эпонимов" in html
    assert not re.search(r"<(script|link|img)[^>]+(src|href)=", html)
    data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1))
    keys = {it["key"] for it in data["items"]}
    assert {"გიორგ შარვაშიძ", "გელოვნ მარშლ"} <= keys
    item = next(it for it in data["items"] if it["key"] == "გიორგ შარვაშიძ")
    assert item["candidates"] and item["proposal"]["qid"] == env["sh"]["candidates"][0]["qid"]
    assert len(data["categories"]) == 66


def test_accept_fix_reject_are_applied_and_survive_merge(env):
    con = env["con"]
    sh_q = env["sh"]["candidates"][0]["qid"]
    other = env["sh"]["candidates"][1]["qid"]
    decisions = [
        {"entity": "family", "entity_key": "გელოვნ მარშლ", "decision": "reject", "payload": {},
         "decided_at": "24/10/2026"},
        {"entity": "family", "entity_key": "გიორგ შარვაშიძ", "decision": "fix",
         "payload": {"qid": other, "kind": "person", "categories": ["power.noble"], "short_ru": "князь"}},
        {"entity": "family", "entity_key": "ბრილებ", "decision": "accept", "payload": {"qid": None, "kind": "nature"}},
        {"entity": "family", "entity_key": "no such family", "decision": "accept", "payload": {}},
        {"entity": "family", "entity_key": "x", "decision": "maybe"},          # malformed: skipped
    ]
    write_json(paths.decisions, decisions)
    st = rerun_merge(env)
    assert st["families"][env["sh"]["family_id"]]["route"] == "manual"   # the LLM route itself is unchanged
    assert fe(con, "გელოვნ მარშლ") is None
    e = fe(con, "გიორგ შარვაშიძ")
    assert e["qid"] == other and e["source"] == "manual" and e["needs_review"] == 0 and e["short_ru"] == "князь"
    assert e["qid"] != sh_q
    cats = [c for (c,) in con.execute("SELECT category FROM eponym_category WHERE eponym_id=?", (e["id"],))]
    assert cats == ["power.noble"]
    assert fe(con, "ბრილებ")["needs_review"] == 0
    assert con.execute("SELECT COUNT(*) FROM review_decision").fetchone()[0] == 4
    assert con.execute("SELECT COUNT(*) FROM provenance WHERE source='manual'").fetchone()[0] == 3


def test_stale_accept_is_ignored(env):
    con = env["con"]
    write_json(paths.decisions, [{"entity": "family", "entity_key": "გიორგ შარვაშიძ", "decision": "accept",
                                  "payload": {"qid": "Q1", "kind": "person"}}])
    rerun_merge(env)
    assert fe(con, "გიორგ შარვაშიძ")["needs_review"] == 1


def test_manual_fix_without_qid(env):
    con = env["con"]
    write_json(paths.decisions, [{"entity": "family", "entity_key": "ბრილებ", "decision": "fix",
                                  "payload": {"kind": "concept", "ru": "брильянты", "categories": ["nature.material"]}}])
    rerun_merge(env)
    e = fe(con, "ბრილებ")
    assert e["key"] == "manual:family:ბრილებ" and e["kind"] == "concept" and e["ru"] == "брильянты"
