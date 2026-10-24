"""Stage ``dossiers``: evidence packets for the LLM classifiers (the "retriever", no LLM).

For every family in the queue (ambiguous / not found / one-word persons, several people, groups,
concepts, unclear names, uncertain toponyms, QIDs of unknown kind) the dossier holds: names
ka/ru/en, members, former names (``name_history``), rule hints, up to 5 Wikidata candidates
(description, years, P106, P27, P19, intro of the ka/ru Wikipedia articles <= 600 chars) and
encyclopedia facts (``history_article.parsed_json``).

Also built here: a deterministic 10% verification sample of decided families (blind: no answer,
no provenance hints), a gold set for threshold calibration (families with a known QID, built as
if the QID were unknown) and name-QA items (ka/ru/en mismatches, unmatched ``addr:street``).

Output: ``data/enrich/dossiers.jsonl`` -- one line ``{"meta": {...}, "dossier": {...}}`` per packet;
only ``dossier`` is ever shown to a model.
"""
from __future__ import annotations

import collections
import json
import sqlite3

from pipeline.enrich import ka as K
from pipeline.enrich.articles import BioIndex, article_facts
from pipeline.enrich.common import (Family, paths, canonical, fid, sha1, table_exists, write_jsonl)
from pipeline.enrich.taxonomy import load as load_taxonomy
from pipeline.log import get_logger, stage
from pipeline.wikidata.client import WikidataClient
from pipeline.wikidata.entity import (claim_ids, description, is_human, label, labels_all, sitelink, year)

log = get_logger(__name__)

MAX_CANDIDATES = 5
EXTRACT_CHARS = 600
VERIFY_SHARE = 10            # 1 in N decided families goes to the blind verifier
SEARCH_REASONS = {"person_not_found", "person_unmatched", "person_single_token", "person_ambiguous",
                  "persons_multi", "unknown", "concept", "toponym_uncertain", "group", "other", "qid_unavailable"}
HUMAN_REASONS = {"person_not_found", "person_unmatched", "person_single_token", "person_ambiguous", "persons_multi"}


def verify_sampled(key: str, share: int = VERIFY_SHARE) -> bool:
    return int(sha1("verify|" + key)[:8], 16) % share == 0


def old_names(con: sqlite3.Connection) -> dict[int, list[dict]]:
    """family_id -> former names (newest first)."""
    out: dict[int, list[dict]] = collections.defaultdict(list)
    if not table_exists(con, "name_history"):
        return out
    q = ("SELECT COALESCE(nh.family_id, f.family_id) AS fam, nh.name_ka, nh.name_ru, nh.name_lat, nh.from_year, "
         "nh.to_year, nh.source FROM name_history nh LEFT JOIN feature f ON f.id = nh.feature_id")
    seen = set()
    for fam, ka_, ru, lat, y0, y1, src in con.execute(q):
        if fam is None:
            continue
        k = (fam, ka_, ru, lat)
        if k in seen:
            continue
        seen.add(k)
        d = {k2: v for k2, v in (("ka", ka_), ("ru", ru), ("lat", lat), ("from", y0), ("to", y1), ("source", src))
             if v not in (None, "")}
        out[fam].append(d)
    for v in out.values():
        v.sort(key=lambda d: -(d.get("to") or d.get("from") or 0))
    return out


