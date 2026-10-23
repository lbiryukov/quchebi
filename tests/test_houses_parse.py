"""House-number parsing: shared vectors, extra shapes seen in OSM, and a real-data check."""
import collections
import json
from pathlib import Path

import pytest

from pipeline.config import RAW
from pipeline.names.houses import HousePart, normalize_letter, parse_house

VECTORS = json.loads((Path(__file__).parent / "vectors" / "house_numbers.json").read_text(encoding="utf-8"))
ADDRESSES = RAW / "prototype_cache" / "overpass" / "addresses" / "addresses.json"


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: repr(c["raw"]))
def test_house_vectors(case):
    assert [p.as_dict() for p in parse_house(case["raw"])] == case["parts"]


def _one(raw):
    parts = parse_house(raw)
    assert len(parts) == 1, (raw, parts)
    return parts[0]


def test_normalize_letter():
    assert normalize_letter("a") == "ა"
    assert normalize_letter("А") == "ა"
    assert normalize_letter("ბ") == "ბ"
    assert normalize_letter("в") == "ვ"
    assert normalize_letter("V") == "ვ"
    assert normalize_letter("г") == "გ"
    assert normalize_letter("д") == "დ"
    assert normalize_letter("u") == "უ"
    assert normalize_letter("x") == "x"      # no Georgian equivalent in the rule: kept lower-cased
    assert normalize_letter("") == ""


@pytest.mark.parametrize("raw, n, lit, sub, n_to, corp, kind", [
    ("11 A", 11, "ა", "", None, "", "letter"),
    ("4а", 4, "ა", "", None, "", "letter"),
    ("N3", 3, "", "", None, "", "int"),
    ("#1", 1, "", "", None, "", "int"),
    ("№ 7", 7, "", "", None, "", "int"),
    ("6 (Archway 6)", 6, "", "", None, "", "int"),
    ("71/b", 71, "", "ბ", None, "", "fraction"),
    ("2/а", 2, "", "ა", None, "", "fraction"),
    ("34/2ა", 34, "", "2ა", None, "", "fraction"),
    ("15ბ/1გ", 15, "ბ", "1გ", None, "", "fraction"),
    ("3/14A", 3, "", "14ა", None, "", "fraction"),
    ("221 / 1ა", 221, "", "1ა", None, "", "fraction"),
    ("11/23/31", 11, "", "23/31", None, "", "fraction"),
    ("1\\2\\3", 1, "", "2/3", None, "", "fraction"),
    ("47ბ-5", 47, "ბ", "5", None, "", "fraction"),
    ("4-2a", 4, "", "2ა", None, "", "fraction"),
    ("19-21ა", 19, "", "", 21, "", "range"),
    ("48-50/8", 48, "", "8", 50, "", "range"),
    ("3 – 5", 3, "", "", 5, "", "range"),
    ("55 კორპ. 1", 55, "", "", None, "1", "corp"),
    ("2 ქორპ. 2", 2, "", "", None, "2", "corp"),
    ("18 კორპ 2", 18, "", "", None, "2", "corp"),
    ("5 კორპუსი 1", 5, "", "", None, "1", "corp"),
    ("265 \\ korp. 6", 265, "", "", None, "6", "corp"),
    ("14/16 კორპ. 2", 14, "", "16", None, "2", "corp"),
    ("87ა კორპ. 2", 87, "ა", "", None, "2", "corp"),
    ("101 ა კორპ. 2", 101, "ა", "", None, "2", "corp"),
    ("83 ბლოკი IV", 83, "", "", None, "IV", "corp"),
    ("36 ბლოკი A", 36, "", "", None, "A", "corp"),
    ("34 კორპ. 2ა", 34, "", "", None, "2ა", "corp"),
    ("2 ქორპ. 6-7", 2, "", "", None, "6-7", "corp"),
    ("24 Block A", 24, "", "", None, "A", "corp"),
    ("31 სახლი 2", 31, "", "", None, "2", "corp"),
])
def test_single_shapes(raw, n, lit, sub, n_to, corp, kind):
    assert _one(raw) == HousePart(n, lit, sub, n_to, corp, kind)


def test_lists_and_letter_pairs():
    assert [(p.n, p.lit, p.kind) for p in parse_house("5;5გ;5ა;5ბ")] == [
        (5, "", "list"), (5, "გ", "list"), (5, "ა", "list"), (5, "ბ", "list")]
    assert [(p.n, p.kind) for p in parse_house("54,56")] == [(54, "list"), (56, "list")]
    assert [(p.n, p.lit) for p in parse_house("34-34ბ")] == [(34, ""), (34, "ბ")]
    assert [p.n for p in parse_house("17-19-21")] == [17, 19, 21]
    assert [(p.n, p.lit) for p in parse_house("14-14ა-14ბ")] == [(14, ""), (14, "ა"), (14, "ბ")]
    mixed = parse_house("28-28ა;30-32")
    assert [(p.n, p.lit, p.n_to, p.kind) for p in mixed] == [
        (28, "", None, "list"), (28, "ა", None, "list"), (30, "", 32, "list")]


@pytest.mark.parametrize("raw", [
    "office 208", "15ბროწეულის ქუჩა", "", "   ", "-", "სახლი", "13კმ", "10 კილომეტრი", "13-th km",
    "V კორპუსი", "კორპ. 1", "ბ ბლოკი", "A3", "building 2a", "3, 9 floor, 25", "111, building 3",
    "12 th", "8'", "ll", "28მიმდებარედ", "11-ისმიმდებარედ", "1st lane 14", None,
])
def test_junk_returns_empty(raw):
    assert parse_house(raw) == []


@pytest.mark.skipif(not ADDRESSES.exists(), reason="prototype address cache not available")
def test_real_addresses_mostly_parse():
    data = json.loads(ADDRESSES.read_text(encoding="utf-8"))
    kinds = collections.Counter()
    junk = 0
    total = 0
    for e in data["elements"]:
        hn = e.get("tags", {}).get("addr:housenumber")
        if hn is None:
            continue
        total += 1
        parts = parse_house(hn)
        if not parts:
            junk += 1
        else:
            kinds[parts[0].kind] += 1
    assert total > 40_000
    assert junk / total < 0.005, (junk, total)
    # plan figures (prototype): ints ~35.9k, letters ~6.6k, fractions ~0.8k, corps ~0.3k
    assert kinds["int"] > 35_000
    assert kinds["letter"] > 6_000
    assert 600 < kinds["fraction"] < 1_200
    assert 250 < kinds["corp"] < 450
