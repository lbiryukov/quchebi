"""Eponym taxonomy for ``eponyms.json``: loading and alias generation.

Source order: ``pipeline/enrich/taxonomy.json`` (owned by the eponyms stage), then the research
prototype ``temp_scripts/prototypes/etymology/out/taxonomy.json``, then a minimal stub.
Both files use ``{"categories": [{"id", "ru", "children": [{"id", "ru"}]}]}``.

When a node has no ``aliases``, they are derived from its ``ru`` label: the label is split on
commas, parenthesised parts are dropped, every phrase is kept, and single words also get a
plural and a feminine form from simple suffix rules (``поэт`` -> ``поэты``, ``поэтесса``).
Aliases are lower case with ``ё`` replaced by ``е`` (the DSL treats them as equal).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pipeline.config import ROOT
from pipeline.log import get_logger

log = get_logger(__name__)

TAXONOMY_PATHS = (
    ROOT / "pipeline" / "enrich" / "taxonomy.json",
    ROOT / "temp_scripts" / "prototypes" / "etymology" / "out" / "taxonomy.json",
)

STUB_TAXONOMY: list[dict[str, Any]] = [
    {"id": "lit", "ru": "Литература", "children": [{"id": "lit.poet", "ru": "поэт"},
                                                  {"id": "lit.writer", "ru": "писатель"}]},
    {"id": "none", "ru": "Без эпонима", "children": [{"id": "none.unknown", "ru": "не установлено"}]},
]

# Eponym kind -> DSL aliases (docs/dsl.md: "персона" = person or persons_multi).
EPONYM_KINDS: dict[str, list[str]] = {
    "person": ["персона", "человек", "люди"],
    "persons_multi": ["персона", "человек", "люди"],
    "fictional_character": ["персонаж"],
    "toponym": ["топоним", "место"],
    "event_date": ["событие", "дата"],
    "nature": ["природа"],
    "concept": ["понятие"],
    "object": ["объект"],
    "group": ["группа"],
}

_HUSHING = "гкхжчшщ"
_VOWELS = "аеёиоуыэюя"


def norm_alias(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower().replace("ё", "е"))


def plural(word: str) -> str | None:
    """Very small Russian nominative-plural rule for occupation nouns."""
    w = word
    if len(w) < 3:
        return None
    if w.endswith(("ой", "ый", "ий", "ая")):     # adjectives: святой, святая -> святые
        return w[:-2] + ("ие" if w[-3] in _HUSHING or w.endswith("ий") else "ые")
    if w.endswith("ец"):
        return w[:-2] + "цы"                     # полководец -> полководцы
    if w.endswith(("ь", "й")):
        return w[:-1] + "и"
    if w.endswith("а"):
        return w[:-1] + ("и" if w[-2] in _HUSHING else "ы")
    if w.endswith("я"):
        return w[:-1] + "и"
    if w.endswith(("о", "е", "и", "у", "ы", "э", "ю")):
        return None                              # indeclinable or already plural
    return w + ("и" if w[-1] in _HUSHING else "ы")


def feminine(word: str) -> str | None:
    """Very small Russian feminine-noun rule for occupation nouns."""
    w = word
    if w == "поэт":
        return "поэтесса"
    if w in ("актер", "актёр"):
        return "актриса"
    if w.endswith(("ой", "ый")):
        return w[:-2] + "ая"                     # святой -> святая
    if w.endswith("тель"):
        return w + "ница"                        # писатель -> писательница
    if w.endswith("ист"):
        return w + "ка"                          # журналист -> журналистка
    if w.endswith("ник"):
        return w[:-1] + "ца"                     # художник -> художница
    if w.endswith("ец") and len(w) <= 5:
        return w[:-2] + "ица"                    # певец -> певица (short words only)
    return None


def derive_aliases(label: str) -> list[str]:
    """Aliases from a ru label like ``"писатель, прозаик"``."""
    label = re.sub(r"\([^)]*\)", " ", label or "")
    out: list[str] = []

    def add(x: str | None) -> None:
        if x:
            x = norm_alias(x)
            if x and x not in out:
                out.append(x)

    for part in label.split(","):
        part = norm_alias(part)
        if not part:
            continue
        add(part)
        if " " not in part and re.fullmatch(r"[а-я-]+", part):
            add(plural(part))
            fem = feminine(part)
            add(fem)
            if fem:
                add(plural(fem))
    return out


def _node(raw: dict[str, Any]) -> dict[str, Any]:
    node: dict[str, Any] = {"id": str(raw["id"]), "ru": str(raw.get("ru") or raw["id"])}
    aliases = raw.get("aliases")
    node["aliases"] = [norm_alias(a) for a in aliases if norm_alias(a)] if aliases else derive_aliases(node["ru"])
    return node


def build_taxonomy(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert raw ``categories`` into the pack form ``[{id, ru, aliases, children}]``."""
    out = []
    for g in categories:
        node = _node(g)
        node["children"] = [_node(c) for c in g.get("children") or []]
        out.append(node)
    return out


def eponym_kinds(raw: Any = None) -> dict[str, list[str]]:
    """``eponyms.kinds``: the taxonomy file's ``kinds`` block if present, else :data:`EPONYM_KINDS`.

    docs/dsl.md: ``персона`` = kind person or persons_multi, so person's aliases are always
    shared with persons_multi.
    """
    kinds: dict[str, list[str]] = {}
    src = raw if isinstance(raw, dict) and raw else EPONYM_KINDS
    for k, al in src.items():
        vals = [al] if isinstance(al, str) else list(al or [])
        kinds[str(k)] = [a for a in dict.fromkeys(norm_alias(str(a)) for a in vals) if a]
    if "персона" not in kinds.setdefault("person", []):
        kinds["person"].insert(0, "персона")
    multi = kinds.setdefault("persons_multi", [])
    multi.extend(a for a in kinds["person"] if a not in multi)
    return kinds


def load_taxonomy(paths: tuple[Path, ...] = TAXONOMY_PATHS) -> tuple[list[dict[str, Any]], str, dict[str, list[str]]]:
    """Return ``(taxonomy_for_pack, source_label, eponym_kind_aliases)``."""
    for p in paths:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                cats = data["categories"] if isinstance(data, dict) else data
                tax = build_taxonomy(cats)
                kinds = eponym_kinds(data.get("kinds") if isinstance(data, dict) else None)
                log.info("taxonomy: %d groups, %d categories, %d eponym kinds from %s", len(tax),
                         sum(len(g["children"]) for g in tax), len(kinds), p)
                return tax, str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p), kinds
            except Exception:
                log.exception("taxonomy file %s is unreadable, trying next source", p)
    log.warning("no taxonomy file found; using minimal stub taxonomy")
    return build_taxonomy(STUB_TAXONOMY), "stub", eponym_kinds()


def taxonomy_ids(tax: list[dict[str, Any]]) -> set[str]:
    ids = set()
    for g in tax:
        ids.add(g["id"])
        ids.update(c["id"] for c in g.get("children", []))
    return ids
