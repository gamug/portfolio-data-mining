"""Tests for data_mining/schema.py -- the canonical `urls.db` DDL, its
idempotent application, and the forward-only migrations.
"""

import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from portfolio_common.db import Database

from data_mining.db import connect as connect_db
from data_mining.schema import (
    _ARTICLES_DDL,
    _DISCOVERY_DDL,
    ARTICLE_COLUMNS,
    PREVIOUS_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SITEMAP_CACHE_DDL,
    apply_schema,
    rollback_sitemap_cache,
    run_migrations,
)
from extractor.db import connect, ensure_articles_table
from news_collector.storage.queue import URLQueue

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


def test_schema_version_is_4() -> None:
    assert SCHEMA_VERSION == PINNED_VERSION


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


def test_run_migrations_adds_prev_fetch_status_to_a_version_2_articles(db: Database) -> None:
    db.execute(
        LEGACY_ARTICLES_SCHEMA.replace(
            "sector             TEXT,", "gics_sector TEXT, gics_sub_industry TEXT,"
        )
    )
    db.execute("INSERT INTO articles (id, fetch_status) VALUES (1, 'ok')")
    db.commit()

    run_migrations(db)
    run_migrations(db)  # idempotent

    assert "prev_fetch_status" in db.table_columns("articles")
    assert tuple(db.execute("SELECT fetch_status, prev_fetch_status FROM articles").fetchone()) == (
        "ok",
        None,
    )


def test_fresh_schema_has_prev_fetch_status_outside_the_upsert_columns(db: Database) -> None:
    apply_schema(db)
    assert "prev_fetch_status" in db.table_columns("articles")
    assert "prev_fetch_status" not in ARTICLE_COLUMNS  # a re-fetch must never reset it


# ---------------------------------------------------------------------------------------------
# The sitemap cache (Work item 15, T-069)
# ---------------------------------------------------------------------------------------------

MIN_WAIT = 0.2  # seconds the second connection must have waited
PINNED_VERSION = 4  # the history is in schema.py
CACHE_TABLES = {"sitemap_entries", "sitemap_crawls"}
CACHE_INDEXES = {
    "ix_sitemap_entries_lastmod",
    "ix_sitemap_crawls_latest",
    "ix_sitemap_crawls_open",
}
PIPELINE_TABLES = {"discovered_urls", "discovery_progress", "articles"}
DOMAIN = "cnbc.com"


def _fingerprint(db: Database) -> tuple[int, int, list[tuple]]:
    """Rows of the tables the cache must not touch, and the definition of each."""
    counts = [
        db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - fixed names
        for t in ("discovered_urls", "articles")
    ]
    ddl = db.execute(
        "SELECT name, sql FROM sqlite_master WHERE name IN "
        "('discovered_urls', 'discovery_progress', 'articles') ORDER BY name"
    ).fetchall()
    return counts[0], counts[1], [tuple(r) for r in ddl]


def _seed_pipeline(db: Database) -> None:
    db.execute(
        "INSERT INTO discovered_urls (url, domain, company, ticker, source, discovered_at) "
        "VALUES ('https://www.cnbc.com/2024/05/01/a.html', 'cnbc.com', 'KLA', 'KLAC', "
        "'sitemap', '2026-10-19T00:00:00.000000')"
    )
    db.execute("INSERT INTO articles (id, ticker, fetch_status) VALUES (1, 'KLAC', 'ok')")
    db.commit()


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def test_apply_schema_creates_the_cache_tables_and_indexes(db: Database) -> None:
    apply_schema(db)

    assert _table_names(db) >= CACHE_TABLES
    assert _index_names(db) >= CACHE_INDEXES


def test_the_migration_adds_the_cache_to_a_version_3_database_and_changes_nothing_else(
    db: Database,
) -> None:
    apply_schema(db)
    _seed_pipeline(db)
    rollback_sitemap_cache(db)  # a database exactly as version 3 left it
    assert not CACHE_TABLES & _table_names(db)
    before = _fingerprint(db)

    run_migrations(db)
    run_migrations(db)  # idempotent

    assert _table_names(db) >= CACHE_TABLES
    assert _index_names(db) >= CACHE_INDEXES
    assert _fingerprint(db) == before  # same rows, same definitions
    assert db.execute("SELECT COUNT(*) FROM sitemap_entries").fetchone()[0] == 0


def test_the_migration_on_an_empty_database_is_a_noop_like_the_other_steps(db: Database) -> None:
    run_migrations(db)
    run_migrations(db)

    assert _table_names(db) == set()  # apply_schema creates the whole schema there


