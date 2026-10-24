"""KIND_TABLE must match the kinds table of docs/pack-format.md exactly (order = core.f[].k)."""
import re
from pathlib import Path

from pipeline.names.parse import (KA_TYPE_WORDS, KIND_CODES, KIND_GROUP, KIND_INDEX, KIND_TABLE, LANDMARK_KINDS, Kind,
                                  kinds_for_pack, parse_name)

DOC = Path(__file__).resolve().parent.parent / "docs" / "pack-format.md"

#: Kinds before the landmark layer; their indices are stored in older packs and must never move.
BASE_CODES = ["street", "avenue", "highway", "road", "embankment", "ascent", "descent", "alley", "row", "lane",
              "dead_end", "passage", "square", "bridge", "tunnel", "ravine", "garden", "park", "river", "stream",
              "canal", "lake", "metro", "district", "neighbourhood", "other"]


def _doc_kinds() -> list[tuple[str, tuple[str, ...], str, tuple[str, ...], str]]:
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## Kinds", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        if not line.startswith("| ") or line.startswith("| code") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        code, ka, ru_cell, group = cells
        ka_words = () if not ka or ka.startswith("(") else tuple(w.strip() for w in ka.split(","))
        m = re.fullmatch(r"(\S+)(?: \(синонимы?: (.*)\))?", ru_cell)
        assert m, ru_cell
        syn = tuple(s.strip() for s in m.group(2).split(",")) if m.group(2) else ()
        rows.append((code, ka_words, m.group(1), syn, group))
    return rows


def test_kind_table_matches_doc():
    doc = _doc_kinds()
    assert len(doc) == len(KIND_TABLE) == 48
    assert [tuple(k) for k in KIND_TABLE] == doc


def test_base_kind_indices_are_stable():
    assert KIND_CODES[:len(BASE_CODES)] == BASE_CODES
    assert KIND_INDEX["other"] == 25


def test_kind_helpers():
    assert KIND_CODES[0] == "street"
    assert KIND_INDEX["lane"] == 9
    assert KIND_GROUP["dead_end"] == "lane*"
    assert KIND_GROUP["square"] == ""
    assert all(isinstance(k, Kind) for k in KIND_TABLE)
    code, ka, ru, syn, group = KIND_TABLE[16]   # tuple unpacking in table column order
    assert (code, ka, ru, syn, group) == ("garden", ("სკვერი", "ბაღი"), "сквер", ("сад",), "")


def test_landmark_kinds():
    assert KIND_CODES[KIND_INDEX["other"] + 1] == "landmark"   # first of the group: autocomplete "место*"
    assert LANDMARK_KINDS == {c for c in KIND_CODES[26:]}
    assert all(KIND_GROUP[c] == "place*" for c in LANDMARK_KINDS)
    names = [w for k in KIND_TABLE for w in (k.ru, *k.syn)]
    assert len(names) == len(set(names)), "a Russian kind name or synonym is used twice"
    for k in KIND_TABLE:
        assert " " not in k.ru and all(" " not in s for s in k.syn), k.code


def test_landmark_words_do_not_change_street_parsing():
    for k in KIND_TABLE:
        if k.code in LANDMARK_KINDS:
            assert not set(k.ka) & set(KA_TYPE_WORDS), k.code
    assert parse_name("ცენტრალური ბაზარი").kind == "other"
    assert parse_name("ბაზრის ქუჩა").kind == "street"


def test_kinds_for_pack_shape():
    kinds = kinds_for_pack()
    assert kinds[0] == {"code": "street", "ka": "ქუჩა", "ru": "улица", "syn": [], "group": "street*"}
    assert kinds[KIND_INDEX["metro"]] == {"code": "metro", "ka": "", "ru": "метро", "syn": ["станция"], "group": ""}
    assert kinds[KIND_INDEX["museum"]] == {"code": "museum", "ka": "მუზეუმი", "ru": "музей", "syn": ["галерея"],
                                           "group": "place*"}
    assert [k["code"] for k in kinds] == KIND_CODES
