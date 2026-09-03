"""Tests for src/common/schema.py -- the canonical pipeline DDL, its
idempotent application, and the forward-only migrations.
"""

import sqlite3
from collections.abc import Iterator

import pytest

from common.schema import SCHEMA_VERSION, apply_schema, run_migrations

LEGACY_ARTICLES_SCHEMA = """
CREATE TABLE articles (
    id                 INTEGER PRIMARY KEY,
    ticker             TEXT,
    company            TEXT,
    sector             TEXT,
    title              TEXT,
    author             TEXT,
    pub_date           TEXT,
    body_text          TEXT,
    word_count         INTEGER,
    language           TEXT,
    source_domain      TEXT,
    extraction_method  TEXT,
    fetch_status       TEXT,
    http_status_code   INTEGER,
    fetched_at         TEXT,
    FOREIGN KEY (id) REFERENCES discovered_urls (id)
)
"""


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def test_apply_schema_creates_every_pipeline_table(conn: sqlite3.Connection) -> None:
    apply_schema(conn)

    assert {"discovered_urls", "discovery_progress", "articles"} <= _table_names(conn)


def test_apply_schema_is_idempotent(conn: sqlite3.Connection) -> None:
    apply_schema(conn)
    apply_schema(conn)  # must not raise on a database that already has the schema

    assert {"discovered_urls", "discovery_progress", "articles"} <= _table_names(conn)


def test_apply_schema_stamps_user_version(conn: sqlite3.Connection) -> None:
    apply_schema(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_apply_schema_creates_expected_indexes(conn: sqlite3.Connection) -> None:
    apply_schema(conn)

    indexes = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert {"idx_status", "idx_domain", "idx_ticker", "idx_progress_domain_range"} <= indexes


def test_run_migrations_on_fresh_schema_is_a_noop(conn: sqlite3.Connection) -> None:
    apply_schema(conn)
    run_migrations(conn)
    run_migrations(conn)  # idempotent

    columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)").fetchall()}
    assert "gics_sector" in columns
    assert "gics_sub_industry" in columns
    assert "sector" not in columns


def test_run_migrations_upgrades_a_legacy_sector_column(conn: sqlite3.Connection) -> None:
    conn.execute(LEGACY_ARTICLES_SCHEMA)
    conn.execute(
        "INSERT INTO articles (id, ticker, company, sector, title, fetch_status) "
        "VALUES (1, 'MMM', '3M', 'Industrials', 'headline', 'ok')"
    )
    conn.commit()

    run_migrations(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)").fetchall()}
    assert "gics_sector" in columns
    assert "gics_sub_industry" in columns
    assert "sector" not in columns

    row = conn.execute(
        "SELECT ticker, company, title, fetch_status FROM articles WHERE id = 1"
    ).fetchone()
    assert row == ("MMM", "3M", "headline", "ok")


def test_run_migrations_without_articles_table_is_a_noop(conn: sqlite3.Connection) -> None:
    run_migrations(conn)  # no articles table at all -- must not raise

    assert "articles" not in _table_names(conn)
