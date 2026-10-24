"""Stage ``match``: person eponyms without a known QID -> Wikidata humans by exact label/alias.

Labels tried: nominative variants of the Georgian eponym (ka), the eponym part of name:ru and
name:en. Only items with P31=Q5. Preferences when several humans match: the only Georgian one
(P27 Georgian state / born in Georgia) -> the only one with a kawiki article -> the only deceased
one. Guards: a one-word eponym (surname/mononym) is never resolved automatically; a living
namesake born after 1960 is "suspicious"; people born after 1995 are dropped.

Families with a known QID (OSM / P138) are matched too, as a control set: the stage reports how
often the matcher reproduces the known QID.

Output ``data/enrich/stages/match.json``: {family.key: {"status", "reason", "chosen", "candidates",
"strong", "n_georgian", "control"}}.
"""
from __future__ import annotations

import collections
import re
import sqlite3

from pipeline.enrich import ka as K
from pipeline.enrich.articles import article_facts, consistent_name, named_after
from pipeline.enrich.common import Family, add_provenance, clear_provenance, fid, stage_path, write_json
from pipeline.enrich.rules import PERSON_SUBS
from pipeline.enrich.taxonomy import load as load_taxonomy
from pipeline.log import get_logger, stage
from pipeline.wikidata.client import WikidataClient
from pipeline.wikidata.entity import (claim_ids, description, is_human, label, labels_all, n_sitelinks, sitelink,
                                      year)

log = get_logger(__name__)

PAIRS_PER_QUERY = 100
GEO_WORDS = ("georgia", "kartli", "kakheti", "imereti", "iberia", "colchis", "lazica", "guria", "mingrelia",
             "samegrelo", "abkhazia", "samtskhe", "tao-klarjeti", "svaneti", "tbilisi", "egrisi", "hereti")
TOO_YOUNG = 1995
SUSPICIOUS_BORN = 1960


def label_pairs(f: Family) -> list[tuple[str, str]]:
    """(label, lang) pairs for exact-label lookups."""
    aliases = []
    for m in f.members:
        aliases += [a.strip() for a in re.findall(r"\(([^)]*)\)", m.ka) if K.has_ka(a)]
    out: list[tuple[str, str]] = [(v, "ka") for v in K.ka_label_variants(f.base_ka, sorted(set(aliases)))]
    if f.nom_ka and (f.nom_ka, "ka") not in out and K.has_ka(f.nom_ka):
        out.append((f.nom_ka, "ka"))
    if f.base_ru and re.search(r"[а-яА-Я]", f.base_ru):
        out.append((f.base_ru, "ru"))
    if f.base_en and re.search(r"[A-Za-z]", f.base_en):
        out.append((f.base_en, "en"))
    return list(dict.fromkeys(out))


def sparql_candidates(client: WikidataClient, pairs: list[tuple[str, str, str]]) -> dict[str, set[str]]:
    """pairs [(family fid, label, lang)] -> {fid: {qid}} of humans with that exact label/alias."""
    found = client.humans_by_label([(lab, lang) for _, lab, lang in pairs], chunk=PAIRS_PER_QUERY)
    res: dict[str, set[str]] = collections.defaultdict(set)
    missing = 0
    for k, lab, lang in pairs:
        hit = found.get((lab, lang))
        if hit is None:
            missing += 1
            continue
        res[k] |= hit
    if missing:
        log.warning("match: %d label pairs unavailable (SPARQL failed or offline)", missing)
    return res


def georgian_signals(e: dict, ents: dict[str, dict], georgian_states: set[str]) -> list[str]:
    why = []
    if sitelink(e, "kawiki"):
        why.append("kawiki")
    for c in claim_ids(e, "P27"):
        lab = label(ents.get(c), "en").lower()
        if c in georgian_states or any(w in lab for w in GEO_WORDS):
            why.append("P27")
            break
    for c in claim_ids(e, "P19"):
        pe = ents.get(c) or {}
        if set(claim_ids(pe, "P17")) & georgian_states or "Q230" in claim_ids(pe, "P131"):
            why.append("P19")
            break
    if "Q18973" in claim_ids(e, "P172"):
        why.append("P172")
    return why


