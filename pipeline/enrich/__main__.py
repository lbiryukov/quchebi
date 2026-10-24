"""CLI of the eponym enrichment: ``uv run python -m pipeline.enrich <stage> [options]``.

Stages (each idempotent; caches keyed by family.key / QID / text):

  qids       QIDs from OSM name:etymology:wikidata + Wikidata street items (P138)
  rules      rule classifier -> family.kind_hint
  match      persons without QID -> Wikidata humans by exact label
  facts      Wikidata facts -> eponym, eponym_category, family.eponym_id
  oldnames   eponyms of former names (name_history.eponym_id)
  dossiers   evidence packets for the LLM queue (+ verification sample, gold set, name QA)
  batches    JSONL batch files for the subagents (--role to restrict)
  merge      validate subagent answers, thresholds, write eponyms; apply review decisions
  review     data/build/review.html
  calibrate  thresholds on the gold set (--apply writes data/enrich/thresholds.json)
  schemas    rewrite pipeline/enrich/schemas/*.json from the taxonomy
  prepare    qids rules match facts oldnames dossiers batches
  finish     merge review
  stats      counts from the database

Workflow with the Haiku subagents (.claude/agents/*.md):
  1. prepare                      -> data/enrich/batches/{classifier,verifier,name_qa}/*.jsonl
  2. run eponym-classifier on every classifier batch (c*, k*, o*), eponym-verifier on v*,
     name-qa on q*; prompt = the batch path; each writes data/enrich/results/<role>/<batch>.json
  3. merge                        -> routes; then `batches --role disambiguator --role verifier`
  4. run eponym-disambiguator on d* and eponym-verifier on the new v*; then merge again
  5. calibrate [--apply]          -> data/build/calibration.json (thresholds on the gold k* set)
  6. review                       -> data/build/review.html; decisions -> data/enrich/review_decisions.json;
     merge applies them (also on every later rebuild)
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from pipeline.config import DB_PATH, ensure_dirs
from pipeline.db import open_db
from pipeline.enrich import batches as B
from pipeline.enrich import calibrate as C
from pipeline.enrich import dossiers as D
from pipeline.enrich import facts as FA
from pipeline.enrich import gazetteer as G
from pipeline.enrich import match as M
from pipeline.enrich import merge as MG
from pipeline.enrich import oldnames as O
from pipeline.enrich import qids as Q
from pipeline.enrich import review as RV
from pipeline.enrich import rules as R
from pipeline.enrich import schemas as S
from pipeline.enrich.common import load_families, paths, read_json, stage_path
from pipeline.log import get_logger
from pipeline.wikidata.client import WikidataClient
from pipeline.wikidata.http import Fetcher, HttpCache

log = get_logger("pipeline.enrich")

STAGES = ["qids", "rules", "match", "facts", "oldnames", "dossiers", "batches", "merge", "review", "calibrate",
          "schemas", "prepare", "finish", "stats"]
PREPARE = ["qids", "rules", "match", "facts", "oldnames", "dossiers", "batches"]
FINISH = ["merge", "review"]


def _client(args) -> WikidataClient:
    gaps = {}
    if args.sparql_gap is not None:
        gaps["sparql"] = args.sparql_gap
    c = WikidataClient(Fetcher(HttpCache(), offline=args.offline, gaps=gaps))
    if not args.no_seed:
        c.seed_from_prototype()
    return c


def _need(name: str) -> dict:
    data = read_json(stage_path(name))
    if data is None:
        raise SystemExit(f"stage '{name}' has not been run yet (missing {stage_path(name)})")
    return data


def stats(con) -> dict:
    out = {}
    out["families"] = con.execute("SELECT COUNT(*) FROM family").fetchone()[0]
    out["families_with_eponym"] = con.execute("SELECT COUNT(*) FROM family WHERE eponym_id IS NOT NULL").fetchone()[0]
    out["eponyms_by_source"] = dict(con.execute("SELECT source, COUNT(*) FROM eponym GROUP BY source").fetchall())
    out["eponyms_by_kind"] = dict(con.execute("SELECT kind, COUNT(*) FROM eponym GROUP BY kind").fetchall())
    out["eponyms_needs_review"] = con.execute("SELECT COUNT(*) FROM eponym WHERE needs_review=1").fetchone()[0]
    out["family_eponym_source"] = dict(con.execute(
        "SELECT e.source, COUNT(*) FROM family f JOIN eponym e ON e.id=f.eponym_id GROUP BY e.source").fetchall())
    out["kind_hint"] = dict(collections.Counter(
        (h or "").split(":")[0] for (h,) in con.execute("SELECT kind_hint FROM family")))
    out["categories_top"] = dict(con.execute(
        "SELECT category, COUNT(*) n FROM eponym_category GROUP BY category ORDER BY n DESC LIMIT 25").fetchall())
    out["name_history_with_eponym"] = con.execute(
        "SELECT COUNT(*) FROM name_history WHERE eponym_id IS NOT NULL").fetchone()[0]
    out["llm_results"] = dict(con.execute("SELECT role, COUNT(*) FROM llm_result GROUP BY role").fetchall())
    return out


def run_stage(name: str, args, con, client_box: list) -> None:
    def client() -> WikidataClient:
        if not client_box:
            client_box.append(_client(args))
        return client_box[0]

    if name == "schemas":
        for p in S.write_all():
            print("wrote", p)
        return
    fams = load_families(con)
    dossier_paths = [paths.dossiers, paths.dossiers_old]
    if name == "qids":
        Q.run(con, fams, client(), refresh=args.refresh)
    elif name == "rules":
        gaz = G.build(con, client())
        R.run(con, fams, gaz)
    elif name == "match":
        M.run(con, fams, _need("rules"), _need("qids"), client(), control=not args.no_control)
    elif name == "facts":
        FA.run(con, fams, _need("rules"), _need("qids"), _need("match"), client())
    elif name == "oldnames":
        gaz = G.build(con, client())
        O.run(con, fams, gaz, client(), search=not args.no_search)
    elif name == "dossiers":
        D.run(con, fams, _need("rules"), _need("qids"), _need("match"), _need("facts"), client(),
              search=not args.no_search, extracts=not args.no_extracts, gold=not args.no_gold)
    elif name == "batches":
        rows = B.load_dossiers(dossier_paths)
        roles = tuple(args.role) if args.role else ("classifier", "verifier", "disambiguator", "name_qa")
        man = B.run(con, rows, client(), roles=roles, size=args.batch_size)
        for role, m in man.items():
            print(f"{role}: {len(m['batches'])} batches, {sum(b['n'] for b in m['batches'].values())} items")
    elif name == "merge":
        rows = B.load_dossiers(dossier_paths)
        st = MG.run(con, fams, rows, FA.EponymBuilder(client()))
        print(json.dumps(st.get("summary", {}), ensure_ascii=False))
    elif name == "review":
        r = RV.run(con, dossier_paths)
        print(f"review page: {r['path']} ({r['items']} items)")
    elif name == "calibrate":
        rep = C.run(con, B.load_dossiers(dossier_paths), target=args.target, apply=args.apply)
        print(json.dumps({k: rep[k] for k in ("gold_dossiers", "gold_answered", "recommended", "matcher_control")},
                         ensure_ascii=False, indent=1))
    elif name == "stats":
        print(json.dumps(stats(con), ensure_ascii=False, indent=1))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.enrich", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=STAGES)
    ap.add_argument("--db", default=str(DB_PATH), help="SQLite database (default data/runcity.sqlite)")
    ap.add_argument("--offline", action="store_true", help="never touch the network (cache only)")
    ap.add_argument("--refresh", action="store_true", help="re-download the Wikidata street items")
    ap.add_argument("--no-seed", action="store_true", help="do not import the prototype entity cache")
    ap.add_argument("--no-search", action="store_true", help="dossiers: skip Wikidata full-text search")
    ap.add_argument("--no-extracts", action="store_true", help="dossiers: skip Wikipedia intros")
    ap.add_argument("--no-gold", action="store_true", help="dossiers: skip the calibration gold set")
    ap.add_argument("--no-control", action="store_true", help="match: skip the control set")
    ap.add_argument("--role", action="append", choices=["classifier", "verifier", "disambiguator", "name_qa"])
    ap.add_argument("--batch-size", type=int, default=B.BATCH_SIZE)
    ap.add_argument("--sparql-gap", type=float, default=None, help="seconds between SPARQL requests (default 5)")
    ap.add_argument("--target", type=float, default=0.97, help="calibrate: precision target")
    ap.add_argument("--apply", action="store_true", help="calibrate: write data/enrich/thresholds.json")
    args = ap.parse_args(argv)
    ensure_dirs()
    if args.stage == "schemas":
        run_stage("schemas", args, None, [])
        return 0
    db = Path(args.db)
    if not db.exists():
        log.error("database %s does not exist (run the OSM stages first)", db)
        return 2
    con = open_db(db)
    try:
        n = con.execute("SELECT COUNT(*) FROM family").fetchone()[0]
        if n == 0 and args.stage not in ("stats",):
            log.error("no families in %s (run the OSM stages first)", db)
            return 2
        seq = PREPARE if args.stage == "prepare" else FINISH if args.stage == "finish" else [args.stage]
        box: list = []
        for s in seq:
            log.info("enrich: stage %s", s)
            run_stage(s, args, con, box)
        if box:
            log.info("wikidata http: %s cache: %s", box[0].fetcher.stats, box[0].cache.count())
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
