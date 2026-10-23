"""Schema and copyright checks for the acceptance set (tests/acceptance/*.json).

Run: uv run pytest tests/acceptance
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TASKS = HERE / "runcity_tasks.json"
ADDRESSES = HERE / "runcity_addresses.json"
EXTRACTED = ROOT / "data" / "raw" / "runcity" / "extracted.json"

TASK_TYPES = {
    "eponym_occupation", "old_name", "rename_chain", "house_number", "corner", "direction",
    "letter_pattern", "number_in_name", "latin_sign", "square_or_park", "other",
}
CATEGORIES = {"Сфинкс", "Лев", "Грифон", "неизвестно"}
DSL_KEYS = {
    "имя", "n", "name", "тип", "t", "type", "район", "р", "d", "в", "in", "кто", "e", "who", "био", "bio",
    "век", "c", "пол", "g", "свой", "ge", "дом", "h", "домов", "hn", "x", "пересекает", "cross", "рядом",
    "near", "семья", "f", "fam", "№", "ord", "no", "было", "old", "длина", "len", "зона", "zone", "сорт", "sort",
}
TBILISI_BBOX = (41.60, 44.60, 41.86, 45.05)


@pytest.fixture(scope="module")
def doc() -> dict:
    return json.loads(TASKS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tasks(doc) -> list[dict]:
    return doc["tasks"]


def test_header(doc, tasks):
    assert doc["v"] == 1
    assert len(tasks) >= 60
    assert re.fullmatch(r"\d\d/\d\d/\d{4}", doc["built"])


def test_ids_unique_and_shaped(tasks):
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids))
    for t in tasks:
        assert re.fullmatch(r"[a-z0-9]+-\d+", t["id"]), t["id"]
        assert t["id"] == f"{t['event']}-{t['kp']}"


def test_fields(tasks):
    for t in tasks:
        tid = t["id"]
        assert t["task_type"] and set(t["task_type"]) <= TASK_TYPES, tid
        assert t["category"] in CATEGORIES, tid
        assert set(t["categories"]) <= CATEGORIES | {"Атлант"}, tid
        words = t["summary_ru"].split()
        assert 3 <= len(words) <= 25, f"{tid}: {len(words)} words"
        a = t["answer"]
        for k in ("street_ru", "street_ka", "house", "corner_with", "raw_address"):
            assert k in a, f"{tid}: answer.{k}"
        lat, lon = t["kp_point"]
        assert TBILISI_BBOX[0] <= lat <= TBILISI_BBOX[2] and TBILISI_BBOX[1] <= lon <= TBILISI_BBOX[3], tid
        assert isinstance(t["facts"], list) and all(isinstance(f, str) and f for f in t["facts"]), tid
        assert t["needs_media"] in (None, "image", "audio", "video"), tid


def test_solvable_consistency(tasks):
    for t in tasks:
        if t["solvable_by_search"]:
            assert t["unsolvable_reason"] is None, t["id"]
            assert t["queries"], t["id"]
            assert t["answer"]["street_ka"] or t["answer"]["street_ru"], t["id"]
        else:
            assert t["unsolvable_reason"], t["id"]
    assert sum(t["solvable_by_search"] for t in tasks) >= 60


KEY_RX = re.compile(r"(?:^|[\s(!\-])([^\s:=()\"/,~|!\-]+)\s*[:=]")


def test_queries_shape_and_keys(tasks):
    for t in tasks:
        for q in t["queries"]:
            assert q["q"].strip(), t["id"]
            assert q["mode"] in ("facts", "name"), t["id"]
            assert q["expects"] == "answer street in results", t["id"]
            assert isinstance(q["max_results_ok"], int) and 1 <= q["max_results_ok"] <= 100, t["id"]
            assert q["q"].count("(") == q["q"].count(")"), f"{t['id']}: {q['q']}"
            assert q["q"].count('"') % 2 == 0, f"{t['id']}: {q['q']}"
            text = re.sub(r"/[^/]*/", " ", q["q"])  # regex bodies may contain ':'
            for key in KEY_RX.findall(text):
                assert key.lower() in DSL_KEYS, f"{t['id']}: unknown DSL key {key!r} in {q['q']!r}"
        assert isinstance(t["dsl_keys"], list), t["id"]


def _words(s: str) -> list[str]:
    return re.findall(r"[0-9a-zа-яёა-ჰ]+", s.lower())


def _sh(ws: list[str], n: int = 6) -> set[tuple[str, ...]]:
    return {tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)}


@pytest.mark.skipif(not EXTRACTED.exists(), reason="raw Runcity texts are not downloaded (data/raw is local only)")
def test_no_verbatim_copy_of_riddles(tasks):
    ex = json.loads(EXTRACTED.read_text(encoding="utf-8"))
    by_id = {f"{ev}-{r['kp']}": r for ev, recs in ex.items() for r in recs}
    for t in tasks:
        src = by_id.get(t["id"])
        if not src:
            continue
        orig = _sh(_words(" ".join([src.get("description", ""), src.get("quest", ""), src.get("explanation", "")])))
        for field in [t["summary_ru"], *t["facts"]]:
            assert not (_sh(_words(field)) & orig), f"{t['id']}: verbatim fragment in {field!r}"


def test_addresses_file():
    if not ADDRESSES.exists():
        pytest.skip("runcity_addresses.json not built")
    d = json.loads(ADDRESSES.read_text(encoding="utf-8"))
    items = d["items"]
    assert len(items) >= 50
    ids = [x["id"] for x in items]
    assert len(ids) == len(set(ids))
    for x in items:
        assert x["answer"]["street_ka"], x["id"]
        assert x["queries"] and all(q["mode"] == "latin" for q in x["queries"]), x["id"]
        assert re.fullmatch(r"[a-z0-9' ]+( дом:\S+)?", x["queries"][0]["q"]), x["queries"][0]["q"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_runner_exits_zero_without_pack(tmp_path):
    r = subprocess.run(["node", str(HERE / "run_acceptance.mjs"), "--pack", str(tmp_path)],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Приёмка пропущена" in r.stdout