class CandidateFactory:
    """Wikidata candidate summaries (+ Wikipedia intros) for dossiers."""

    def __init__(self, client: WikidataClient, with_extracts: bool = True):
        self.client = client
        self.with_extracts = with_extracts
        self.tax = load_taxonomy()
        self._ents: dict[str, dict] = {}
        self._vents: dict[str, dict] = {}
        self._ext: dict[tuple[str, str], str] = {}

    def prefetch(self, qids: set[str]) -> None:
        need = sorted(q for q in qids if q not in self._ents)
        if not need:
            return
        self._ents.update(self.client.entities(need))
        vals = set()
        for q in need:
            e = self._ents.get(q)
            for p in ("P106", "P27", "P19", "P31", "P17", "P39", "P800", "P463"):
                vals.update(claim_ids(e, p))
        self._vents.update(self.client.entities(sorted(v for v in vals if v not in self._vents),
                                                props="labels|claims", langs="ru|en|ka"))
        if self.with_extracts:
            for wiki, lang in (("kawiki", "ka"), ("ruwiki", "ru")):
                titles = {sitelink(self._ents.get(q), wiki) for q in need} - {""}
                titles = {t for t in titles if (lang, t) not in self._ext}
                if titles:
                    got = self.client.extracts(lang, sorted(titles), chars=EXTRACT_CHARS)
                    for t in titles:
                        self._ext[(lang, t)] = got.get(t, "")

    def label_matches(self, q: str, targets: set[str], human_ok: bool = True) -> bool:
        """Search hits are kept when they are humans (namesakes help) or carry the eponym as a label."""
        e = self._ents.get(q)
        if e is None:
            return False
        if human_ok and is_human(e):
            return True
        for lang, v in labels_all(e):
            if (K.label_key(v) if lang == "ka" else K.norm_latin(v)) in targets:
                return True
        return False

    def _lab(self, q: str) -> str:
        e = self._vents.get(q) or self._ents.get(q)
        return label(e, "ru") or label(e, "en") or label(e, "ka")

    def georgian(self, e: dict) -> bool:
        gs = self.tax.georgian_states
        if set(claim_ids(e, "P27")) & gs or set(claim_ids(e, "P17")) & gs:
            return True
        return any(set(claim_ids(self._vents.get(bp), "P17")) & gs for bp in claim_ids(e, "P19"))

    def summary(self, q: str, found_by: str | None = None) -> dict | None:
        e = self._ents.get(q)
        if e is None:
            return None
        human = is_human(e)
        d: dict = {"qid": q, "label_ka": label(e, "ka"), "label_ru": label(e, "ru"), "label_en": label(e, "en"),
                   "description_ru": description(e, "ru"), "description_en": description(e, "en"),
                   "is_human": human}
        if human:
            d.update(born=year(e, "P569"), died=year(e, "P570"),
                     gender={"Q6581097": "m", "Q6581072": "f"}.get((claim_ids(e, "P21") or [""])[0]),
                     occupations_ru=[self._lab(v) for v in claim_ids(e, "P106")][:5],
                     positions_ru=[self._lab(v) for v in claim_ids(e, "P39")][:3],
                     citizenship_ru=[self._lab(v) for v in claim_ids(e, "P27")][:3],
                     notable_works_ru=[self._lab(v) for v in claim_ids(e, "P800")][:4],
                     member_of_ru=[self._lab(v) for v in claim_ids(e, "P463")][:3],
                     birthplace_ru=next((self._lab(v) for v in claim_ids(e, "P19")), ""))
        else:
            d.update(instance_of_ru=[self._lab(v) for v in claim_ids(e, "P31")][:4],
                     country_ru=[self._lab(v) for v in claim_ids(e, "P17")][:2])
        d["georgian"] = self.georgian(e)
        for wiki, lang in (("kawiki", "ka"), ("ruwiki", "ru")):
            t = sitelink(e, wiki)
            if t:
                d[f"{wiki}_title"] = t
                ext = self._ext.get((lang, t), "")
                if ext:
                    d[f"{wiki}_intro"] = ext[:EXTRACT_CHARS]
        if found_by:
            d["found_by"] = found_by
        return {k: v for k, v in d.items() if v not in (None, "", [])}


def name_targets(f: Family) -> set[str]:
    out = {K.label_key(v) for v in K.ka_label_variants(f.base_ka)} if f.base_ka and K.has_ka(f.base_ka) else set()
    if f.nom_ka:
        out.add(K.label_key(f.nom_ka))
    for v in (f.base_en, f.base_ru):
        if v:
            out.add(K.norm_latin(v))
    return {x for x in out if x}


def search_candidates(client: WikidataClient, f: Family, reasons: list[str], limit: int = 6) -> list[str]:
    human = bool(set(reasons) & HUMAN_REASONS)
    queries: list[str] = []
    if f.base_ka and K.has_ka(f.base_ka):
        if "persons_multi" in reasons and " და " in f.base_ka:
            *heads, last = f.base_ka.split(" და ")
            sur = K.stem_gen(last.split()[-1].replace("ების", "ის")) if last.split() else ""
            for h in heads + [last]:
                toks = h.split()
                nm = " ".join(toks[:-1] + [K.stem_gen(toks[-1])]) if len(toks) > 1 else (toks[0] + " " + sur if toks else "")
                queries.append(nm.strip())
        else:
            v = K.ka_label_variants(f.base_ka)
            queries.append(v[0] if v else f.nom_ka or f.base_ka)
    if f.base_en:
        queries.append(f.base_en)
    elif f.base_ru:
        queries.append(f.base_ru)
    out: list[str] = []
    for q in dict.fromkeys(q for q in queries if q):
        for hit in client.search(q, human=human, limit=limit):
            if hit not in out:
                out.append(hit)
    return out


