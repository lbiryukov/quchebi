"""Schema smoke test: migrations apply cleanly to an empty database."""
from pipeline.db import connect, migrate


def test_migrations_apply_in_memory():
    con = connect(":memory:")
    applied = migrate(con)
    assert "001_init.sql" in applied
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("feature", "family", "eponym", "crossing", "house", "area", "name_history", "llm_result"):
        assert t in tables
    assert migrate(con) == []  # idempotent
