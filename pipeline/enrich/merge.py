"""Stage ``merge``: validate subagent answers, apply thresholds, write eponyms.

1. Ingest ``data/enrich/results/<role>/<batch>.json``: JSON Schema validation (whole file, then
   item by item to salvage valid items), family id must belong to the batch file, cache in
   ``llm_result`` under ``sha1(prompt_version | model | dossier)``.
2. Route every queued dossier that has a classifier answer:
   * ``accept``   -- confidence >= 0.85, qid_confidence >= 0.8 (when a QID is chosen), no conflict
                     with the rules, no manual trigger;
   * ``second_opinion`` -- 0.6 <= confidence < 0.85 (or a weak QID): goes to eponym-disambiguator;
     accepted when the disambiguator agrees with >= 0.85 / >= 0.8, otherwise manual;
   * ``verify_conflict`` -- rules and LLM disagree on the kind: the blind verifier decides
     (agrees -> accept, else manual);
   * ``manual``  -- < 0.6, one-word name, >= 2 Georgian candidate namesakes, a person "from
     model knowledge" without a QID, ka/ru/en mismatch in OSM, QID outside the candidates,
     kind/category mismatch, model asked for review, verifier disagreement.
   Thresholds can be overridden by ``data/enrich/thresholds.json`` (written by ``calibrate``).
3. Write ``eponym`` / ``eponym_category`` (``source='llm'``, ``needs_review`` unless accepted),
   ``family.eponym_id`` / ``name_history.eponym_id``, ``provenance``; apply review decisions.
"""
from __future__ import annotations

import collections
import json
import sqlite3
from pathlib import Path

from pipeline.enrich import ka as K
from pipeline.enrich.common import (Family, paths, add_provenance,
                                    agent_prompt_version, clear_provenance, llm_cache_key, read_json, read_jsonl,
                                    stage_path, write_json)
from pipeline.enrich.dossiers import verify_sampled
from pipeline.enrich.facts import EponymBuilder, multi_record
from pipeline.enrich.schemas import item_validator, validator
from pipeline.enrich.store import delete_orphans, link_family, set_categories, unlink_stale, upsert_eponym
from pipeline.enrich.taxonomy import load as load_taxonomy
from pipeline.log import get_logger, stage

log = get_logger(__name__)

DEFAULT_THRESHOLDS = {"accept": 0.85, "qid_accept": 0.8, "second_low": 0.6}
PERSONISH = {"person", "persons_multi", "fictional_character"}
STRONG_PERSON_SUBS = {"full_name", "title_epithet", "multi", "mononym"}
NO_EVIDENCE = {"knowledge", "name", "name_ru", "name_en", "family", "rules"}
KIND_CAT_PREFIX = {
    "person": ("lit.", "power.", "mil.", "rel.", "sci.", "art.", "sport.", "biz.", "none.unknown"),
    "persons_multi": ("lit.", "power.", "mil.", "rel.", "sci.", "art.", "sport.", "biz.", "group.", "none.unknown"),
    "fictional_character": ("lit.character", "rel.", "none.unknown"),
    "group": ("group.", "mil.hero", "none.unknown"),
    "toponym": ("place.", "event.battle", "group.ethnos", "none.unknown"),
    "event_date": ("event.", "rel.feast", "none.unknown"),
    "nature": ("nature.", "place.water", "place.mountain", "none.unknown"),
    "concept": ("concept.", "rel.feast", "group.profession", "event.history", "none.unknown"),
    "object": ("concept.", "none.unknown"),
    "no_eponym": ("none.",),
    "unknown": ("none.unknown",),
}


def thresholds() -> dict:
    t = dict(DEFAULT_THRESHOLDS)
    t.update({k: v for k, v in (read_json(paths.thresholds, {}) or {}).items() if k in t})
    return t