def build_dossier(f: Family, r: dict, reasons: list[str], cand: list[dict], olds: list[dict],
                  enc: list[dict], blind: bool = False, bios: list[dict] | None = None) -> dict:
    h = f.head
    d = {
        "family_id": f.fid,
        "entity": "family",
        "names": {k: v for k, v in (("ka", h.ka if h else ""), ("ru", h.ru if h else ""), ("en", h.en if h else ""))
                  if v},
        "eponym_ka": f.base_ka,
        "eponym_ka_nominative_guess": f.nom_ka or (K.nominative(f.base_ka) if f.base_ka else ""),
        "eponym_ru": f.base_ru,
        "eponym_en": f.base_en,
        "members": f.member_names(12),
        "n_members": len({m.ka for m in f.members}),
        "old_names": olds[:8],
        "rules": {k: r.get(k) for k in ("kind", "sub", "evidence", "titles", "flags", "geo_hits") if r.get(k)},
        "candidates": cand[:MAX_CANDIDATES],
        "encyclopedia": enc[:3],
        "encyclopedia_bio": (bios or [])[:3],
    }
    if not blind:
        d["queue_reasons"] = reasons
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def candidate_ids(f: Family, reasons: list[str], r: dict, m: dict | None, q: dict | None,
                  fstate: dict | None, gold: bool) -> list[tuple[str, str]]:
    """(qid, found_by) in priority order, before search."""
    out: list[tuple[str, str]] = []
    if m:
        out += [(x, "label_match") for x in m.get("candidates", [])]
    if q and not gold:
        out += [(x, "osm_tag" if q["source"] == "osm" else "wikidata_street") for x in q["qids"]]
        out += [(x, "wikidata_street") for x in q.get("p138_disagree", [])]
    if fstate and fstate.get("qids") and not gold:
        out += [(x, "known") for x in fstate["qids"]]
    if r.get("gaz_qids"):
        out += [(x, "gazetteer") for x in r["gaz_qids"][:4]]
    seen, uniq = set(), []
    for x, how in out:
        if x not in seen:
            seen.add(x)
            uniq.append((x, how))
    return uniq


def name_qa_items(con: sqlite3.Connection, fams: list[Family], rules: dict[str, dict], limit: int = 400) -> list[dict]:
    items = []
    for f in fams:
        r = rules.get(f.key) or {}
        if "ka_ru_en_mismatch" in r.get("flags", []):
            items.append({"item_id": "N" + sha1("fam|" + f.key)[:8], "kind": "name_mismatch",
                          "family_key": f.key, "members": f.member_names(6),
                          "eponym_ka": f.base_ka, "eponym_ru": f.base_ru, "eponym_en": f.base_en})
    if table_exists(con, "house"):
        q = ("SELECT h.addr_street, COUNT(*) AS n, MAX(f.ka), MAX(f.ru), MAX(h.match) FROM house h "
             "LEFT JOIN feature f ON f.id = h.feature_id WHERE h.match IN ('fuzzy','none') AND h.addr_street IS NOT NULL "
             "GROUP BY h.addr_street ORDER BY n DESC LIMIT ?")
        for street, n, fka, fru, match in con.execute(q, (limit,)):
            items.append({"item_id": "N" + sha1("addr|" + street)[:8], "kind": "addr_typo", "addr_street": street,
                          "count": n, "match": match,
                          "matched_feature": {k: v for k, v in (("ka", fka), ("ru", fru)) if v} or None})
    return [{k: v for k, v in it.items() if v not in (None, "", [], {})} for it in items[:limit]]


