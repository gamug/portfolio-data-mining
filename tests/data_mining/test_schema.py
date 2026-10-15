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


LEGACY_DISCOVERED_V1 = """
CREATE TABLE discovered_urls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT    NOT NULL,
    domain          TEXT    NOT NULL,
    company         TEXT    NOT NULL,
    ticker          TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    discovered_at   TEXT    NOT NULL,
    pub_date        TEXT,
    title           TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending',
    fetch_status_code INTEGER,
    UNIQUE (url, ticker)
)
"""


def _discovered_columns(db: Database) -> set[str]:
    return {row[1] for row in db.execute("PRAGMA table_info(discovered_urls)").fetchall()}


def _index_names(db: Database) -> set[str]:
    return {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }


def test_schema_version_is_2() -> None:
    assert SCHEMA_VERSION == 2


def test_fresh_schema_has_match_columns_and_index(db: Database) -> None:
    apply_schema(db)
    run_migrations(db)

    assert {"match_class", "match_reason", "query"} <= _discovered_columns(db)
    assert "idx_match_class" in _index_names(db)


def test_run_migrations_upgrades_a_version_1_discovered_urls(db: Database) -> None:
    db.execute(LEGACY_DISCOVERED_V1)
    db.execute(
        "INSERT INTO discovered_urls (url, domain, company, ticker, source, discovered_at)"
        " VALUES ('https://cnbc.com/a', 'cnbc.com', '3M', 'MMM', 'sitemap', '2026-08-10T00:00:00')"
    )
    db.commit()

    apply_schema(db)  # what every opener runs first: must not fail on the old table
    run_migrations(db)
    run_migrations(db)  # idempotent

    assert {"match_class", "match_reason", "query"} <= _discovered_columns(db)
    assert "idx_match_class" in _index_names(db)
    row = db.execute(
        "SELECT match_class, match_reason, query, status FROM discovered_urls"
    ).fetchone()
    assert tuple(row) == ("accept", None, None, "pending")  # existing rows default to accept


def test_run_migrations_leaves_a_current_database_untouched(db: Database) -> None:
    apply_schema(db)
    run_migrations(db)
    db.execute(
        "INSERT INTO discovered_urls (url, domain, company, ticker, source, discovered_at,"
        " match_class, match_reason, query) VALUES ('https://cnbc.com/a', 'cnbc.com', '3M', 'MMM',"
        " 'ddg', '2026-08-10T00:00:00', 'review', 'weak_ticker', 'q')"
    )
    db.commit()
    before = db.execute("SELECT * FROM discovered_urls").fetchall()

    run_migrations(db)

    assert db.execute("SELECT * FROM discovered_urls").fetchall() == before


_BAD_INSERT = (
    "INSERT INTO discovered_urls (url, domain, company, ticker, source, discovered_at, match_class)"
    " VALUES ('https://cnbc.com/a', 'cnbc.com', '3M', 'MMM', 'ddg', '2026-08-10T00:00:00', 'bogus')"
)


def test_match_class_check_constraint_on_a_fresh_schema(db: Database) -> None:
    apply_schema(db)
    run_migrations(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(_BAD_INSERT)


def test_match_class_check_constraint_on_a_migrated_schema(db: Database) -> None:
    db.execute(LEGACY_DISCOVERED_V1)
    db.commit()
    apply_schema(db)
    run_migrations(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(_BAD_INSERT)
