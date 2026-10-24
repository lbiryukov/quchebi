"""Stage ``calibrate``: choose merge thresholds on the gold set (families with a known QID).

Gold dossiers (``kNNN.jsonl``) are built as if the QID were unknown. After the classifier has
answered them, every (accept, qid_accept) pair on a grid is replayed through the merge routing;
precision = share of auto-accepted answers whose QID equals the known one. The loosest pair with
precision >= target (default 97%) and the best coverage is recommended (``--apply`` writes
``data/enrich/thresholds.json``). The rule matcher's control results are reported alongside.
"""
from __future__ import annotations

import sqlite3

from pipeline.enrich.common import (agent_prompt_version, paths, llm_cache_key, read_json, stage_path,
                                    write_json)
from pipeline.enrich.merge import DEFAULT_THRESHOLDS, cached_answer, qids_of, route
from pipeline.log import get_logger, stage

log = get_logger(__name__)

GRID = [round(0.5 + 0.05 * i, 2) for i in range(10)]


def evaluate(gold: list[tuple[dict, dict, dict]], t: dict) -> dict:
    """gold = [(meta, dossier, answer)] -> accepted / correct / precision / coverage."""
    acc = ok = kind_ok = 0
    errors = []
    for meta, d, ans in gold:
        r = route(meta, ans, d, t, None, None)
        good_kind = r["answer"]["eponym_kind"] in ("person", "persons_multi", "fictional_character") \
            if meta.get("gold_is_human", True) else True
        kind_ok += good_kind
        if r["route"] != "accept" or not qids_of(r["answer"]):
            continue
        acc += 1
        if meta["gold_qid"] in qids_of(r["answer"]):
            ok += 1
        else:
            errors.append({"family_key": meta["family_key"], "gold": meta["gold_qid"], "got": qids_of(r["answer"]),
                           "confidence": r["confidence"], "qid_confidence": r.get("qid_confidence")})
    n = len(gold)
    return {"n": n, "accepted": acc, "correct": ok, "precision": round(ok / acc, 4) if acc else None,
            "coverage": round(acc / n, 4) if n else None, "kind_accuracy": round(kind_ok / n, 4) if n else None,
            "errors": errors[:30]}


def run(con: sqlite3.Connection, dossier_rows: list[dict], target: float = 0.97, apply: bool = False) -> dict:
    with stage(log, "enrich.calibrate") as st:
        pv, model = agent_prompt_version("classifier")
        gold = []
        n_gold = 0
        for row in dossier_rows:
            meta, d = row["meta"], row["dossier"]
            if meta["kind"] != "gold":
                continue
            n_gold += 1
            a = cached_answer(con, llm_cache_key(pv, model, d))
            if a:
                gold.append((meta, d, a["answer"]))
        st["gold_dossiers"], st["gold_answered"] = n_gold, len(gold)
        grid = []
        for ta in GRID:
            for tq in GRID:
                t = dict(DEFAULT_THRESHOLDS, accept=ta, qid_accept=tq)
                e = evaluate(gold, t)
                grid.append({"accept": ta, "qid_accept": tq, **{k: v for k, v in e.items() if k != "errors"}})
        ok = [g for g in grid if g["precision"] is not None and g["precision"] >= target and g["accepted"] >= 5]
        best = max(ok, key=lambda g: (g["coverage"], -g["accept"], -g["qid_accept"])) if ok else None
        default_eval = evaluate(gold, DEFAULT_THRESHOLDS)
        by_status = {}
        for s in sorted({m.get("gold_match_status") or "none" for m, _, _ in gold}):
            sub = [g for g in gold if (g[0].get("gold_match_status") or "none") == s]
            by_status[s] = {k: v for k, v in evaluate(sub, DEFAULT_THRESHOLDS).items() if k != "errors"}
        match = read_json(stage_path("match"), {}) or {}
        ctl = [m for m in match.values() if "control" in m]
        matcher = {"control": len(ctl),
                   "unique": sum(1 for m in ctl if m["status"] == "unique"),
                   "unique_correct": sum(1 for m in ctl if m["status"] == "unique" and m["chosen"] == m["control"]),
                   "ambiguous_truth_in_candidates": sum(1 for m in ctl if m["status"] == "ambiguous"
                                                        and m["control"] in m.get("candidates", []))}
        if matcher["unique"]:
            matcher["precision"] = round(matcher["unique_correct"] / matcher["unique"], 4)
        report = {"target_precision": target, "gold_dossiers": n_gold, "gold_answered": len(gold),
                  "defaults": DEFAULT_THRESHOLDS, "default_eval": default_eval, "default_by_match_status": by_status,
                  "recommended": best,
                  "matcher_control": matcher, "grid": grid}
        write_json(paths.calibration, report)
        if apply and best:
            write_json(paths.thresholds, dict(DEFAULT_THRESHOLDS, accept=best["accept"], qid_accept=best["qid_accept"]))
            log.info("calibrate: thresholds written to %s", paths.thresholds)
        st.update(recommended=best and {k: best[k] for k in ("accept", "qid_accept", "precision", "coverage")},
                  default_precision=default_eval["precision"], matcher_precision=matcher.get("precision"))
    return report