def candidate_summary(q: str, e: dict, ents: dict[str, dict], georgian_states: set[str]) -> dict:
    return {
        "qid": q, "label_ka": label(e, "ka"), "label_ru": label(e, "ru"), "label_en": label(e, "en"),
        "desc": description(e, "ru") or description(e, "en"), "born": year(e, "P569"), "died": year(e, "P570"),
        "georgian": georgian_signals(e, ents, georgian_states), "n_sitelinks": n_sitelinks(e),
    }


def decide(f: Family, labels: list[tuple[str, str]], cand: set[str], ents: dict[str, dict],
           georgian_states: set[str]) -> dict:
    target_k = {K.label_key(v) for v, lang in labels if lang == "ka"}
    target_l = {K.norm_latin(v) for v, lang in labels if lang in ("ru", "en")}
    strong = []
    for q in sorted(cand, key=lambda x: int(x[1:])):
        e = ents.get(q)
        if not e or not is_human(e):
            continue
        labs = labels_all(e)
        m_ka = any(lang == "ka" and K.label_key(v) in target_k for lang, v in labs)
        m_l = any(lang in ("en", "ru") and K.norm_latin(v) in target_l for lang, v in labs)
        if not (m_ka or m_l):
            continue
        c = candidate_summary(q, e, ents, georgian_states)
        c["m_ka"], c["m_l"] = m_ka, m_l
        if c["born"] and c["born"] > TOO_YOUNG:
            continue
        c["suspicious"] = bool(c["born"] and c["born"] > SUSPICIOUS_BORN and c["died"] is None)
        strong.append(c)
    strong.sort(key=lambda c: (not c["georgian"], not c["m_ka"], -c["n_sitelinks"], int(c["qid"][1:])))
    res = {"status": None, "reason": "", "chosen": None, "candidates": [c["qid"] for c in strong],
           "strong": strong[:8], "n_georgian": sum(1 for c in strong if c["georgian"])}
    single = len(K.core_tokens(f.base_ka)) <= 1
    if single and strong:
        res.update(status="ambiguous", reason="single-token name (surname/mononym): review")
        return res
    if not strong:
        res.update(status="not_found", reason="no human with a matching label")
        return res
    chosen, reason = None, ""
    if len(strong) == 1:
        chosen, reason = strong[0], "single name match"
    else:
        g = [c for c in strong if c["georgian"]]
        k = [c for c in g if "kawiki" in c["georgian"]]
        dead = [c for c in strong if c["died"] is not None]
        if len(g) == 1:
            chosen, reason = g[0], "resolved: only Georgian candidate"
        elif len(k) == 1 and all(c["died"] is None for c in g if c is not k[0]):
            # a kawiki article beats only sparse/living namesakes; a deceased Georgian namesake
            # without an article (often the one the street honours) keeps it ambiguous
            chosen, reason = k[0], "resolved: only one with kawiki"
        elif len(dead) == 1:
            chosen, reason = dead[0], "resolved: only deceased candidate"
    if chosen is None:
        res.update(status="ambiguous", reason="%d name matches (%d Georgian)" % (len(strong), res["n_georgian"]))
    elif chosen["suspicious"]:
        res.update(status="suspicious", reason="living namesake born after %d: review" % SUSPICIOUS_BORN,
                   chosen=chosen["qid"])
    else:
        res.update(status="unique", reason=reason, chosen=chosen["qid"])
    return res


