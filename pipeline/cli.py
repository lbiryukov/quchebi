"""Pipeline command line.

    uv run python -m pipeline.cli osm [--offline] [--stages areas,features,...] [--db PATH]
    uv run python -m pipeline.cli all [--offline]

``all`` runs the OSM import, then (when the packages exist) history, enrich, export and validate
through their ``main(argv)`` functions. A missing package or entry point is a logged warning.
"""
from __future__ import annotations

import argparse
import importlib
import sys
import time
from typing import Callable

from pipeline.log import get_logger

log = get_logger("pipeline.cli")

AFTER_OSM = ("history", "enrich", "export", "validate")


def _find_main(pkg: str) -> Callable[[list[str]], int] | None:
    """``main`` of ``pipeline.<pkg>`` or of its ``__main__`` / ``run`` / ``cli`` module."""
    for mod in (f"pipeline.{pkg}", f"pipeline.{pkg}.__main__", f"pipeline.{pkg}.run", f"pipeline.{pkg}.cli"):
        try:
            m = importlib.import_module(mod)
        except ModuleNotFoundError as exc:
            if exc.name and (exc.name == mod or mod.startswith(exc.name + ".") or exc.name == f"pipeline.{pkg}"):
                continue
            raise
        fn = getattr(m, "main", None)
        if callable(fn):
            return fn
    return None


def _run_step(name: str, fn: Callable[[list[str]], int], argv: list[str]) -> int:
    t0 = time.perf_counter()
    log.info("cli: %s start argv=%s", name, argv)
    try:
        rc = fn(argv)
    except SystemExit as exc:  # argparse inside a step
        rc = exc.code if isinstance(exc.code, int) else 1
    rc = 0 if rc is None else int(rc)
    log.info("cli: %s finished rc=%d in %.1fs", name, rc, time.perf_counter() - t0)
    return rc


def cmd_all(args: argparse.Namespace, rest: list[str]) -> int:
    from pipeline.osm.run import main as osm_main

    osm_argv = (["--offline"] if args.offline else []) + rest
    results = {"osm": _run_step("osm", osm_main, osm_argv)}
    if results["osm"] != 0:
        log.error("cli all: osm failed (rc=%d); later steps skipped", results["osm"])
        return results["osm"]
    for pkg in AFTER_OSM:
        fn = _find_main(pkg)
        if fn is None:
            log.warning("cli all: pipeline.%s has no main(argv); skipped", pkg)
            results[pkg] = None
            continue
        argv = ["--offline"] if args.offline else []
        rc = _run_step(pkg, fn, argv)
        if rc == 2 and argv:
            log.warning("cli all: %s rejected %s; retrying without arguments", pkg, argv)
            rc = _run_step(pkg, fn, [])
        results[pkg] = rc
        if rc != 0:
            log.error("cli all: %s failed (rc=%d); later steps skipped", pkg, rc)
            break
    log.info("cli all summary: %s", results)
    return 0 if all(v in (0, None) for v in results.values()) else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="python -m pipeline.cli", description="Quchebi data pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("osm", help="OSM import (fetch, areas, features, crossings, houses)", add_help=False)
    p_all = sub.add_parser("all", help="osm, then history, enrich, export, validate")
    p_all.add_argument("--offline", action="store_true")
    if not argv or argv[0] in ("-h", "--help"):
        ap.print_help()
        return 0 if argv else 2
    if argv[0] == "osm":
        from pipeline.osm.run import main as osm_main

        return osm_main(argv[1:])
    args, rest = ap.parse_known_args(argv)
    if args.cmd == "all":
        return cmd_all(args, rest)
    ap.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