def run(con: sqlite3.Connection, fams: list[Family], rules: dict, qids: dict, match: dict, facts_state: dict,
        client: WikidataClient, search: bool = True, extracts: bool = True, gold: bool = True,
        verify: bool = True, out_path=None, extra: list[dict] | None = None) -> list[dict]:
    """Build all dossiers; ``extra`` = already built old-name dossiers to append."""
    with stage(log, "enrich.dossiers", families=len(fams)) as st:
        olds = old_names(con)
        enc = article_facts(con)
        bio_index = BioIndex(con)
        factory = CandidateFactory(client, with_extracts=extracts)
        plan: list[tuple[Family, str, list[str], list[tuple[str, str]]]] = []
        for f in fams:
            fs = facts_state.get(f.key) or {}
            r = rules.get(f.key) or {}
            if fs.get("status") == "queue":
                reasons = fs.get("reasons") or ["unknown"]
                cids = candidate_ids(f, reasons, r, match.get(f.key), qids.get(f.key), fs, gold=False)
                if search and len(cids) < MAX_CANDIDATES and set(reasons) & SEARCH_REASONS:
                    cids += [(x, "search") for x in search_candidates(client, f, reasons) if x not in {c for c, _ in cids}]
                plan.append((f, "queue", reasons, cids))
            elif fs.get("status") == "decided" and verify and verify_sampled(f.key):
                cids = candidate_ids(f, [], r, match.get(f.key), qids.get(f.key), fs, gold=False)
                plan.append((f, "verify", [], cids))
            if gold and f.key in qids and len(qids[f.key]["qids"]) == 1 and not qids[f.key].get("conflict"):
                m = match.get(f.key)
                cids = candidate_ids(f, [], r, m, None, None, gold=True)
                reasons = ["person_ambiguous" if m and m.get("status") == "ambiguous" else
                           "person_not_found" if m and m.get("status") == "not_found" else "unknown"]
                if search and len(cids) < MAX_CANDIDATES:
                    cids += [(x, "search") for x in search_candidates(client, f, reasons) if x not in {c for c, _ in cids}]
                plan.append((f, "gold", reasons, cids))
        allq = {q for _, _, _, c in plan for q, _ in c}
        st["candidate_qids"] = len(allq)
        factory.prefetch(allq)
        rows: list[dict] = []
        for f, kind, reasons, cids in plan:
            r = rules.get(f.key) or {}
            cand = []
            targets = name_targets(f)
            for q, how in cids:
                if how == "search" and not factory.label_matches(q, targets, human_ok=True):
                    continue                    # full-text noise ("observatory" -> Palomar Observatory)
                s = factory.summary(q, None if kind in ("verify", "gold") or how == "known" else how)
                if s is not None:
                    cand.append(s)
                if len(cand) >= MAX_CANDIDATES:
                    break
            # rank humans first for person reasons, Georgian first, keep retrieval order otherwise
            if set(reasons) & HUMAN_REASONS:
                cand.sort(key=lambda c: (not c.get("is_human"), not c.get("georgian")))
            bios = bio_index.lookup(f.base_ka) if r.get("kind") in ("person", "persons_multi") and f.base_ka else []
            d = build_dossier(f, r, reasons, cand, olds.get(f.id, []), enc.get(f.id, []),
                              blind=kind in ("verify", "gold"), bios=bios)
            meta = {"kind": kind, "family_key": f.key, "entity": "family", "reasons": reasons,
                    "rules_hint": r.get("hint"), "n_georgian_candidates": sum(1 for c in cand if c.get("is_human")
                                                                               and c.get("georgian")),
                    "single_token": r.get("single_token", False),
                    "flags": r.get("flags", [])}
            if kind == "gold":
                meta["gold_qid"] = qids[f.key]["qids"][0]
                meta["gold_match_status"] = (match.get(f.key) or {}).get("status")
            if kind == "verify":
                fs = facts_state.get(f.key) or {}
                meta["decided_source"] = fs.get("source")
            rows.append({"meta": meta, "dossier": d})
        for it in name_qa_items(con, fams, rules):
            rows.append({"meta": {"kind": "name_qa", "item_key": it["item_id"], "entity": "name"}, "dossier": it})
        for x in extra or []:
            rows.append(x)
        write_jsonl(out_path or paths.dossiers, rows)
        cnt = collections.Counter(x["meta"]["kind"] for x in rows)
        st.update(**{f"n_{k}": v for k, v in sorted(cnt.items())},
                  with_candidates=sum(1 for x in rows if x["dossier"].get("candidates")))
    return rows
