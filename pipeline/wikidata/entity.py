"""Accessors for Wikidata entity JSON (``wbgetentities`` format).

Deprecated statements are ignored everywhere; preferred statements come first.
"""
from __future__ import annotations

import re
from typing import Any

QID_RE = re.compile(r"^Q[1-9][0-9]*$")
HUMAN = "Q5"


def is_qid(s: Any) -> bool:
    return isinstance(s, str) and bool(QID_RE.match(s))


def _statements(e: dict, prop: str) -> list[dict]:
    st = [c for c in (e.get("claims") or {}).get(prop, []) if c.get("rank") != "deprecated"]
    # stable: preferred first, then normal, keeping Wikidata order inside each rank
    return [c for c in st if c.get("rank") == "preferred"] + [c for c in st if c.get("rank") != "preferred"]


def claim_ids(e: dict | None, prop: str) -> list[str]:
    """Item ids (QIDs) that are values of ``prop``; order preserved, duplicates removed."""
    if not e:
        return []
    out: list[str] = []
    for c in _statements(e, prop):
        dv = (c.get("mainsnak") or {}).get("datavalue") or {}
        if dv.get("type") == "wikibase-entityid":
            v = dv.get("value") or {}
            q = v.get("id") or (("Q%d" % v["numeric-id"]) if "numeric-id" in v else None)
            if q and q not in out:
                out.append(q)
    return out


def claim_strings(e: dict | None, prop: str) -> list[str]:
    if not e:
        return []
    out = []
    for c in _statements(e, prop):
        dv = (c.get("mainsnak") or {}).get("datavalue") or {}
        if dv.get("type") == "string" and isinstance(dv.get("value"), str):
            out.append(dv["value"])
    return out


def year(e: dict | None, prop: str) -> int | None:
    """Year of the first (preferred) time value of ``prop``; negative for BCE; None if unknown."""
    if not e:
        return None
    for c in _statements(e, prop):
        dv = (c.get("mainsnak") or {}).get("datavalue") or {}
        v = dv.get("value")
        if isinstance(v, dict) and isinstance(v.get("time"), str):
            m = re.match(r"^([+-])(\d+)-", v["time"])
            if m:
                y = int(m.group(2))
                if y == 0:
                    continue
                # decade/century precision still yields a usable year (start of the period)
                return -y if m.group(1) == "-" else y
    return None


def label(e: dict | None, lang: str) -> str:
    if not e:
        return ""
    return ((e.get("labels") or {}).get(lang) or {}).get("value", "") or ""


def description(e: dict | None, lang: str) -> str:
    if not e:
        return ""
    return ((e.get("descriptions") or {}).get(lang) or {}).get("value", "") or ""


def aliases(e: dict | None, lang: str) -> list[str]:
    if not e:
        return []
    return [a.get("value", "") for a in (e.get("aliases") or {}).get(lang, []) if a.get("value")]


def labels_all(e: dict | None, langs: tuple[str, ...] = ("ka", "ru", "en")) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for lang in langs:
        lab = label(e, lang)
        if lab:
            out.append((lang, lab))
        out.extend((lang, a) for a in aliases(e, lang))
    return out


def sitelink(e: dict | None, wiki: str) -> str:
    if not e:
        return ""
    return ((e.get("sitelinks") or {}).get(wiki) or {}).get("title", "") or ""


def n_sitelinks(e: dict | None) -> int:
    return len((e or {}).get("sitelinks") or {})


def is_human(e: dict | None) -> bool:
    return HUMAN in claim_ids(e, "P31")


def best_label(e: dict | None, langs: tuple[str, ...] = ("ru", "en", "ka")) -> str:
    for lang in langs:
        lab = label(e, lang)
        if lab:
            return lab
    return ""
