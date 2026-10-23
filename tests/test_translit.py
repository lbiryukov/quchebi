"""Transliteration: shared vectors (must match app/src/search/normalize.js) plus extra cases."""
import json
from pathlib import Path

import pytest

from pipeline.names.translit import ka_to_lat, ka_to_ru, skeleton

VECTORS = json.loads((Path(__file__).parent / "vectors" / "translit.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", VECTORS["skeleton"], ids=lambda c: c["in"])
def test_skeleton_vectors(case):
    assert skeleton(case["in"]) == case["out"]


@pytest.mark.parametrize("case", VECTORS["ka_to_ru"], ids=lambda c: c["in"])
def test_ka_to_ru_vectors(case):
    assert ka_to_ru(case["in"]) == case["out"]


def test_skeleton_cross_script_equality():
    # the whole point of the skeleton: ka / ru / lat spellings meet
    assert skeleton("ჭავჭავაძე") == skeleton("Чавчавадзе") == skeleton("Chavchavadze")
    assert skeleton("ყაზბეგი") == skeleton("Qazbegi") == skeleton("Казбеги")
    assert skeleton("აღმაშენებელი") == skeleton("Aghmashenebeli") == skeleton("Агмашенебели")
    assert skeleton("ჯანსუღ") == skeleton("Джансуг") == skeleton("Jansug")


def test_skeleton_edge_cases():
    assert skeleton("") == ""
    assert skeleton(None) == ""  # type: ignore[arg-type]
    assert skeleton("  -- ,. ") == ""
    assert skeleton("№ 12") == "12"
    assert skeleton("Ёж") == "ezh"
    assert skeleton("Wake") == "vake"
    assert skeleton("Yerevan") == "ierevan"
    # Mtavruli and Mkhedruli give the same skeleton
    assert skeleton("ᲥᲣᲩᲐ") == skeleton("ქუჩა") == "kucha"


def test_skeleton_is_idempotent():
    for case in VECTORS["skeleton"]:
        once = skeleton(case["in"])
        assert skeleton(once) == once


def test_ka_to_ru_extra():
    assert ka_to_ru("ერეკლე") == "эрекле"
    assert ka_to_ru("ლაშა-ერეკლე") == "лаша-эрекле"   # word-initial after a hyphen
    assert ka_to_ru("მეტეხი") == "метехи"             # not word-initial
    assert ka_to_ru("ᲛᲔᲢᲔᲮᲘ") == "метехи"             # Mtavruli
    assert ka_to_ru("ქ. 5") == "к. 5"
    assert ka_to_ru("") == ""


def test_ka_to_lat_national_system():
    assert ka_to_lat("ჭავჭავაძე") == "chavchavadze"
    assert ka_to_lat("წყალტუბო") == "tsqaltubo"
    assert ka_to_lat("აღმაშენებელი") == "aghmashenebeli"
    assert ka_to_lat("ჟორდანია") == "zhordania"
    assert ka_to_lat("ჯავახიშვილი") == "javakhishvili"
    assert ka_to_lat("ჰერეთი") == "hereti"
    assert ka_to_lat("ძმები") == "dzmebi"
    assert ka_to_lat("ილია ჭავჭავაძის გამზირი", capitalize=True) == "Ilia Chavchavadzis Gamziri"
    assert ka_to_lat("ვაჟა-ფშაველა", capitalize=True) == "Vazha-Pshavela"
    assert ka_to_lat("26 მაისი", capitalize=True) == "26 Maisi"
    assert ka_to_lat("ᲥᲣᲩᲐ") == "kucha"
    assert ka_to_lat("") == ""


def test_skeleton_table_is_exactly_modern_alphabets():
    from pipeline.names.translit import SKELETON_TABLE

    # 33 Mkhedruli + 33 Russian letters: what app/src/search/normalize.js must mirror
    assert len(SKELETON_TABLE) == 66
    assert SKELETON_TABLE["ღ"] == "gh" and SKELETON_TABLE["ყ"] == "q" and SKELETON_TABLE["щ"] == "shch"