def test_the_rollback_restores_the_previous_schema_and_can_be_redone(db: Database) -> None:
    apply_schema(db)
    _seed_pipeline(db)
    before = _fingerprint(db)
    db.execute(
        "INSERT INTO sitemap_entries VALUES ('cnbc.com', 'https://x/2024/a.html', "
        "'2024-05-01', NULL, '2026-10-19T00:00:00.000000', '2026-10-19T00:00:00.000000')"
    )
    db.commit()

    rollback_sitemap_cache(db)

    assert _table_names(db) - {"sqlite_sequence"} == PIPELINE_TABLES  # the cache is gone
    assert not CACHE_INDEXES & _index_names(db)
    assert db.execute("PRAGMA user_version").fetchone()[0] == PREVIOUS_SCHEMA_VERSION
    assert _fingerprint(db) == before
    apply_schema(db)  # and the schema comes back
    assert _table_names(db) >= CACHE_TABLES


def test_the_design_queries_use_an_index_on_the_migrated_schema(db: Database) -> None:
    apply_schema(db)
    run_migrations(db)
    db.execute(
        "INSERT INTO sitemap_crawls VALUES ('cnbc.com', '2026-10-19T10:00:00.000000', NULL, 0, "
        "'2022-01-01', 'sig', NULL, NULL)"
    )
    queries = {
        "latest complete crawl": (
            "SELECT started_at, finished_at, range_start, signature FROM sitemap_crawls "
            "WHERE domain = ? AND complete = 1 ORDER BY finished_at DESC LIMIT 1",
            (DOMAIN,),
        ),
        "crawl in progress": (
            "SELECT 1 FROM sitemap_crawls WHERE domain = ? AND finished_at IS NULL "
            "AND started_at > ?",
            (DOMAIN, "2026-10-19T09:30:00.000000"),
        ),
        "current entries": (
            "SELECT url, lastmod, title FROM sitemap_entries WHERE domain = ? AND last_seen = ?",
            (DOMAIN, "2026-10-19T10:00:00.000000"),
        ),
    }
    for name, (sql, params) in queries.items():
        plan = " ".join(r[3] for r in db.execute("EXPLAIN QUERY PLAN " + sql, params))
        assert "SEARCH" in plan, (name, plan)
        assert "SCAN" not in plan, (name, plan)
    # the in-progress row is found inside the timeout and not after it
    sql, params = queries["crawl in progress"]
    assert db.execute(sql, params).fetchall() == [(1,)]
    assert db.execute(sql, (DOMAIN, "2026-10-19T10:30:00.000000")).fetchall() == []


def test_the_cache_keys_refuse_a_duplicate(db: Database) -> None:
    apply_schema(db)
    stamp = "2026-10-19T00:00:00.000000"
    row = ("cnbc.com", "https://x/2024/a.html", None, None, stamp, stamp)
    db.execute("INSERT INTO sitemap_entries VALUES (?, ?, ?, ?, ?, ?)", row)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO sitemap_entries VALUES (?, ?, ?, ?, ?, ?)", row)
    crawl = ("cnbc.com", "2026-10-19T10:00:00.000000", None, 0, "2022-01-01", "sig", None, None)
    db.execute("INSERT INTO sitemap_crawls VALUES (?, ?, ?, ?, ?, ?, ?, ?)", crawl)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO sitemap_crawls VALUES (?, ?, ?, ?, ?, ?, ?, ?)", crawl)


def test_both_stages_and_a_read_only_reader_work_on_a_version_3_file(tmp_path: Path) -> None:
    """The collector's queue and the extractor open the file as they do in production, and a
    reader (portfolio-nlp) opens it read-only; the cache tables do not change what they see."""
    path = tmp_path / "urls.db"
    old = Database(sqlite3.connect(path))
    apply_schema(old)
    _seed_pipeline(old)
    rollback_sitemap_cache(old)  # the file as the previous version left it
    old.close()

    def read(uri_path: Path) -> list[tuple]:
        reader = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
        try:
            return reader.execute(
                "SELECT d.url, d.ticker, a.fetch_status FROM discovered_urls d "
                "JOIN articles a ON a.id = d.id ORDER BY d.id"
            ).fetchall()
        finally:
            reader.close()

    before = read(path)
    assert before  # the reader's join is not vacuous

    queue = URLQueue(path)
    queue.initialize()  # the collector's writer path: apply_schema + run_migrations
    queue.close()
    stage = connect(str(path))
    ensure_articles_table(stage)  # the extractor's path, foreign keys on
    stage.close()

    assert read(path) == before
    check = sqlite3.connect(path)
    assert {r[0] for r in check.execute("SELECT name FROM sqlite_master")} >= CACHE_TABLES
    assert check.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    check.close()


