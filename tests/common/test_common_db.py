"""Tests for src/common/db.py -- the one connection factory shared by
news_collector and extractor.
"""

import sqlite3
from pathlib import Path

import pytest

from common.db import BUSY_TIMEOUT_MS, DEFAULT_DB_PATH, connect, resolve_db_path


def test_resolve_db_path_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert resolve_db_path() == DEFAULT_DB_PATH


def test_resolve_db_path_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "/somewhere/custom.db")

    assert resolve_db_path() == "/somewhere/custom.db"


def test_connect_always_sets_row_factory_and_busy_timeout(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "t.db"))
    try:
        assert conn.row_factory is sqlite3.Row
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
    finally:
        conn.close()


def test_connect_defaults_leave_wal_and_fk_off(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "t.db"))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    finally:
        conn.close()


def test_connect_wal_true_enables_wal_and_normal_sync(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "t.db"), wal=True)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        # synchronous NORMAL == 1
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    finally:
        conn.close()


def test_connect_foreign_keys_true_enforces_fk(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "t.db"), foreign_keys=True)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_connect_check_same_thread_false_is_accepted(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "t.db"), wal=True, check_same_thread=False)
    try:
        conn.execute("SELECT 1")
    finally:
        conn.close()