def _close(a: int | None, b: int | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= 1


def resolve_by_articles(d: dict, named: list[dict], linked: set[str]) -> None:
    """Resolve namesakes with explicit article facts: the street article links the person's kawiki
    page, or gives birth and death years that only one candidate has."""
    strong = d["strong"]
    hit = [c for c in strong if c["qid"] in linked and not c.get("suspicious")]
    if len(hit) == 1:
        d.update(status="unique", chosen=hit[0]["qid"], reason="resolved: street article links the person")
        return
    for n in named:
        if n.get("birth") is None and n.get("death") is None:
            continue
        yrs = [c for c in strong if (n.get("birth") is None or _close(c["born"], n["birth"]))
               and (n.get("death") is None or _close(c["died"], n["death"]))]
        if len(yrs) == 1:
            d.update(status="unique", chosen=yrs[0]["qid"], reason="resolved: encyclopedia years")
            return


def run(con: sqlite3.Connection, fams: list[Family], rules: dict[str, dict], qids: dict[str, dict],
        client: WikidataClient, out_dir=None, control: bool = True) -> dict[str, dict]:
    tax = load_taxonomy()
    with stage(log, "enrich.match") as st:
        pool, ctrl = [], []
        for f in fams:
            r = rules.get(f.key) or {}
            if r.get("cls") != "person" or r.get("sub") not in PERSON_SUBS:
                continue
            known = qids.get(f.key)
            if not known:
                pool.append(f)
            elif control and len(known["qids"]) == 1 and not known.get("conflict"):
                ctrl.append(f)
        items = pool + ctrl
        facts = article_facts(con)
        named = {f.key: [n for n in named_after(facts.get(f.id, []))
                         if consistent_name(f.base_ka, n.get("name_ka", ""))] for f in items}
        labels = {f.key: label_pairs(f) for f in items}
        for f in items:                     # the full name given by the encyclopedia / Wikipedia article
            for n in named[f.key]:
                pair = (K.strip_parens(n["name_ka"]), "ka")
                if pair not in labels[f.key]:
                    labels[f.key].append(pair)
        pairs = [(fid(f.key), lab, lang) for f in items for lab, lang in labels[f.key]]
        st["pool"], st["control"], st["pairs"] = len(pool), len(ctrl), len(pairs)
        found = sparql_candidates(client, pairs)
        link_titles = {K.strip_parens(n["name_ka"]) for v in named.values() for n in v if n.get("src") == "kawiki_link"}
        by_title = client.titles_to_qids("kawiki", sorted(link_titles)) if link_titles else {}
        linked: dict[str, set[str]] = {}
        for f in items:
            qs = {by_title[K.strip_parens(n["name_ka"])] for n in named[f.key]
                  if n.get("src") == "kawiki_link" and K.strip_parens(n["name_ka"]) in by_title}
            if qs:
                linked[f.key] = qs
                found.setdefault(fid(f.key), set()).update(qs)
        allq = sorted({q for s in found.values() for q in s})
        ents = client.entities(allq)
        hop = set()
        for q in allq:
            e = ents.get(q)
            if e and is_human(e):
                hop.update(claim_ids(e, "P19"))
                hop.update(claim_ids(e, "P27"))
        ents_hop = client.entities(sorted(hop), props="labels|claims", langs="en|ru|ka")
        all_ents = {**ents_hop, **ents}
        out: dict[str, dict] = {}
        for f in items:
            d = decide(f, labels[f.key], found.get(fid(f.key), set()), all_ents, tax.georgian_states)
            if d["status"] != "unique" and (named[f.key] or linked.get(f.key)):
                resolve_by_articles(d, named[f.key], linked.get(f.key, set()))
            d["labels"] = [f"{lab}@{lang}" for lab, lang in labels[f.key]]
            if named[f.key]:
                d["named_after"] = named[f.key][:3]
            if f.key in qids:
                d["control"] = qids[f.key]["qids"][0]
            out[f.key] = d
        with con:
            clear_provenance(con, ["qid"], ["wd_match"])
            for k, d in out.items():
                if d["status"] == "unique" and "control" not in d:
                    add_provenance(con, "family", k, "qid", d["chosen"], "wd_match", d["reason"],
                                   0.9 if d["reason"] == "single name match" else 0.85)
        write_json(stage_path("match", out_dir), out)
        cnt = collections.Counter(d["status"] for k, d in out.items() if "control" not in d)
        st.update(**{f"pool_{k}": v for k, v in sorted(cnt.items())})
        ctl = [d for d in out.values() if "control" in d]
        ok = sum(1 for d in ctl if d["status"] == "unique" and d["chosen"] == d["control"])
        wrong = [(k, d["control"], d["chosen"]) for k, d in out.items()
                 if "control" in d and d["status"] == "unique" and d["chosen"] != d["control"]]
        st.update(control_unique_correct=ok, control_unique_wrong=len(wrong),
                  control_total=len(ctl))
        for w in wrong[:20]:
            log.warning("match control mismatch: %s known %s matched %s", *w)
    return out