def test_complete_refuses_a_value_other_than_0_or_1(db: Database) -> None:
    apply_schema(db)
    bad = ("cnbc.com", "2026-10-19T10:00:00.000000", None, 7, "2022-01-01", "sig", None, None)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO sitemap_crawls VALUES (?, ?, ?, ?, ?, ?, ?, ?)", bad)
    for flag in (0, 1):
        ok = ("cnbc.com", f"2026-10-19T10:00:00.00000{flag}", None, flag, "2022-01-01", "s", 0, 0)
        db.execute("INSERT INTO sitemap_crawls VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ok)


def test_the_rollback_refuses_a_database_that_is_not_at_version_4(db: Database) -> None:
    apply_schema(db)
    rollback_sitemap_cache(db)  # version 3 now
    with pytest.raises(ValueError, match="expects schema version 4"):
        rollback_sitemap_cache(db)  # twice
    db.set_schema_version(SCHEMA_VERSION + 1)
    with pytest.raises(ValueError, match="found 5"):
        rollback_sitemap_cache(db)  # a later version is never lowered
    assert db.schema_version == SCHEMA_VERSION + 1


def test_timestamps_that_differ_by_microseconds_are_two_keys_in_time_order(db: Database) -> None:
    apply_schema(db)
    first = "2026-10-19T10:00:00.000001"
    second = "2026-10-19T10:00:00.000002"
    for started in (second, first):  # inserted out of order
        db.execute(
            "INSERT INTO sitemap_crawls VALUES ('cnbc.com', ?, NULL, 0, '2022-01-01', 's', 0, 0)",
            (started,),
        )
    rows = db.execute(
        "SELECT started_at FROM sitemap_crawls WHERE domain = 'cnbc.com' ORDER BY started_at"
    ).fetchall()
    assert [r[0] for r in rows] == [first, second]


def test_two_connections_starting_together_migrate_one_file_once(tmp_path: Path) -> None:
    """Two connections (threads of one process: the lock is the file's, so two processes behave
    the same). The collector and the extractor start independently and both run the migration: the
    second waits for the first's write lock (busy_timeout) and finds nothing to do."""
    path = tmp_path / "urls.db"
    old = Database(sqlite3.connect(path))
    apply_schema(old)
    rollback_sitemap_cache(old)  # a version-3 file
    old.close()

    first = connect_db(str(path), wal=True, check_same_thread=False)
    second = connect_db(str(path), wal=True, check_same_thread=False)
    holding = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def hold_the_lock() -> None:
        try:
            first.execute("BEGIN IMMEDIATE")  # the migration's write lock, held a moment
            for statement in SITEMAP_CACHE_DDL.split(";"):  # the DDL, inside the transaction
                if statement.strip():
                    first.execute(statement)
            holding.set()
            release.wait(5)
            first.commit()
        except BaseException as exc:
            errors.append(exc)
            holding.set()

    holder = threading.Thread(target=hold_the_lock)
    holder.start()
    assert holding.wait(5)
    threading.Timer(0.3, release.set).start()
    began = time.monotonic()
    apply_schema(second)  # blocks on the lock, then runs as a no-op
    run_migrations(second)
    second.commit()
    waited = time.monotonic() - began
    holder.join(5)

    assert errors == []
    assert waited >= MIN_WAIT  # it really waited for the first
    check = sqlite3.connect(path)
    objects = [
        r[0]
        for r in check.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%sitemap%' AND name NOT LIKE 'sqlite_autoindex%' ORDER BY name"
        )
    ]
    check.close()
    assert objects == sorted([*CACHE_TABLES, *CACHE_INDEXES])  # one copy of each
    first.close()
    second.close()


def _apply_schema_as_version_3(db: Database) -> None:
    """The previous `apply_schema`: the same two DDL scripts (unchanged in version 4) and the
    version stamp 3, with no knowledge of the cache."""
    db.create_schema(_DISCOVERY_DDL.replace("{autoincrement_pk}", db.dialect.autoincrement_pk))
    db.create_schema(_ARTICLES_DDL)
    db.set_schema_version(PREVIOUS_SCHEMA_VERSION)


def test_a_version_3_build_leaves_the_cache_and_the_next_start_restamps_4(db: Database) -> None:
    """Going back to the old code needs no data step: it ignores the cache tables and stamps 3."""
    apply_schema(db)
    run_migrations(db)
    stamp = "2026-10-19T00:00:00.000000"
    db.execute(
        "INSERT INTO sitemap_entries VALUES ('cnbc.com', 'https://x/2024/a.html', "
        "'2024-05-01', NULL, ?, ?)",
        (stamp, stamp),
    )
    _seed_pipeline(db)
    db.commit()
    before = _fingerprint(db)

    _apply_schema_as_version_3(db)  # the old build starts on the version-4 file

    assert db.schema_version == PREVIOUS_SCHEMA_VERSION
    assert _table_names(db) >= CACHE_TABLES  # ignored, not dropped
    assert db.execute("SELECT COUNT(*) FROM sitemap_entries").fetchone()[0] == 1
    assert _fingerprint(db) == before
    apply_schema(db)  # the new build starts again
    run_migrations(db)
    assert db.schema_version == SCHEMA_VERSION
    assert db.execute("SELECT COUNT(*) FROM sitemap_entries").fetchone()[0] == 1
