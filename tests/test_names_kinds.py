"""KIND_TABLE must match the kinds table of docs/pack-format.md exactly (order = core.f[].k)."""
import re
from pathlib import Path

from pipeline.names.parse import KIND_CODES, KIND_GROUP, KIND_INDEX, KIND_TABLE, Kind, kinds_for_pack

DOC = Path(__file__).resolve().parent.parent / "docs" / "pack-format.md"


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
    assert len(doc) == 26
    assert [tuple(k) for k in KIND_TABLE] == doc


def test_kind_helpers():
    assert KIND_CODES[0] == "street" and KIND_CODES[-1] == "other"
    assert KIND_INDEX["lane"] == 9
    assert KIND_GROUP["dead_end"] == "lane*"
    assert KIND_GROUP["square"] == ""
    assert all(isinstance(k, Kind) for k in KIND_TABLE)
    code, ka, ru, syn, group = KIND_TABLE[16]   # tuple unpacking in table column order
    assert (code, ka, ru, syn, group) == ("garden", ("სკვერი", "ბაღი"), "сквер", ("сад",), "")


def test_kinds_for_pack_shape():
    kinds = kinds_for_pack()
    assert kinds[0] == {"code": "street", "ka": "ქუჩა", "ru": "улица", "syn": [], "group": "street*"}
    assert kinds[KIND_INDEX["metro"]] == {"code": "metro", "ka": "", "ru": "метро", "syn": ["станция"], "group": ""}
    assert [k["code"] for k in kinds] == KIND_CODES