# ---------------------------------------------------------------------------------- ingest
def ingest(con: sqlite3.Connection, role: str, batch_dir: Path | None = None, result_dir: Path | None = None,
           keys: dict[str, str] | None = None) -> dict:
    """Validate result files of ``role`` and cache valid answers in ``llm_result``.

    ``keys`` maps dossier ids (F.../N...) to family / old-name keys stored in ``llm_result.family_key``.
    """
    batch_dir = batch_dir or paths.batches
    result_dir = result_dir or paths.results
    stats = collections.Counter()
    rdir = result_dir / role
    if not rdir.exists():
        return dict(stats)
    pv, model = agent_prompt_version(role)
    v_file, v_item = validator(role), item_validator(role)
    id_field = "item_id" if role == "name_qa" else "family_id"
    for res_path in sorted(rdir.glob("*.json")):
        batch = res_path.stem
        lines = read_jsonl(batch_dir / role / f"{batch}.jsonl")
        if not lines:
            log.error("merge: %s has no batch file %s", res_path, batch)
            stats["orphan_files"] += 1
            continue
        by_id = {d.get(id_field): d for d in lines}
        try:
            doc = json.loads(res_path.read_text(encoding="utf-8-sig"))
        except ValueError as e:
            log.error("merge: %s is not JSON: %s", res_path, e)
            stats["bad_files"] += 1
            continue
        if isinstance(doc, list):
            doc = {"results": doc}
        items = doc.get("results") if isinstance(doc, dict) else None
        if not isinstance(items, list):
            log.error("merge: %s has no results array", res_path)
            stats["bad_files"] += 1
            continue
        errs = sorted(v_file.iter_errors(doc), key=lambda e: list(e.path))
        if errs:
            stats["files_with_errors"] += 1
            log.warning("merge: %s fails the schema (%d errors), salvaging valid items; first: %s at %s",
                        res_path.name, len(errs), errs[0].message[:200], list(errs[0].path))
        with con:
            for it in items:
                if not isinstance(it, dict):
                    stats["items_invalid"] += 1
                    continue
                ie = list(v_item.iter_errors(it))
                if ie:
                    stats["items_invalid"] += 1
                    log.warning("merge: %s item %s invalid: %s", res_path.name, it.get(id_field), ie[0].message[:200])
                    continue
                d = by_id.get(it[id_field])
                if d is None:
                    stats["items_unknown_id"] += 1
                    log.warning("merge: %s item %s not in batch", res_path.name, it[id_field])
                    continue
                ck = llm_cache_key(pv, model, d)
                payload = {"answer": it, "file": f"{role}/{res_path.name}", "dossier_id": it[id_field]}
                if role == "disambiguator":
                    payload["answer_of_classifier"] = d.get("classifier_answer")
                known = (keys or {}).get(it[id_field])
                con.execute("INSERT INTO llm_result(cache_key, role, model, family_key, batch, payload) "
                            "VALUES (?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, "
                            "batch=excluded.batch, family_key=COALESCE(?, llm_result.family_key)",
                            (ck, role, model, known or it[id_field], batch, json.dumps(payload, ensure_ascii=False),
                             known))
                stats["items_ok"] += 1
    if stats:
        log.info("merge: ingest %s %s", role, dict(stats))
    return dict(stats)


