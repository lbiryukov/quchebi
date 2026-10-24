"""Stage ``oldnames``: eponyms of former names (``name_history.eponym_id``).

Same mechanism as for current names: the eponym part of a former name is extracted (Georgian
parser, or the Russian base), reused when it equals the eponym of an existing family, otherwise
classified by the rules, matched against Wikidata humans and, failing that, queued for the
classifier (``oNNN.jsonl`` batches; dossiers in ``data/enrich/dossiers_oldnames.jsonl``).

Keys are text keys (``old:<normalized eponym>``), so decisions survive rebuilds.
"""
from __future__ import annotations

import collections
import re
import sqlite3

from pipeline.enrich import ka as K
from pipeline.enrich.common import (Family, add_provenance, paths, clear_provenance, fid, stage_path, table_exists,
                                    write_json, write_jsonl)
from pipeline.enrich.dossiers import CandidateFactory, MAX_CANDIDATES
from pipeline.enrich.facts import CONF, EponymBuilder, confident_rule, rules_record
from pipeline.enrich.gazetteer import Gazetteer
from pipeline.enrich.match import decide, label_pairs, sparql_candidates
from pipeline.enrich.rules import KIND_OF, PERSON_SUBS, classify, data_given_names
from pipeline.enrich.store import set_categories, upsert_eponym
from pipeline.log import get_logger, stage
from pipeline.wikidata.client import WikidataClient
from pipeline.wikidata.entity import claim_ids, is_human

log = get_logger(__name__)



def split_old(name_ka: str | None, name_ru: str | None, name_lat: str | None) -> dict:
    """Eponym parts of a former name: base_ka (genitive), base_ru, base_en, nominative guess."""
    base_ka = nom = ""
    if name_ka and K.has_ka(name_ka):
        try:
            from pipeline.names.parse import parse_name
            p = parse_name(name_ka, name_ru, name_lat)
            base_ka, nom = p.base_ka or "", p.eponym_nom_ka or ""
        except Exception as e:  # parser still evolving: fall back to the local splitter
            log.debug("parse_name failed for %r: %s", name_ka, e)
            base_ka = K.split_ka_name(name_ka)["base"]
        if not nom and base_ka:
            nom = K.nominative(base_ka)
    return {"base_ka": K.clean(base_ka), "base_ru": K.ru_base(name_ru or ""), "base_en": K.en_base(name_lat or ""),
            "nom_ka": nom}


def old_key(parts: dict) -> str | None:
    if parts["base_ka"]:
        return "old:" + K.norm_key(parts["base_ka"])
    if parts["base_ru"]:
        return "old-ru:" + " ".join(re.sub(r"[^\w\s-]", " ", parts["base_ru"].lower().replace("ё", "е")).split())
    if parts["base_en"]:
        return "old-lat:" + K.norm_latin(parts["base_en"])
    return None


def collect(con: sqlite3.Connection) -> dict[str, dict]:
    """old key -> {"parts", "nh_ids", "names": [...], "current": [...], "years"}."""
    out: dict[str, dict] = {}
    if not table_exists(con, "name_history"):
        return out
    q = ("SELECT nh.id, nh.name_ka, nh.name_ru, nh.name_lat, nh.from_year, nh.to_year, nh.source, "
         "f.ka, f.ru FROM name_history nh LEFT JOIN feature f ON f.id = nh.feature_id ORDER BY nh.id")
    for i, nka, nru, nlat, y0, y1, src, cka, cru in con.execute(q):
        parts = split_old(nka, nru, nlat)
        k = old_key(parts)
        if not k:
            continue
        d = out.setdefault(k, {"parts": parts, "nh_ids": [], "names": [], "current": [], "years": []})
        d["nh_ids"].append(i)
        nm = {kk: v for kk, v in (("ka", nka), ("ru", nru), ("lat", nlat), ("from", y0), ("to", y1), ("source", src))
              if v not in (None, "")}
        if nm not in d["names"]:
            d["names"].append(nm)
        cur = {kk: v for kk, v in (("ka", cka), ("ru", cru)) if v}
        if cur and cur not in d["current"]:
            d["current"].append(cur)
    return out


