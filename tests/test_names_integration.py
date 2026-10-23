"""Integration test on the real OSM names (prototype Overpass cache, ~13k named highway ways).

Reference figures from the prototype (temp_scripts/prototypes/etymology/build_families.py):
4 586 unique names -> 2 278 families.
"""
import collections
import json
import time

import pytest

from pipeline.config import RAW
from pipeline.log import get_logger
from pipeline.names.families import group_families
from pipeline.names.parse import KIND_CODES, _parse_cached, parse_name

log = get_logger(__name__)
SRC = RAW / "prototype_cache" / "overpass_named_highways_tags.json"

pytestmark = pytest.mark.skipif(not SRC.exists(), reason="prototype Overpass cache not available")


@pytest.fixture(scope="module")
def ways():
    data = json.loads(SRC.read_text(encoding="utf-8"))
    return [e for e in data["elements"] if e["type"] == "way" and e.get("tags", {}).get("name")]


@pytest.fixture(scope="module")
def parsed(ways):
    agg = collections.defaultdict(lambda: (collections.Counter(), collections.Counter()))
    for w in ways:
        t = w["tags"]
        ru_c, en_c = agg[t["name"]]
        if t.get("name:ru"):
            ru_c[t["name:ru"]] += 1
        if t.get("name:en"):
            en_c[t["name:en"]] += 1
    out = []
    for name, (ru_c, en_c) in agg.items():
        ru = ru_c.most_common(1)[0][0] if ru_c else None
        en = en_c.most_common(1)[0][0] if en_c else None
        out.append(parse_name(name, ru, en))
    return out


def test_performance_13k(ways):
    # Best of three cold runs: other processes may load the machine during a test run.
    best = float("inf")
    for _ in range(3):
        _parse_cached.cache_clear()
        t0 = time.perf_counter()
        for w in ways:
            t = w["tags"]
            parse_name(t["name"], t.get("name:ru"), t.get("name:en"))
        best = min(best, time.perf_counter() - t0)
    log.info("parse_name: %d ways in %.2fs (best of 3)", len(ways), best)
    assert len(ways) > 13_000
    # ~0.9 s on an idle machine; offline pipeline step, so the bound only catches regressions.
    assert best < 4.0, best


def test_family_count(parsed):
    assert 4_500 < len(parsed) < 4_700
    mapping = group_families(parsed)
    families = {mapping[p.family_key] for p in parsed}
    log.info("real data: %d names -> %d families", len(parsed), len(families))
    assert 2_278 * 0.97 <= len(families) <= 2_278 * 1.03, len(families)


def test_unrecognized_types(parsed):
    unrec = [p.raw for p in parsed if p.type_word is None and p.kind == "other"]
    no_word = [p.raw for p in parsed if p.type_word is None]
    assert all(p.kind in KIND_CODES for p in parsed)
    assert len(no_word) / len(parsed) < 0.01, no_word
    assert len(unrec) / len(parsed) < 0.01, unrec


def test_derived_have_parent(parsed):
    mapping = group_families(parsed)
    primary = {mapping[p.family_key] for p in parsed if not p.is_derived}
    derived = [p for p in parsed if p.is_derived]
    found = [p for p in derived if p.parent_family_key and mapping[p.parent_family_key] in primary]
    log.info("derived names: %d, parent family found: %d", len(derived), len(found))
    assert len(derived) > 2_000
    assert len(found) / len(derived) >= 0.95


def test_kind_distribution(parsed):
    kinds = collections.Counter(p.kind for p in parsed)
    # prototype counts by type word: ქუჩა 2199, შესახვევი 966, ჩიხი 1052, გასასვლელი 207
    assert 2_150 < kinds["street"] < 2_250
    assert kinds["lane"] == 966
    assert kinds["dead_end"] == 1_052
    assert 200 < kinds["passage"] < 220
    with_ord = sum(1 for p in parsed if p.ord is not None)
    assert 1_900 < with_ord < 2_000          # prototype: 1 961 names with an ordinal
    nested = sum(1 for p in parsed if p.parent_kind is not None)
    assert 25 <= nested <= 45                # prototype: 32 nested parents