def cached_answer(con: sqlite3.Connection, ck: str) -> dict | None:
    row = con.execute("SELECT payload FROM llm_result WHERE cache_key=?", (ck,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return None


def latest_by_family(con: sqlite3.Connection, role: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = collections.defaultdict(list)
    for fam, payload in con.execute("SELECT family_key, payload FROM llm_result WHERE role=? ORDER BY created_at",
                                    (role,)):
        try:
            out[fam].append(json.loads(payload))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------------- decide
def qids_of(ans: dict) -> list[str]:
    out = [e["wikidata_qid"] for e in ans.get("eponyms", []) if e.get("wikidata_qid")]
    top = (ans.get("toponym") or {}).get("wikidata_qid")
    if top and top not in out:
        out.append(top)
    return out


def same_answer(a: dict, b: dict) -> bool:
    ka, kb = a.get("eponym_kind"), b.get("eponym_kind")
    group = lambda k: "person" if k in ("person", "persons_multi") else k  # noqa: E731
    if group(ka) != group(kb):
        return False
    return set(qids_of(a)) == set(qids_of(b))


def check(ans: dict, dossier: dict) -> tuple[dict, list[str]]:
    """Semantic checks beyond the schema; returns a cleaned copy and issues."""
    tax = load_taxonomy()
    a = json.loads(json.dumps(ans))
    issues: list[str] = []
    cands = {c.get("qid") for c in dossier.get("candidates", [])}
    for e in a.get("eponyms", []):
        if e.get("wikidata_qid") and e["wikidata_qid"] not in cands:
            issues.append("qid_not_in_candidates")
            e["wikidata_qid"] = None
    top = a.get("toponym")
    if top and top.get("wikidata_qid") and top["wikidata_qid"] not in cands:
        issues.append("qid_not_in_candidates")
        top["wikidata_qid"] = None
    cats = [c for c in a.get("categories", []) if tax.valid(c)]
    if len(cats) != len(a.get("categories", [])):
        issues.append("category_not_in_taxonomy")
    allowed = KIND_CAT_PREFIX.get(a.get("eponym_kind"), ())
    bad = [c for c in cats if not c.startswith(allowed)]
    if bad:
        issues.append("kind_category_mismatch")
    a["categories"] = cats
    if qids_of(a) and a.get("qid_confidence") is None:
        issues.append("qid_without_confidence")
    return a, sorted(set(issues))


def rules_conflict(meta: dict, ans: dict) -> bool:
    hint = meta.get("rules_hint") or ""
    if ":" not in hint:
        return False
    rkind, sub = hint.split(":", 1)
    k = ans.get("eponym_kind")
    if rkind in ("person", "persons_multi") and sub in STRONG_PERSON_SUBS:
        return k not in PERSONISH and k != "group"
    if rkind == "toponym" and sub not in ("route",):
        return k in ("person", "persons_multi")
    if rkind == "event_date":
        return k != "event_date"
    return False


def manual_triggers(meta: dict, ans: dict, dossier: dict, t: dict) -> list[str]:
    out = []
    k = ans.get("eponym_kind")
    if ans["confidence"] < t["second_low"]:
        out.append("low_confidence")
    if meta.get("single_token") and k == "person":
        out.append("surname_only")
    if k in ("person", "persons_multi") and (meta.get("n_georgian_candidates") or 0) >= 2:
        out.append("georgian_namesakes")
    if k in ("person", "persons_multi") and not qids_of(ans):
        srcs = {e.get("source") for e in ans.get("evidence", [])}
        if not srcs or srcs <= NO_EVIDENCE:
            out.append("knowledge_without_qid")
    if "ka_ru_en_mismatch" in (meta.get("flags") or []):
        out.append("ka_ru_en_mismatch")
    if ans.get("needs_review"):
        out.append("model_review:" + (ans.get("review_reason") or "unspecified"))
    return out


def route(meta: dict, ans: dict, dossier: dict, t: dict, dis: dict | None, ver: dict | None) -> dict:
    a, issues = check(ans, dossier)
    conf = float(a["confidence"])
    qconf = a.get("qid_confidence")
    has_q = bool(qids_of(a))
    trig = manual_triggers(meta, a, dossier, t) + issues
    conflict = rules_conflict(meta, a)
    out = {"answer": a, "issues": issues, "conflict": conflict, "triggers": trig, "confidence": conf,
           "qid_confidence": qconf}
    strong_q = (not has_q) or (qconf is not None and qconf >= t["qid_accept"])
    if ver is not None and not same_answer(a, ver):
        out.update(route="manual", reason="verifier_disagrees", needs_verifier=False)
        return out
    if trig:
        out.update(route="manual", reason=trig[0])
        return out
    if conflict:
        if ver is None:
            out.update(route="verify_conflict", reason="rules_conflict", needs_verifier=True)
        else:
            out.update(route="accept", reason="verifier_agrees_over_rules")
        return out
    if conf >= t["accept"] and strong_q:
        out.update(route="accept", reason="thresholds")
        if verify_sampled(meta.get("family_key", "")):
            out["needs_verifier"] = ver is None
        return out
    # second opinion
    if dis is None:
        out.update(route="second_opinion", reason="mid_confidence" if conf < t["accept"] else "weak_qid")
        return out
    d_a, d_issues = check(dis, dossier)
    d_ok = (d_a["confidence"] >= t["accept"] and
            ((not qids_of(d_a)) or (d_a.get("qid_confidence") or 0) >= t["qid_accept"]) and not d_issues)
    if same_answer(a, d_a) and d_ok:
        out.update(route="accept", reason="disambiguator_agrees", answer=d_a, confidence=float(d_a["confidence"]),
                   qid_confidence=d_a.get("qid_confidence"))
        return out
    out.update(route="manual", reason="disambiguator_disagrees" if not same_answer(a, d_a) else "disambiguator_weak",
               disambiguator=d_a)
    return out


# ---------------------------------------------------------------------------------- write
def answer_record(a: dict, meta: dict, dossier: dict, builder: EponymBuilder, conf: float, review: bool,
                  key: str) -> tuple[dict, list[str], str]:
    """(eponym record, categories, category source) for an LLM answer."""
    tax = builder.tax
    qs = qids_of(a)
    cats = [c for c in a.get("categories", []) if tax.valid(c)]
    eps = a.get("eponyms", [])
    kind = a["eponym_kind"]
    if qs:
        facts = builder.build(qs)
        parts = [facts[q] for q in qs if q in facts]
        if len(parts) == 1 and len(eps) <= 1:
            p = parts[0]
            rec = p.record("llm", conf, review)
            if p.kind == "unknown" or (kind in PERSONISH) != (p.kind in PERSONISH):
                rec["kind"] = kind
            if not rec.get("short_ru") and eps:
                rec["short_ru"] = eps[0].get("short_ru") or None
            return rec, (p.categories or cats), ("wikidata" if p.categories else "llm")
        if len(parts) >= 2 and len(parts) == len(qs) and kind == "persons_multi":
            rec, mcats = multi_record(key, parts, "llm", conf, review)
            rec["key"] = key
            return rec, (mcats or cats), "wikidata"
    if len(eps) == 1:
        e = eps[0]
        rec = {"qid": None, "key": key, "kind": kind, "ka": e.get("name_ka") or None, "ru": e.get("name_ru") or None,
               "en": dossier.get("eponym_en") or None, "gender": e.get("gender"), "birth_year": e.get("birth_year"),
               "death_year": e.get("death_year"), "origin": e.get("origin") or "unknown",
               "short_ru": e.get("short_ru") or None, "occupations_ru": None}
    else:
        def j(f: str, sep: str) -> str | None:
            vals = [e.get(f) for e in eps if e.get(f)]
            return sep.join(vals) if vals else None
        top = a.get("toponym") or {}
        genders = {e.get("gender") for e in eps}
        by = [e["birth_year"] for e in eps if e.get("birth_year") is not None]
        dy = [e["death_year"] for e in eps if e.get("death_year") is not None]
        origins = {e.get("origin") for e in eps}
        rec = {"qid": None, "key": key, "kind": kind,
               "ka": j("name_ka", " და ") or (K.nominative(dossier.get("eponym_ka", "")) or None),
               "ru": j("name_ru", " и ") or top.get("name_ru") or dossier.get("eponym_ru") or None,
               "en": dossier.get("eponym_en") or None, "gender": genders.pop() if len(genders) == 1 else None,
               "birth_year": min(by) if by else None, "death_year": max(dy) if dy else None,
               "origin": origins.pop() if len(origins) == 1 else ("georgian" if "georgian" in origins else "unknown"),
               "short_ru": j("short_ru", "; ") if eps else None, "occupations_ru": None}
        if rec["short_ru"]:
            rec["short_ru"] = rec["short_ru"][:120]
        if kind == "toponym" and top.get("scope"):
            rec["origin"] = "foreign" if top["scope"] == "abroad" else "georgian"
    rec.update(confidence=conf, source="llm", needs_review=review)
    return rec, cats, "llm"


def run(con: sqlite3.Connection, fams: list[Family], dossier_rows: list[dict], builder: EponymBuilder,
        batch_dir: Path | None = None, result_dir: Path | None = None, out_dir=None) -> dict:
    t = thresholds()
    with stage(log, "enrich.merge", dossiers=len(dossier_rows), **t) as st:
        keys = dossier_keys(dossier_rows)
        for role in ("classifier", "disambiguator", "verifier", "name_qa"):
            for k, v in ingest(con, role, batch_dir, result_dir, keys).items():
                st[f"{role}_{k}"] = v
        pv = {r: agent_prompt_version(r) for r in ("classifier", "disambiguator", "verifier", "name_qa")}
        dis_by = latest_by_family(con, "disambiguator")
        state: dict = {"thresholds": t, "families": {}, "review": [], "verifier_disagreements": [],
                       "name_qa": []}
        routes = collections.Counter()
        llm_linked: set[str] = set()
        from pipeline.enrich.batches import blind
        # prefetch Wikidata facts of every QID chosen in any cached answer (one batched pass)
        all_q = set()
        for (payload,) in con.execute("SELECT payload FROM llm_result WHERE role IN ('classifier','disambiguator')"):
            try:
                all_q.update(qids_of(json.loads(payload).get("answer") or {}))
            except (ValueError, AttributeError):
                continue
        if all_q:
            builder.build(sorted(all_q))
        with con:
            clear_provenance(con, ["eponym", "translation_ru"], ["llm"])
            clear_provenance(con, ["eponym", "translation_ru"], ["llm"], entity="name_history")
            for row in dossier_rows:
                meta, d = row["meta"], row["dossier"]
                kind = meta["kind"]
                if kind == "name_qa":
                    a = cached_answer(con, llm_cache_key(*pv["name_qa"], d))
                    if a:
                        state["name_qa"].append({"item": d, "answer": a["answer"]})
                    continue
                if kind == "verify":
                    ver = cached_answer(con, llm_cache_key(*pv["verifier"], blind(d)))
                    if ver:
                        eid = con.execute("SELECT eponym_id FROM family WHERE key=?", (meta["family_key"],)).fetchone()
                        verdict = verifier_vs_decided(con, eid[0] if eid else None, ver["answer"])
                        if verdict:
                            state["verifier_disagreements"].append({"family_key": meta["family_key"],
                                                                    "family_id": d["family_id"], "why": verdict,
                                                                    "verifier": ver["answer"]})
                            if eid and eid[0]:
                                con.execute("UPDATE eponym SET needs_review=1 WHERE id=?", (eid[0],))
                    continue
                if kind not in ("queue", "old_name"):
                    continue
                cls = cached_answer(con, llm_cache_key(*pv["classifier"], d))
                fam_id = d["family_id"]
                if cls is None:
                    state["families"][fam_id] = {"route": "pending", "family_key": meta.get("family_key"),
                                                 "entity": meta["entity"]}
                    routes["pending"] += 1
                    continue
                ans = cls["answer"]
                ekey = meta.get("family_key") or meta.get("old_key")
                dis = next((x["answer"] for x in reversed(dis_by.get(ekey, []))
                            if x.get("answer_of_classifier") == ans), None)
                ver_p = cached_answer(con, llm_cache_key(*pv["verifier"], blind(d)))
                ver = ver_p["answer"] if ver_p else None
                r = route(meta, ans, d, t, dis, ver)
                routes[r["route"]] += 1
                accepted = r["route"] == "accept"
                key = ("fam:" + meta["family_key"]) if meta["entity"] == "family" else meta["old_key"]
                rec, cats, csrc = answer_record(r["answer"], meta, d, builder, r["confidence"], not accepted, key)
                existing = None
                if rec.get("qid"):
                    existing = con.execute("SELECT id, source FROM eponym WHERE qid=?", (rec["qid"],)).fetchone()
                if existing is not None and existing[1] not in ("llm",):
                    eid = existing[0]
                    if not accepted:
                        con.execute("UPDATE eponym SET needs_review=1 WHERE id=? AND source != 'manual'", (eid,))
                else:
                    eid = upsert_eponym(con, rec)
                    set_categories(con, eid, cats, csrc)
                if meta["entity"] == "family":
                    link_family(con, meta["family_key"], eid)
                    llm_linked.add(meta["family_key"])
                else:
                    ids = meta.get("nh_ids") or []
                    con.executemany("UPDATE name_history SET eponym_id=? WHERE id=?", [(eid, i) for i in ids])
                ent_name = "family" if meta["entity"] == "family" else "name_history"
                ent_key = meta.get("family_key") or meta.get("old_key")
                add_provenance(con, ent_name, ent_key, "eponym", rec.get("qid") or rec.get("key"), "llm",
                               cls.get("file"), r["confidence"])
                tr = (r["answer"].get("translation_ru") or "").strip()
                if tr and r["answer"]["eponym_kind"] not in PERSONISH:
                    add_provenance(con, ent_name, ent_key, "translation_ru", tr, "llm", cls.get("file"),
                                   r["confidence"])
                info = {"route": r["route"], "reason": r.get("reason"), "family_key": meta.get("family_key"),
                        "old_key": meta.get("old_key"), "entity": meta["entity"], "confidence": r["confidence"],
                        "qid_confidence": r.get("qid_confidence"), "qids": qids_of(r["answer"]),
                        "kind": r["answer"]["eponym_kind"], "categories": r["answer"].get("categories", []),
                        "triggers": r["triggers"], "conflict": r["conflict"], "answer": ans,
                        "needs_verifier": bool(r.get("needs_verifier")), "eponym_id": eid}
                if r.get("disambiguator"):
                    info["disambiguator"] = r["disambiguator"]
                if ver is not None:
                    info["verifier"] = ver
                state["families"][fam_id] = info
                if r["route"] == "manual":
                    state["review"].append(fam_id)
            st["unlinked_stale"] = unlink_stale(con, llm_linked, ("llm",))
            st["old_names_via_family"] = propagate_old_names(con)
        from pipeline.enrich.review import apply_decisions
        st["review_applied"] = apply_decisions(con, builder)
        with con:
            st["orphans_deleted"] = delete_orphans(con)
        state["summary"] = dict(routes)
        write_json(stage_path("merge", out_dir), state)
        if state["name_qa"]:
            write_json(paths.build / "name_qa_suggestions.json",
                       [{"item": x["item"], "suggestion": x["answer"]} for x in state["name_qa"]
                        if x["answer"].get("issue") != "ok"])
        st.update(**{f"route_{k}": v for k, v in sorted(routes.items())},
                  verifier_disagreements=len(state["verifier_disagreements"]), name_qa=len(state["name_qa"]))
    return state


def propagate_old_names(con: sqlite3.Connection) -> int:
    """Former names equal to a current family's eponym share that family's (possibly LLM) eponym."""
    st = read_json(stage_path("oldnames"), {}) or {}
    n = 0
    for k, v in (st.get("names") or {}).items():
        if v.get("status") != "family" or not v.get("family_key"):
            continue
        row = con.execute("SELECT eponym_id FROM family WHERE key=?", (v["family_key"],)).fetchone()
        if row and row[0]:
            con.executemany("UPDATE name_history SET eponym_id=? WHERE id=?", [(row[0], i) for i in v.get("nh_ids", [])])
            n += len(v.get("nh_ids", []))
    return n


def dossier_keys(rows: list[dict]) -> dict[str, str]:
    out = {}
    for r in rows:
        d, m = r["dossier"], r["meta"]
        i = d.get("family_id") or d.get("item_id")
        k = m.get("family_key") or m.get("old_key") or m.get("item_key")
        if i and k:
            out[i] = k
    return out


def verifier_vs_decided(con: sqlite3.Connection, eponym_id: int | None, ver: dict) -> str | None:
    """Compare a blind verifier answer with a decision made without the LLM. None = agrees."""
    if eponym_id is None:
        return "no decision linked"
    row = con.execute("SELECT kind, qid, key FROM eponym WHERE id=?", (eponym_id,)).fetchone()
    if row is None:
        return "eponym missing"
    kind, qid, key = row
    vk = ver.get("eponym_kind")
    group = lambda k: "person" if k in ("person", "persons_multi") else k  # noqa: E731
    if group(vk) != group(kind):
        return f"kind {kind} vs verifier {vk}"
    vq = set(qids_of(ver))
    if qid and vq and qid not in vq:
        return f"qid {qid} vs verifier {sorted(vq)}"
    if key and key.startswith("multi:") and vq and not vq <= set(key[6:].split("|")):
        return f"qids {key[6:]} vs verifier {sorted(vq)}"
    return None