def pseudo_family(key: str, parts: dict) -> Family:
    return Family(id=-1, key=key, base_ka=parts["base_ka"], base_ru=parts["base_ru"], base_en=parts["base_en"],
                  nom_ka=parts["nom_ka"], kind_hint=None, eponym_id=None, members=[])


def run(con: sqlite3.Connection, fams: list[Family], gaz: Gazetteer, client: WikidataClient,
        builder: EponymBuilder | None = None, search: bool = True, out_dir=None) -> dict:
    builder = builder or EponymBuilder(client)
    with stage(log, "enrich.oldnames") as st:
        olds = collect(con)
        st["old_keys"] = len(olds)
        st["name_history_rows"] = sum(len(v["nh_ids"]) for v in olds.values())
        fam_by_norm: dict[str, Family] = {}
        for f in sorted(fams, key=lambda x: x.eponym_id is None):     # families with an eponym first
            if f.base_ka:
                fam_by_norm.setdefault(K.norm_key(f.base_ka), f)
        gn_all = gaz.given_names | data_given_names(fams, gaz.given_names)
        state: dict[str, dict] = {}
        pf: dict[str, Family] = {}
        rules: dict[str, dict] = {}
        for k, v in olds.items():
            p = v["parts"]
            f_cur = fam_by_norm.get(K.norm_key(p["base_ka"])) if p["base_ka"] else None
            if f_cur is not None:
                state[k] = {"status": "family", "eponym_id": f_cur.eponym_id, "family_key": f_cur.key}
                continue
            f = pseudo_family(k, p)
            pf[k] = f
            if p["base_ka"]:
                r = classify(f, gaz, gn_all)
                r["kind"] = KIND_OF.get(r["cls"], "unknown")
                r["hint"] = f"{r['kind']}:{r['sub']}"
                r["single_token"] = len(K.core_tokens(p["base_ka"])) <= 1
            else:
                r = {"cls": "proper_unknown", "sub": "ru_only", "kind": "unknown", "hint": "unknown:ru_only",
                     "evidence": ["no Georgian name"], "single_token": len(p["base_ru"].split()) <= 1}
            rules[k] = r
        # person matching
        persons = [k for k, r in rules.items() if r.get("cls") == "person" and r.get("sub") in PERSON_SUBS]
        labels = {k: label_pairs(pf[k]) for k in persons}
        pairs = [(fid(k), lab, lang) for k in persons for lab, lang in labels[k]]
        found = sparql_candidates(client, pairs) if pairs else {}
        allq = sorted({q for s in found.values() for q in s})
        ents = client.entities(allq)
        hop = {x for q in allq if is_human(ents.get(q)) for p_ in ("P19", "P27") for x in claim_ids(ents.get(q), p_)}
        all_ents = {**client.entities(sorted(hop), props="labels|claims", langs="en|ru|ka"), **ents}
        matches = {k: decide(pf[k], labels[k], found.get(fid(k), set()), all_ents, builder.tax.georgian_states)
                   for k in persons}
        queue: list[str] = []
        builder.build(sorted({m["chosen"] for m in matches.values() if m.get("status") == "unique"}))  # batched
        with con:
            clear_provenance(con, ["eponym"], ["wd_match", "rules", "family"], entity="name_history")
            for k, r in rules.items():
                m = matches.get(k)
                eid = None
                if m and m["status"] == "unique":
                    facts = builder.build([m["chosen"]]).get(m["chosen"])
                    if facts is not None:
                        row = con.execute("SELECT id FROM eponym WHERE qid=?", (facts.qid,)).fetchone()
                        if row:
                            eid = row[0]
                        else:
                            eid = upsert_eponym(con, facts.record("wd_match", CONF["wd_match"], False))
                            set_categories(con, eid, facts.categories, "wikidata")
                        state[k] = {"status": "wd_match", "eponym_id": eid, "qid": facts.qid}
                elif r.get("hint") and confident_rule(r):
                    rec, cats = rules_record(pf[k], r, builder.tax, confident_rule(r))
                    rec["key"] = k
                    eid = upsert_eponym(con, rec)
                    set_categories(con, eid, cats, "rules")
                    state[k] = {"status": "rules", "eponym_id": eid, "hint": r["hint"]}
                if eid is None:
                    queue.append(k)
                    state[k] = {"status": "queue", "reasons": _reasons(r, m)}
            for k, s in state.items():
                ids = olds[k]["nh_ids"]
                if s.get("eponym_id"):
                    con.executemany("UPDATE name_history SET eponym_id=? WHERE id=?", [(s["eponym_id"], i) for i in ids])
                    add_provenance(con, "name_history", k, "eponym", str(s["eponym_id"]),
                                   {"family": "family", "wd_match": "wd_match", "rules": "rules"}.get(s["status"], "rules"),
                                   s.get("family_key") or s.get("qid") or s.get("hint"), None)
        rows = dossiers(queue, olds, pf, rules, matches, client, search)
        write_jsonl(paths.dossiers_old, rows)
        write_json(stage_path("oldnames", out_dir),
                   {"names": {k: {**olds[k], **state.get(k, {}), "rules": rules.get(k, {}).get("hint")}
                              for k in olds}})
        cnt = collections.Counter(s["status"] for s in state.values())
        st.update(**{f"status_{k}": v for k, v in sorted(cnt.items())}, dossiers=len(rows))
    return state


