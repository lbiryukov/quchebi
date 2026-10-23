"""Golden counts for cross-checking the JS DSL engine (docs/dsl.md), zone off.

Two independent implementations of the same queries:

* :func:`golden_sql` -- SQL over ``data/runcity.sqlite`` (this is what ``golden_counts.json``
  stores and what the JS engine must reproduce on the pack);
* :func:`golden_pack` -- plain Python over the exported pack, resolving ``кто:`` values through
  the shipped taxonomy aliases and ``eponyms.kinds`` like the client does.

When both agree the pack carries everything the queries need.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from pipeline.export.build import HOUSE_EXPORT_WHERE

Q_HOUSE3 = "дом:3"
Q_STREET_HOUSE3 = "тип:улица* дом:3"
Q_LANE_X_POET = "тип:переулок x:(тип:улица* кто:поэт)"
Q_DEADEND_NOHOUSE = "тип:тупик дом:нет"
Q_WRITER_F = "кто:писатель пол:ж"
Q_STREET_NOT_PERSON = "тип:улица -кто:персона"
Q_LANE_GROUP = "тип:переулок*"
QUERIES = (Q_HOUSE3, Q_STREET_HOUSE3, Q_LANE_X_POET, Q_DEADEND_NOHOUSE, Q_WRITER_F, Q_STREET_NOT_PERSON, Q_LANE_GROUP)

POET, WRITER = "lit.poet", "lit.writer"
PERSON_KINDS = ("person", "persons_multi")


def _in(codes: list[str]) -> str:
    return "(" + ",".join("'" + c.replace("'", "''") + "'" for c in codes) + ")" if codes else "('')"


def golden_sql(con: sqlite3.Connection, kinds: list[dict[str, Any]]) -> dict[str, int]:
    """Count the golden queries directly in SQLite."""
    street = _in([k["code"] for k in kinds if k["group"] == "street*"])
    lane = _in([k["code"] for k in kinds if k["group"] == "lane*"])
    has_house = f"EXISTS (SELECT 1 FROM house h WHERE h.feature_id = f.id AND {HOUSE_EXPORT_WHERE})"
    # A range "1-5" covers 3; "2-4" does not: a range stays on its own parity side (docs/client-architecture.md).
    house3 = (f"EXISTS (SELECT 1 FROM house h WHERE h.feature_id = f.id AND {HOUSE_EXPORT_WHERE} "
              f"AND (h.n = 3 OR (h.n_to IS NOT NULL AND h.n <= 3 AND h.n_to >= 3 AND h.n % 2 = 1)))")

    def cat(alias: str, category: str) -> str:
        return (f"EXISTS (SELECT 1 FROM family fm JOIN eponym_category ec ON ec.eponym_id = fm.eponym_id "
                f"WHERE fm.id = {alias}.family_id AND ec.category = '{category}')")

    person = (f"EXISTS (SELECT 1 FROM family fm JOIN eponym ep ON ep.id = fm.eponym_id "
              f"WHERE fm.id = f.family_id AND ep.kind IN {_in(list(PERSON_KINDS))})")
    female = ("EXISTS (SELECT 1 FROM family fm JOIN eponym ep ON ep.id = fm.eponym_id "
              "WHERE fm.id = f.family_id AND lower(ep.gender) IN ('f', 'ж', 'female'))")
    crosses_poet_street = (
        "EXISTS (SELECT 1 FROM crossing c JOIN feature g ON g.id = (CASE WHEN c.a = f.id THEN c.b ELSE c.a END) "
        f"WHERE (c.a = f.id OR c.b = f.id) AND c.a <> c.b AND g.kind IN {street} AND {cat('g', POET)})")
    sql = {
        Q_HOUSE3: f"SELECT COUNT(*) FROM feature f WHERE {house3}",
        Q_STREET_HOUSE3: f"SELECT COUNT(*) FROM feature f WHERE f.kind IN {street} AND {house3}",
        Q_LANE_X_POET: f"SELECT COUNT(*) FROM feature f WHERE f.kind = 'lane' AND {crosses_poet_street}",
        Q_DEADEND_NOHOUSE: f"SELECT COUNT(*) FROM feature f WHERE f.kind = 'dead_end' AND NOT {has_house}",
        Q_WRITER_F: f"SELECT COUNT(*) FROM feature f WHERE {cat('f', WRITER)} AND {female}",
        Q_STREET_NOT_PERSON: f"SELECT COUNT(*) FROM feature f WHERE f.kind = 'street' AND NOT {person}",
        Q_LANE_GROUP: f"SELECT COUNT(*) FROM feature f WHERE f.kind IN {lane}",
    }
    return {q: int(con.execute(sql[q]).fetchone()[0]) for q in QUERIES}


def _norm(s: str) -> str:
    return s.strip().lower().replace("ё", "е")


def resolve_who(value: str, eponyms: dict[str, Any]) -> tuple[set[str], set[str]]:
    """``кто:value`` -> (taxonomy ids, eponym kinds), resolution order from docs/dsl.md."""
    v = _norm(value)
    groups = eponyms["taxonomy"]
    all_ids = {g["id"]: [c["id"] for c in g.get("children", [])] for g in groups}
    for g in groups:
        if v == g["id"]:
            return {g["id"], *all_ids[g["id"]]}, set()
        for c in g.get("children", []):
            if v == c["id"]:
                return {c["id"]}, set()
    ids: set[str] = set()
    for g in groups:
        if v in (_norm(a) for a in g.get("aliases", [])):
            ids |= {g["id"], *all_ids[g["id"]]}
        for c in g.get("children", []):
            if v in (_norm(a) for a in c.get("aliases", [])):
                ids.add(c["id"])
    if ids:
        return ids, set()
    kinds = {k for k, al in eponyms.get("kinds", {}).items() if v in (_norm(a) for a in al)}
    return set(), kinds


def golden_pack(pack: dict[str, Any]) -> dict[str, int]:
    """Evaluate the golden queries on the parsed pack (core, houses, eponyms)."""
    core, houses, eps = pack["core"], pack["houses"], pack["eponyms"]
    kinds, F, fam, E = core["kinds"], core["f"], core["fam"], eps["e"]
    code = [kinds[f["k"]]["code"] for f in F]
    group = [kinds[f["k"]]["group"] for f in F]
    n = len(F)
    hrows = [houses["h"].get(str(i), []) for i in range(n)]

    def ep(i: int) -> dict[str, Any] | None:
        j = F[i].get("fam")
        if j is None or "e" not in fam[j]:
            return None
        return E[fam[j]["e"]]

    resolved = {v: resolve_who(v, eps) for v in ("поэт", "писатель", "персона")}

    def who(i: int, value: str) -> bool:
        ids, ks = resolved[value]
        e = ep(i)
        if e is None:
            return False
        return bool(ids & set(e.get("cat", []))) or e.get("k") in ks

    def house3(i: int) -> bool:
        return any(r[1] == 3 or (len(r) > 6 and r[6] is not None and r[1] <= 3 <= r[6] and r[1] % 2 == 1)
                   for r in hrows[i])

    poet_street = {i for i in range(n) if group[i] == "street*" and who(i, "поэт")}
    adj: dict[int, set[int]] = {}
    x = core["x"]
    for t in range(len(x) // 2):
        a, b = x[2 * t], x[2 * t + 1]
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    female = {i for i in range(n) if (ep(i) or {}).get("g") == "f"}
    return {
        Q_HOUSE3: sum(1 for i in range(n) if house3(i)),
        Q_STREET_HOUSE3: sum(1 for i in range(n) if group[i] == "street*" and house3(i)),
        Q_LANE_X_POET: sum(1 for i in range(n) if code[i] == "lane" and adj.get(i, set()) & poet_street),
        Q_DEADEND_NOHOUSE: sum(1 for i in range(n) if code[i] == "dead_end" and not hrows[i]),
        Q_WRITER_F: sum(1 for i in range(n) if i in female and who(i, "писатель")),
        Q_STREET_NOT_PERSON: sum(1 for i in range(n) if code[i] == "street" and not who(i, "персона")),
        Q_LANE_GROUP: sum(1 for i in range(n) if group[i] == "lane*"),
    }
