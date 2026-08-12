import sqlite3

import pytest

from extractor.db import (
    enable_foreign_keys,
    ensure_articles_table,
    get_pending_urls,
    get_status_counts,
    mark_status,
    save_article,
)

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

DISCOVERED_URLS_SCHEMA = """
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


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.execute(DISCOVERED_URLS_SCHEMA)
    connection.execute(
        """
        INSERT INTO discovered_urls (url, domain, company, ticker, source, discovered_at)
        VALUES ('https://cnbc.com/a', 'cnbc.com', '3M', 'MMM', 'sitemap', '2026-08-10T00:00:00'),
               ('https://cnbc.com/b', 'cnbc.com', '3M', 'MMM', 'sitemap', '2026-08-10T00:00:00')
        """
    )
    connection.commit()
    yield connection
    connection.close()


def test_ensure_articles_table_creates_table_once_and_is_idempotent(conn):
    ensure_articles_table(conn)
    ensure_articles_table(conn)  # must not raise on second call

    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='articles'"
    ).fetchall()
    assert len(tables) == 1


def test_get_pending_urls_returns_only_pending_rows(conn):
    conn.execute("UPDATE discovered_urls SET status = 'fetched' WHERE id = 2")
    conn.commit()

    pending = get_pending_urls(conn)

    assert [row["id"] for row in pending] == [1]
    assert pending[0]["url"] == "https://cnbc.com/a"
    assert pending[0]["ticker"] == "MMM"


def test_save_article_then_mark_status_updates_discovered_urls(conn):
    ensure_articles_table(conn)

    save_article(
        conn,
        {
            "id": 1,
            "ticker": "MMM",
            "company": "3M",
            "gics_sector": "Industrials",
            "gics_sub_industry": "Industrial Conglomerates",
            "title": "3M beats estimates",
            "author": "Jane Reporter",
            "pub_date": "2023-01-24T17:31:05+00:00",
            "body_text": "full article text here",
            "word_count": 4,
            "language": "en",
            "source_domain": "cnbc.com",
            "extraction_method": "jsonld+trafilatura",
            "fetch_status": "ok",
            "http_status_code": 200,
            "fetched_at": "2026-08-10T12:00:00",
        },
    )
    mark_status(conn, url_id=1, status="fetched", http_status_code=200)
    conn.commit()

    saved = conn.execute("SELECT * FROM articles WHERE id = 1").fetchone()
    assert saved is not None

    updated = conn.execute(
        "SELECT status, fetch_status_code FROM discovered_urls WHERE id = 1"
    ).fetchone()
    assert updated[0] == "fetched"
    assert updated[1] == 200


def test_get_status_counts_groups_discovered_urls_by_status(conn):
    conn.execute("UPDATE discovered_urls SET status = 'ok' WHERE id = 1")
    conn.commit()

    counts = get_status_counts(conn)

    assert counts == {"ok": 1, "pending": 1}


def test_ensure_articles_table_migrates_legacy_sector_column(conn):
    conn.execute(LEGACY_ARTICLES_SCHEMA)
    conn.execute(
        "INSERT INTO articles (id, ticker, company, sector, title, fetch_status) "
        "VALUES (1, 'MMM', '3M', 'Industrials', 'headline', 'ok')"
    )
    conn.commit()

    ensure_articles_table(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)").fetchall()}
    assert "gics_sector" in columns
    assert "gics_sub_industry" in columns
    assert "sector" not in columns

    row = conn.execute(
        "SELECT ticker, company, title, fetch_status FROM articles WHERE id = 1"
    ).fetchone()
    assert row == ("MMM", "3M", "headline", "ok")


def test_save_article_with_id_not_in_discovered_urls_is_rejected_once_fk_enforced(conn):
    ensure_articles_table(conn)
    enable_foreign_keys(conn)

    with pytest.raises(sqlite3.IntegrityError):
        save_article(
            conn,
            {
                "id": 9999,  # does not exist in discovered_urls
                "ticker": "MMM",
                "company": "3M",
                "gics_sector": None,
                "gics_sub_industry": None,
                "title": "orphan",
                "author": None,
                "pub_date": None,
                "body_text": "text",
                "word_count": 1,
                "language": "en",
                "source_domain": "cnbc.com",
                "extraction_method": "jsonld+trafilatura",
                "fetch_status": "ok",
                "http_status_code": 200,
                "fetched_at": "2026-08-11T00:00:00",
            },
        )
