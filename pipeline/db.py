"""SQLite access and schema migrations for the pipeline database."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pipeline.config import DB_PATH, MIGRATIONS
from pipeline.log import get_logger

log = get_logger(__name__)


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open the database with foreign keys on and rows accessible by column name."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        con.execute("PRAGMA journal_mode = WAL")
    return con


def migrate(con: sqlite3.Connection, migrations_dir: Path = MIGRATIONS) -> list[str]:
    """Apply not-yet-applied ``*.sql`` files in name order. Returns applied names."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    done = {r[0] for r in con.execute("SELECT name FROM schema_migrations")}
    applied = []
    for f in sorted(migrations_dir.glob("*.sql")):
        if f.name in done:
            continue
        log.info("applying migration %s", f.name)
        con.executescript(f.read_text(encoding="utf-8"))
        con.execute("INSERT INTO schema_migrations(name) VALUES (?)", (f.name,))
        con.commit()
        applied.append(f.name)
    return applied


def open_db(path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Connect and bring the schema up to date."""
    con = connect(path)
    migrate(con)
    return con


@contextmanager
def tx(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Transaction scope: commit on success, rollback on error."""
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise


def set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def get_meta(con: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default
