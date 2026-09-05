"""Tests for data_mining/db.py -- this domain's own defaults
(resolve_db_path()/DEFAULT_DB_PATH/BUSY_TIMEOUT_MS) and a thin smoke test
that its connect() wrapper delegates correctly to
portfolio_common.db.Database.connect(). The full connect()/pragma contract
(row_factory, busy_timeout, wal, foreign_keys, check_same_thread) is already
covered by portfolio-common's own tests/test_engine.py -- not duplicated
here.
"""

from pathlib import Path

import pytest
from portfolio_common.db import Database

from data_mining.db import BUSY_TIMEOUT_MS, DEFAULT_DB_PATH, connect, resolve_db_path


def test_resolve_db_path_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert resolve_db_path() == DEFAULT_DB_PATH


def test_resolve_db_path_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "/somewhere/custom.db")

    assert resolve_db_path() == "/somewhere/custom.db"


def test_connect_returns_database_with_expected_pragmas(tmp_path: Path) -> None:
    db = connect(str(tmp_path / "t.db"), wal=True, foreign_keys=True)
    try:
        assert isinstance(db, Database)
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        db.close()
