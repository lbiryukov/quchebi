"""JSON Schemas of the subagent answers (strict, draft 2020-12).

``classifier_output.schema.json`` is shared by the classifier, the disambiguator and the blind
verifier; the category enum is generated from ``taxonomy.json`` so the schema and the taxonomy
cannot drift apart (``uv run python -m pipeline.enrich schemas`` rewrites the files, a test
checks they are current).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

from pipeline.enrich.common import SCHEMA_DIR
from pipeline.enrich.taxonomy import category_ids

KINDS = ["person", "persons_multi", "group", "fictional_character", "toponym", "event_date", "nature", "concept",
         "object", "no_eponym", "unknown"]
REVIEW_REASONS = ["namesake_ambiguity", "surname_only", "no_source", "conflicting_sources", "low_confidence",
                  "multi_eponym", "osm_tag_error", "not_a_name"]
EVIDENCE_SOURCES = ["name", "name_ru", "name_en", "old_name", "family", "rules", "wikidata", "kawiki", "ruwiki",
                    "encyclopedia", "knowledge"]
QA_ISSUES = ["ka_ru_mismatch", "ka_en_mismatch", "typo_ka", "typo_ru", "typo_en", "addr_typo", "wrong_type",
             "ordinal_mismatch", "ok"]


def classifier_schema() -> dict:
    cats = category_ids()
    eponym = {
        "type": "object", "additionalProperties": False,
        "required": ["name_ka", "name_ru", "wikidata_qid", "qid_choice", "gender", "birth_year", "death_year",
                     "origin", "short_ru"],
        "properties": {
            "name_ka": {"type": "string", "maxLength": 120,
                        "description": "nominative, e.g. 'ლევან ასათიანი'; empty string if unknown"},
            "name_ru": {"type": "string", "maxLength": 120, "description": "nominative, e.g. 'Леван Асатиани'"},
            "wikidata_qid": {"type": ["string", "null"], "pattern": "^Q[1-9][0-9]*$",
                             "description": "ONLY a qid listed in the dossier candidates, else null"},
            "qid_choice": {"type": "string",
                           "enum": ["only_candidate", "picked_among_candidates", "none_fit", "no_candidates"]},
            "gender": {"type": ["string", "null"], "enum": ["m", "f", None]},
            "birth_year": {"type": ["integer", "null"], "minimum": -3000, "maximum": 2030},
            "death_year": {"type": ["integer", "null"], "minimum": -3000, "maximum": 2030},
            "origin": {"type": "string", "enum": ["georgian", "foreign", "unknown"]},
            "short_ru": {"type": "string", "maxLength": 160,
                         "description": "one line for the card, own words, with notable works when known: "
                                        "'грузинский педагог, автор букваря «Деда эна»'"},
        },
    }
    item = {
        "type": "object", "additionalProperties": False,
        "required": ["family_id", "eponym_kind", "eponyms", "categories", "confidence", "qid_confidence",
                     "evidence", "needs_review", "review_reason"],
        "properties": {
            "family_id": {"type": "string", "pattern": "^F[0-9a-f]{8}$", "description": "echo of the dossier id"},
            "eponym_kind": {"type": "string", "enum": KINDS},
            "eponyms": {"type": "array", "maxItems": 6, "items": eponym,
                        "description": "0..N eponyms; two for 'ლეო და ნოდარ გაბუნიების ქუჩა'"},
            "categories": {"type": "array", "minItems": 1, "maxItems": 4, "uniqueItems": True,
                           "items": {"type": "string", "enum": cats},
                           "description": "second-level taxonomy ids, main first"},
            "toponym": {
                "type": ["object", "null"], "additionalProperties": False,
                "required": ["name_ru", "scope", "wikidata_qid"],
                "properties": {
                    "name_ru": {"type": "string", "maxLength": 120},
                    "scope": {"type": "string", "enum": ["tbilisi", "georgia", "historical_georgia", "abroad"]},
                    "wikidata_qid": {"type": ["string", "null"], "pattern": "^Q[1-9][0-9]*$"},
                },
            },
            "translation_ru": {"type": ["string", "null"], "maxLength": 120,
                               "description": "for common-word eponyms (concept, nature, object, toponym that "
                                              "is a common word): literal Russian translation of the name, "
                                              "e.g. ვერცხლის -> 'Серебряная', რკინის რიგი -> 'Железный ряд'"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1,
                           "description": "probability that eponym_kind + categories are right"},
            "qid_confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1,
                               "description": "probability that the chosen QID is right; null without a QID"},
            "evidence": {
                "type": "array", "maxItems": 4,
                "items": {"type": "object", "additionalProperties": False, "required": ["source", "quote"],
                          "properties": {"source": {"type": "string", "enum": EVIDENCE_SOURCES},
                                         "quote": {"type": "string", "maxLength": 200}}},
            },
            "needs_review": {"type": "boolean"},
            "review_reason": {"type": ["string", "null"], "enum": REVIEW_REASONS + [None]},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "quchebi/eponym-classification/v2",
        "title": "EponymClassification",
        "description": "Answer of eponym-classifier / eponym-disambiguator / eponym-verifier for one batch file.",
        "type": "object", "additionalProperties": False, "required": ["results"],
        "properties": {
            "batch": {"type": "string", "maxLength": 40},
            "role": {"type": "string", "enum": ["classifier", "disambiguator", "verifier"]},
            "results": {"type": "array", "items": {"$ref": "#/$defs/item"}},
        },
        "$defs": {"item": item},
    }


def name_qa_schema() -> dict:
    item = {
        "type": "object", "additionalProperties": False,
        "required": ["item_id", "issue", "field", "current", "suggested", "confidence", "evidence", "osm_fix"],
        "properties": {
            "item_id": {"type": "string", "pattern": "^N[0-9a-f]{8}$"},
            "issue": {"type": "string", "enum": QA_ISSUES},
            "field": {"type": "string", "enum": ["ka", "ru", "en", "addr_street", "none"]},
            "current": {"type": "string", "maxLength": 200},
            "suggested": {"type": ["string", "null"], "maxLength": 200},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence": {"type": "string", "maxLength": 200},
            "osm_fix": {"type": "boolean", "description": "true if the fix belongs in OpenStreetMap"},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "quchebi/name-qa/v1",
        "title": "NameQA",
        "type": "object", "additionalProperties": False, "required": ["results"],
        "properties": {"batch": {"type": "string", "maxLength": 40}, "role": {"type": "string", "enum": ["name_qa"]},
                       "results": {"type": "array", "items": item}},
    }


FILES = {"classifier_output.schema.json": classifier_schema, "name_qa_output.schema.json": name_qa_schema}


def write_all(dst: Path = SCHEMA_DIR) -> list[Path]:
    dst.mkdir(parents=True, exist_ok=True)
    out = []
    for name, fn in FILES.items():
        p = dst / name
        p.write_text(json.dumps(fn(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        out.append(p)
    load.cache_clear()
    return out


@lru_cache(maxsize=4)
def load(name: str) -> dict:
    p = SCHEMA_DIR / name
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return FILES[name]()


def validator(role: str) -> jsonschema.Draft202012Validator:
    name = "name_qa_output.schema.json" if role == "name_qa" else "classifier_output.schema.json"
    return jsonschema.Draft202012Validator(load(name))


def item_validator(role: str) -> jsonschema.Draft202012Validator:
    """Validator of a single result item (used to salvage valid items from an invalid file)."""
    s = load("name_qa_output.schema.json" if role == "name_qa" else "classifier_output.schema.json")
    if role == "name_qa":
        sub = dict(s["properties"]["results"]["items"])
    else:
        sub = dict(s["$defs"]["item"])
        sub["$defs"] = s["$defs"]
    sub["$schema"] = s["$schema"]
    return jsonschema.Draft202012Validator(sub)
