"""Tests for data_mining/schema.py -- the canonical `urls.db` DDL, its
idempotent application, and the forward-only migrations.
"""

import sqlite3
from collections.abc import Iterator

import pytest
from portfolio_common.db import Database

from data_mining.schema import SCHEMA_VERSION, apply_schema, run_migrations

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
def db() -> Iterator[Database]:
    database = Database(sqlite3.connect(":memory:"))
    yield database
    database.close()


def _table_names(db: Database) -> set[str]:
    return {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def test_apply_schema_creates_every_pipeline_table(db: Database) -> None:
    apply_schema(db)

    assert {"discovered_urls", "discovery_progress", "articles"} <= _table_names(db)


def test_apply_schema_is_idempotent(db: Database) -> None:
    apply_schema(db)
    apply_schema(db)  # must not raise on a database that already has the schema

    assert {"discovered_urls", "discovery_progress", "articles"} <= _table_names(db)


def test_apply_schema_stamps_user_version(db: Database) -> None:
    apply_schema(db)

    assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_apply_schema_creates_expected_indexes(db: Database) -> None:
    apply_schema(db)

    indexes = {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert {"idx_status", "idx_domain", "idx_ticker", "idx_progress_domain_range"} <= indexes


def test_run_migrations_on_fresh_schema_is_a_noop(db: Database) -> None:
    apply_schema(db)
    run_migrations(db)
    run_migrations(db)  # idempotent

    columns = {row[1] for row in db.execute("PRAGMA table_info(articles)").fetchall()}
    assert "gics_sector" in columns
    assert "gics_sub_industry" in columns
    assert "sector" not in columns


def test_run_migrations_upgrades_a_legacy_sector_column(db: Database) -> None:
    db.execute(LEGACY_ARTICLES_SCHEMA)
    db.execute(
        "INSERT INTO articles (id, ticker, company, sector, title, fetch_status) "
        "VALUES (1, 'MMM', '3M', 'Industrials', 'headline', 'ok')"
    )
    db.commit()

    run_migrations(db)

    columns = {row[1] for row in db.execute("PRAGMA table_info(articles)").fetchall()}
    assert "gics_sector" in columns
    assert "gics_sub_industry" in columns
    assert "sector" not in columns

    row = db.execute(
        "SELECT ticker, company, title, fetch_status FROM articles WHERE id = 1"
    ).fetchone()
    assert row == ("MMM", "3M", "headline", "ok")


def test_run_migrations_without_articles_table_is_a_noop(db: Database) -> None:
    run_migrations(db)  # no articles table at all -- must not raise

    assert "articles" not in _table_names(db)
