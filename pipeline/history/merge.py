"""Stage merge: validate ``history-extractor`` answers and store them.

For every ``data/enrich/results/history/hNNN.json``:

1. JSON Schema validation (``pipeline/history/schema.json``, strict: unknown fields rejected);
2. the ``batch`` must match the file name and every result id must belong to that batch;
3. anti-hallucination checks against the batch text: a former name is kept only when its
   distinctive word (stem) occurs in the article text; evidence quotes that are not found in the
   text lower the confidence; ``from_year > to_year`` drops ``to_year``;
4. accepted payloads go to ``llm_result`` (role ``history``, keyed by the article key, so they
   survive OSM rebuilds) and ``name_history`` is rebuilt (rows with ``source = 'enc2008_llm'``
   replace the parser rows of the same article).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

import jsonschema

from pipeline.db import tx
from pipeline.history import georgian as G
from pipeline.history.batches import BATCH_DIR, RESULT_DIR
from pipeline.log import get_logger, stage

log = get_logger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.json")
PROMPT_VERSION = "history-v1"
MODEL = "opus"
MIN_CONFIDENCE = 0.3


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("„", "\"").replace("“", "\"")).strip().lower()


def _stem_in_text(name: str, text: str) -> bool:
    """The most distinctive word of the name (longest non-kind word) must occur in the text (by stem)."""
    words = [w.strip(".,()„“\"") for w in name.split()]
    words = [w for w in words if w and not G.kind_of(w) and not G.INITIAL_RE.match(w + ".")
             and not re.fullmatch(r"[IVX]+|\d+(-ე)?|მე-\d+", w)]
    if not words:
        return True     # e.g. "46-ე ქუჩა": nothing distinctive to check
    w = max(words, key=len)
    stem = re.sub(r"(ისა|ის|ს|ი|ა|ე)$", "", w)
    stem = stem[: max(3, min(len(stem), 6))]
    return stem in text


def check_result(res: dict, text: str) -> tuple[dict, list[str]]:
    """Apply content checks to one validated result; returns (cleaned result, problems)."""
    problems: list[str] = []
    out = {"id": res["id"], "former_names": [], "named_after": res.get("named_after"),
           "confidence": float(res["confidence"]), "evidence": list(res.get("evidence", []))}
    ntext = _norm(text)
    for f in res.get("former_names", []):
        if not _stem_in_text(f["name_ka"], text):
            problems.append(f"name_not_in_text:{f['name_ka']}")
            continue
        f = dict(f)
        if f.get("from_year") and f.get("to_year") and f["to_year"] < f["from_year"]:
            problems.append(f"years:{f['name_ka']}")
            f.pop("to_year")
        out["former_names"].append(f)
    missing_ev = [e for e in out["evidence"] if _norm(e).strip(" .…") not in ntext]
    if missing_ev:
        problems.append(f"evidence_not_found:{len(missing_ev)}")
        out["confidence"] = round(max(0.0, out["confidence"] - 0.2), 2)
    na = out.get("named_after")
    if na and not _stem_in_text(na["name_ka"], text):
        problems.append("named_after_not_in_text")
        out["named_after"] = None
    return out, problems


def cache_key(item_id: str, text: str) -> str:
    return hashlib.sha1(f"{PROMPT_VERSION}|{MODEL}|{item_id}|{hashlib.sha1(text.encode('utf-8')).hexdigest()}"
                        .encode("utf-8")).hexdigest()


def load_batch(path: Path) -> dict[str, dict]:
    items = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                it = json.loads(line)
                items[it["id"]] = it
    return items


def merge_file(con: sqlite3.Connection, res_path: Path, batch_dir: Path, schema: dict) -> dict:
    st = {"file": res_path.name, "results": 0, "accepted": 0, "rejected": 0, "names": 0, "problems": []}
    try:
        data = json.loads(res_path.read_text(encoding="utf-8"))
    except ValueError as e:
        st["problems"].append(f"bad_json:{e}")
        return st
    try:
        jsonschema.validate(data, schema, cls=jsonschema.Draft202012Validator)
    except jsonschema.ValidationError as e:
        st["problems"].append(f"schema:{e.message[:200]}")
        log.warning("merge: %s fails schema: %s", res_path.name, e.message)
        return st
    name = res_path.stem
    if data["batch"] != name:
        st["problems"].append(f"batch_mismatch:{data['batch']}")
        return st
    bpath = batch_dir / f"{name}.jsonl"
    if not bpath.exists():
        st["problems"].append("no_batch_file")
        return st
    items = load_batch(bpath)
    rows = []
    for res in data["results"]:
        st["results"] += 1
        it = items.get(res["id"])
        if it is None:
            st["rejected"] += 1
            st["problems"].append(f"unknown_id:{res['id'][:60]}")
            continue
        clean, probs = check_result(res, it["text"])
        st["problems"].extend(probs)
        if clean["confidence"] < MIN_CONFIDENCE:
            st["rejected"] += 1
            continue
        st["accepted"] += 1
        st["names"] += len(clean["former_names"])
        rows.append((cache_key(res["id"], it["text"]), "history", MODEL, res["id"], name,
                     json.dumps(clean, ensure_ascii=False)))
    with tx(con):
        con.executemany("INSERT INTO llm_result(cache_key, role, model, family_key, batch, payload) "
                        "VALUES (?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, "
                        "batch=excluded.batch, created_at=CURRENT_TIMESTAMP", rows)
    return st


def run(con: sqlite3.Connection, results_dir: Path = RESULT_DIR, batch_dir: Path = BATCH_DIR,
        relink: bool = True) -> dict:
    with stage(log, "history.merge") as st:
        schema = load_schema()
        files = sorted(results_dir.glob("h*.json")) if results_dir.exists() else []
        st["files"] = len(files)
        tot = {"results": 0, "accepted": 0, "rejected": 0, "names": 0}
        problems = 0
        for f in files:
            s = merge_file(con, f, batch_dir, schema)
            for k in tot:
                tot[k] += s[k]
            problems += len(s["problems"])
            if s["problems"]:
                log.info("merge %s: %d problems, e.g. %s", f.name, len(s["problems"]), s["problems"][:3])
        st.update(tot)
        st["problems"] = problems
        if relink and files:
            from pipeline.history import link
            st["link"] = link.run(con)
        return st
