"""Logging setup shared by all pipeline stages.

Every stage calls ``get_logger(__name__)``; the first call configures an append-mode
file handler in ``logs/pipeline.log`` (archived when over 20 MB) plus a console handler. A per-process
``RUN_ID`` is attached to every record so one pipeline run can be grepped out.
"""
from __future__ import annotations

import logging
import sys
import time
import uuid
from contextlib import contextmanager
from typing import Iterator

from pipeline.config import LOGS

RUN_ID = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
_configured = False


class _RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = RUN_ID
        return True


def setup_logging(level: int = logging.INFO, logfile: str = "pipeline.log") -> None:
    """Configure root logging once: rotating file (DEBUG) + console (``level``)."""
    global _configured
    if _configured:
        return
    LOGS.mkdir(parents=True, exist_ok=True)
    _archive_if_large(LOGS / logfile)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(run_id)s] %(name)s: %(message)s", "%d/%m/%Y %H:%M:%S"
    )
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # Plain append handler: several pipeline processes may log at once, and size-based
    # rotation of a shared file is unreliable on Windows. Large files are archived at startup.
    fh = logging.FileHandler(LOGS / logfile, mode="a", encoding="utf-8", delay=True)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    fh.addFilter(_RunIdFilter())
    stream = sys.stderr
    try:
        stream.reconfigure(encoding="utf-8")  # Windows console defaults to cp1251
    except (AttributeError, ValueError):
        pass
    ch = logging.StreamHandler(stream)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    ch.addFilter(_RunIdFilter())
    root.addHandler(fh)
    root.addHandler(ch)
    for noisy in ("urllib3", "pdfminer", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def _archive_if_large(path, limit: int = 20_000_000) -> None:
    """Move a log file over ``limit`` bytes aside as ``<name>-<timestamp>.log`` (best effort)."""
    try:
        if path.exists() and path.stat().st_size > limit:
            path.rename(path.with_name(f"{path.stem}-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}"))
    except OSError:
        pass  # another process holds or already moved it


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


@contextmanager
def stage(log: logging.Logger, name: str, **counts_in: object) -> Iterator[dict]:
    """Log start/finish/duration of a pipeline stage.

    Usage::

        with stage(log, "build_features", ways=len(ways)) as out:
            ...
            out["features"] = len(features)
    """
    log.info("stage %s: start %s", name, counts_in or "")
    t0 = time.perf_counter()
    out: dict = {}
    try:
        yield out
    except Exception:
        log.exception("stage %s: FAILED after %.2fs", name, time.perf_counter() - t0)
        raise
    log.info("stage %s: done in %.2fs %s", name, time.perf_counter() - t0, out or "")