def _reasons(r: dict, m: dict | None) -> list[str]:
    from pipeline.enrich.facts import queue_reasons
    return queue_reasons(r, m)


def dossiers(queue: list[str], olds: dict, pf: dict[str, Family], rules: dict, matches: dict,
             client: WikidataClient, search: bool) -> list[dict]:
    from pipeline.enrich.dossiers import search_candidates
    factory = CandidateFactory(client)
    plan = []
    for k in queue:
        m = matches.get(k) or {}
        cids = [(q, "label_match") for q in m.get("candidates", [])]
        r = rules.get(k) or {}
        cids += [(q, "gazetteer") for q in r.get("gaz_qids", [])[:4]]
        reasons = _reasons(r, m) if r else ["unknown"]
        if search and len(cids) < MAX_CANDIDATES:
            cids += [(q, "search") for q in search_candidates(client, pf[k], reasons) if q not in {c for c, _ in cids}]
        plan.append((k, reasons, cids))
    factory.prefetch({q for _, _, c in plan for q, _ in c})
    rows = []
    for k, reasons, cids in plan:
        v = olds[k]
        p = v["parts"]
        from pipeline.enrich.dossiers import name_targets
        targets = name_targets(pf[k])
        cand = [s for s in (factory.summary(q, how) for q, how in cids
                            if how != "search" or factory.label_matches(q, targets)) if s][:MAX_CANDIDATES]
        r = rules.get(k) or {}
        d = {"family_id": fid(k), "entity": "old_name", "old_names": v["names"][:6],
             "current_names": v["current"][:4], "eponym_ka": p["base_ka"],
             "eponym_ka_nominative_guess": p["nom_ka"], "eponym_ru": p["base_ru"], "eponym_en": p["base_en"],
             "rules": {kk: r.get(kk) for kk in ("kind", "sub", "evidence", "titles", "flags", "geo_hits") if r.get(kk)},
             "candidates": cand, "queue_reasons": reasons}
        d = {kk: vv for kk, vv in d.items() if vv not in (None, "", [], {})}
        meta = {"kind": "old_name", "entity": "old_name", "old_key": k, "nh_ids": v["nh_ids"], "reasons": reasons,
                "rules_hint": r.get("hint"), "single_token": r.get("single_token", False), "flags": r.get("flags", []),
                "n_georgian_candidates": sum(1 for c in cand if c.get("is_human") and c.get("georgian"))}
        rows.append({"meta": meta, "dossier": d})
    return rows
