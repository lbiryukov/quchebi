"""Eponym taxonomy (``pipeline/enrich/taxonomy.json``) and the Wikidata -> category mapping."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

from pipeline.wikidata.entity import claim_ids

TAXONOMY_PATH = Path(__file__).with_name("taxonomy.json")
GEORGIA = "Q230"
# props in the order that decides the main category (claim order inside a prop is kept)
PROP_ORDER = ["P106", "P39", "P97", "P411", "P410", "P166", "P1196", "P31"]
GENDER = {"Q6581097": "m", "Q6581072": "f", "Q1052281": "f", "Q2449503": "m"}


def norm_alias(s: str) -> str:
    return " ".join((s or "").lower().replace("ё", "е").split())


@dataclass
class Taxonomy:
    raw: dict
    ids: set[str] = field(default_factory=set)            # second-level ids (lit.poet)
    groups: set[str] = field(default_factory=set)         # first-level ids (lit)
    ru: dict[str, str] = field(default_factory=dict)
    priority: dict[str, int] = field(default_factory=dict)
    alias: dict[str, str] = field(default_factory=dict)   # normalized alias -> id (category or group)
    rules: list[dict] = field(default_factory=list)
    by_prop: dict[str, list[dict]] = field(default_factory=dict)
    georgian_states: set[str] = field(default_factory=set)
    # category -> categories that make it redundant (union of exclude_if_also of all its rules)
    exclusions: dict[str, set[str]] = field(default_factory=dict)

    @property
    def props(self) -> list[str]:
        return [p for p in PROP_ORDER if p in self.by_prop]

    def roots(self, prop: str) -> set[str]:
        return {r["root"] for r in self.by_prop.get(prop, [])}

    def kind_for_categories(self, cats: Iterable[str]) -> str | None:
        ck = self.raw.get("category_kind", {})
        for c in cats:
            if c in ck:
                return ck[c]
            g = c.split(".")[0]
            if g in ck:
                return ck[g]
        return None

    def heuristic(self, hint: str) -> list[str]:
        return list(self.raw.get("heuristic_map", {}).get(hint, []))

    def title_category(self, title: str) -> str | None:
        return self.raw.get("title_categories", {}).get(title)

    def valid(self, cat: str) -> bool:
        return cat in self.ids

    def resolve(self, value: str) -> list[str]:
        """DSL-like resolution of an alias/id into second-level ids (used by tests and QA)."""
        v = norm_alias(value)
        if v in self.ids:
            return [v]
        if v in self.groups:
            return sorted(i for i in self.ids if i.startswith(v + "."))
        hit = self.alias.get(v)
        if hit is None:
            return []
        return [hit] if hit in self.ids else sorted(i for i in self.ids if i.startswith(hit + "."))

    # ------------------------------------------------------------------ mapping
    def categorize(self, e: dict, ancestors: Callable[[list[str]], dict[str, frozenset[str]]],
                   in_georgia: bool = False) -> tuple[list[str], list[str]]:
        """Categories of a Wikidata entity (ordered, main first) and the evidence strings."""
        order: list[str] = []
        why: list[str] = []
        for prop in self.props:
            vals = claim_ids(e, prop)
            if not vals:
                continue
            anc = ancestors(vals)
            for v in vals:
                a = anc.get(v, frozenset({v}))
                for r in self.by_prop[prop]:
                    if r["root"] in a and r.get("cat"):
                        cat = r.get("georgia_cat") if (in_georgia and r.get("georgia_cat")) else r["cat"]
                        if cat not in order:
                            order.append(cat)
                        why.append(f"{prop}:{v}->{cat}")
        cats = set(order)
        keep = []
        for c in order:
            others = cats - {c}
            if others & self.exclusions.get(c, set()):
                why.append(f"drop:{c}")
                continue
            keep.append(c)
        return keep, why


def _build(raw: dict) -> Taxonomy:
    t = Taxonomy(raw=raw)
    for g in raw["categories"]:
        t.groups.add(g["id"])
        t.ru[g["id"]] = g["ru"]
        t.priority[g["id"]] = g.get("priority", 5)
        for a in g.get("aliases", []):
            t.alias[norm_alias(a)] = g["id"]
        for c in g["children"]:
            t.ids.add(c["id"])
            t.ru[c["id"]] = c["ru"]
            t.priority[c["id"]] = g.get("priority", 5)
            for a in c.get("aliases", []):
                t.alias[norm_alias(a)] = c["id"]
    t.rules = [r for r in raw["rules"] if r.get("cat") is None or r["cat"] in t.ids]
    for r in t.rules:
        t.by_prop.setdefault(r["prop"], []).append(r)
        for c in {r.get("cat"), r.get("georgia_cat")} - {None}:
            t.exclusions.setdefault(c, set()).update(set(r.get("exclude_if_also") or []) - {c})
    t.georgian_states = set(raw.get("attributes", {}).get("origin", {}).get("georgian_states", [GEORGIA]))
    return t


@lru_cache(maxsize=4)
def load(path: str | None = None) -> Taxonomy:
    p = Path(path) if path else TAXONOMY_PATH
    return _build(json.loads(p.read_text(encoding="utf-8")))


def category_ids(path: str | None = None) -> list[str]:
    return sorted(load(path).ids)
