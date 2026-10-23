"""Cross-name family resolution.

:func:`pipeline.names.parse.parse_name` computes a per-name ``family_key``. A few equivalences can
only be decided by looking at the whole population of names:

* initials: ``პრეზიდენტ ზ. გამსახურდიას სანაპირო`` (key ``გამსახურდი ზ.``) belongs to the unique
  family whose other token starts with ``ზ`` (``გამსახურდი ზვიად``);
* Russian base equality: two Georgian spellings (hyphen/space, typo in a given name) with the same
  multi-word Russian base name are one family;
* typo in a given name of a derived name whose family has no primary street:
  ``გიოგი სააკაძის I შეს.`` -> family of ``გიორგი სააკაძის ქუჩა`` (edit distance 1, same surname).

:func:`group_families` returns a mapping ``family_key -> canonical family_key`` (identity for keys
that are not merged). Pseudo keys (``#...``) are never merged.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable

from pipeline.log import get_logger
from pipeline.names.parse import ParsedName

log = get_logger(__name__)


def _damerau1(a: str, b: str) -> bool:
    """True if ``a`` and ``b`` differ by at most one edit (insert/delete/substitute/transpose)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    if la > lb:
        a, b = b, a
    # b is longer by one: check single deletion
    i = j = 0
    skipped = False
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            j += 1
    return True


def group_families(parsed: Iterable[ParsedName], weights: dict[str, int] | None = None) -> dict[str, str]:
    """Resolve per-name family keys into canonical family keys.

    ``parsed`` -- all parsed names of the city (one entry per unique name is enough).
    ``weights`` -- optional ``family_key -> size`` (e.g. number of ways) used to pick the canonical
    key when several keys merge; defaults to the number of names per key.
    """
    items = list(parsed)
    by_key: dict[str, list[ParsedName]] = defaultdict(list)
    for p in items:
        by_key[p.family_key].append(p)
    w = Counter({k: len(v) for k, v in by_key.items()})
    if weights:
        w.update(weights)
    parent: dict[str, str] = {k: k for k in by_key}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    merges: Counter = Counter()

    def union(src: str, dst: str, why: str) -> None:
        a, b = find(src), find(dst)
        if a == b:
            return
        if w[a] > w[b]:
            a, b = b, a
        parent[a] = b
        merges[why] += 1
        log.debug("family %s-merge %r -> %r", why, src, dst)

    real = [k for k in by_key if not k.startswith("#")]
    tokset = {k: frozenset(k.split(" ")) for k in real}
    by_token: dict[str, set[str]] = defaultdict(set)
    for k, ts in tokset.items():
        for t in ts:
            by_token[t].add(k)

    # (a) initials
    for k in real:
        toks = k.split(" ")
        inits = [t for t in toks if len(t) == 2 and t.endswith(".")]
        rest = frozenset(t for t in toks if t not in inits)
        if len(inits) != 1 or not rest:
            continue
        pool = set.intersection(*(by_token[t] for t in rest)) if rest else set()
        cands = [
            k2 for k2 in pool
            if k2 != k and len(tokset[k2]) == len(rest) + 1
            and any(t.startswith(inits[0][0]) and t not in rest for t in tokset[k2])
        ]
        if len(cands) == 1:
            union(k, cands[0], "initial")

    # (b) Russian base equality (multi-word or hyphenated, non-ordinal names)
    ru_map: dict[str, set[str]] = defaultdict(set)
    for k in real:
        for p in by_key[k]:
            b = (p.base_ru or "").strip().lower().replace("ё", "е")
            if b and (" " in b or "-" in b) and p.ord is None and p.parent_ord is None:
                ru_map[b].add(k)
    for b, ks in ru_map.items():
        if len(ks) > 1:
            ks_sorted = sorted(ks, key=lambda x: -w[x])
            for k2 in ks_sorted[1:]:
                union(k2, ks_sorted[0], "ru")

    # (c) typo in a given name: derived-only family vs a family with a primary street
    has_primary = {k: any(not p.is_derived for p in by_key[k]) for k in real}
    for k in real:
        if has_primary[k]:
            continue
        toks = k.split(" ")
        if len(toks) < 2:
            continue
        cands = set()
        for t in toks:
            others = [x for x in toks if x != t]
            pool = set.intersection(*(by_token[x] for x in others)) if others else set()
            for k2 in pool:
                if k2 == k or not has_primary.get(k2) or len(tokset[k2]) != len(toks):
                    continue
                extra = list(tokset[k2] - set(others))
                if len(extra) == 1 and len(t) >= 4 and _damerau1(t, extra[0]):
                    cands.add(k2)
        if len(cands) == 1:
            union(k, cands.pop(), "typo")

    log.info("family resolution: %d keys -> %d families, merges=%s",
             len(by_key), len({find(k) for k in by_key}), dict(merges))
    return {k: find(k) for k in by_key}


__all__ = ["group_families"]
