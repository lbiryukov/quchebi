"""Taxonomy file, Wikidata -> category mapping, schema/agent prompts kept in sync."""
from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

from pipeline.enrich import schemas
from pipeline.enrich.common import AGENTS_DIR, AGENT_FILES
from pipeline.enrich.taxonomy import load, norm_alias

ROOT = Path(__file__).resolve().parent.parent


def claim(*vals):
    return [{"mainsnak": {"datavalue": {"type": "wikibase-entityid", "value": {"id": v}}}, "rank": "normal"}
            for v in vals]


def entity(**props):
    return {"claims": {p: claim(*v) for p, v in props.items()}}


def direct(vals):
    return {v: frozenset({v}) for v in vals}


def test_aliases_unique_and_distinct_from_kind_aliases():
    t = load()
    seen = {}
    for g in t.raw["categories"]:
        for node in [g] + g["children"]:
            for a in node["aliases"]:
                k = norm_alias(a)
                assert k not in seen, f"alias {a!r} used by {seen.get(k)} and {node['id']}"
                seen[k] = node["id"]
    for kind, al in t.raw["kinds"].items():
        for a in al:
            assert norm_alias(a) not in seen, f"kind alias {a!r} collides with {seen.get(norm_alias(a))}"


def test_every_rule_and_heuristic_points_to_a_category():
    t = load()
    for r in t.raw["rules"]:
        assert r["cat"] is None or r["cat"] in t.ids, r
        assert r.get("georgia_cat") in (None, *t.ids), r
        for c in r.get("exclude_if_also", []):
            assert c in t.ids, r
    for hint, cats in t.raw["heuristic_map"].items():
        assert all(c in t.ids for c in cats), hint
    assert all(c in t.ids for c in t.raw["title_categories"].values())


def test_dsl_aliases_resolve():
    t = load()
    assert t.resolve("поэтесса") == ["lit.poet"]
    assert t.resolve("Поэты") == ["lit.poet"]
    assert t.resolve("писатель") == ["lit.writer"]
    assert t.resolve("актер") == ["art.actor"]           # ё = е
    assert set(t.resolve("наука")) == {c for c in t.ids if c.startswith("sci.")}
    assert set(t.resolve("lit")) == {c for c in t.ids if c.startswith("lit.")}
    assert t.resolve("хормейстер") == []                  # third level: raw occupations, not taxonomy
    assert t.resolve("lit.poet") == ["lit.poet"]


def test_writer_is_dropped_next_to_poet_and_musician_next_to_composer():
    t = load()
    cats, why = t.categorize(entity(P106=["Q49757", "Q36180"]), direct)
    assert cats == ["lit.poet"]
    assert "drop:lit.writer" in why
    cats, _ = t.categorize(entity(P106=["Q639669", "Q36834"]), direct)
    assert cats == ["art.composer"]


def test_multi_label_and_main_category_order():
    t = load()
    # king who is a saint and died in battle: P106 monarch first, then P411 saint, then P1196
    cats, _ = t.categorize(entity(P106=["Q116"], P411=["Q43115"], P1196=["Q210392"]), direct)
    assert cats == ["power.monarch", "rel.saint", "mil.hero"]


def test_subclass_closure_maps_third_level_occupations():
    t = load()
    anc = {"Q900001": frozenset({"Q900001", "Q158852", "Q639669"})}   # хормейстер -> дирижёр -> музыкант
    cats, _ = t.categorize(entity(P106=["Q900001"]), lambda vals: {v: anc.get(v, frozenset({v})) for v in vals})
    assert cats == ["art.musician"]


def test_georgia_specific_place_category():
    t = load()
    assert t.categorize(entity(P31=["Q515"]), direct, in_georgia=True)[0] == ["place.ge_city"]
    assert t.categorize(entity(P31=["Q515"]), direct, in_georgia=False)[0] == ["place.abroad_city"]
    assert t.kind_for_categories(["place.ge_city"]) == "toponym"
    assert t.kind_for_categories(["concept.object"]) == "object"
    assert t.kind_for_categories(["lit.character"]) == "fictional_character"


def test_schema_files_are_current():
    for name, fn in schemas.FILES.items():
        on_disk = json.loads((ROOT / "pipeline/enrich/schemas" / name).read_text(encoding="utf-8"))
        assert on_disk == fn(), f"{name} is stale: run `uv run python -m pipeline.enrich schemas`"
        jsonschema.Draft202012Validator.check_schema(on_disk)


def test_agent_prompts_frontmatter_and_categories():
    ids = load().ids
    for role, fname in AGENT_FILES.items():
        txt = (AGENTS_DIR / fname).read_text(encoding="utf-8")
        head = txt.split("---")[1]
        assert re.search(r"^name:\s*" + re.escape(fname[:-3]) + r"\s*$", head, re.M)
        assert re.search(r"^tools:\s*Read, Write\s*$", head, re.M)
        assert re.search(r"^model:\s*haiku\s*$", head, re.M)
        assert re.search(r"^description:\s*\S", head, re.M)
        assert f"data/enrich/results/{role}/" in txt
        if role != "name_qa":
            for c in ids:
                assert f"`{c}`" in txt, f"{fname} misses category {c}"
            assert "classifier_output.schema.json" in txt
        else:
            assert "name_qa_output.schema.json" in txt
