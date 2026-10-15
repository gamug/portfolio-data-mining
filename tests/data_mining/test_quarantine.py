"""Reversible quarantine of non-`accept` articles (T-057)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from portfolio_common.db import Database

from data_mining.quarantine import (
    QUARANTINED,
    quarantine_articles,
    release_articles,
    sync_quarantine,
)
from data_mining.schema import apply_schema, run_migrations


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database(sqlite3.connect(":memory:"))
    apply_schema(database)
    run_migrations(database)
    # id, class, article status
    for i, (cls, status) in enumerate(
        [
            ("accept", "ok"),
            ("review", "ok"),
            ("reject", "ok"),
            ("review", "failed"),
            ("reject", None),
        ],
        start=1,
    ):
        database.execute(
            "INSERT INTO discovered_urls (id, url, domain, company, ticker, source, discovered_at,"
            " match_class, status) VALUES (?, ?, 'd', 'C', 'T', 'sitemap', 'x', ?, 'fetched')",
            (i, f"https://d/{i}", cls),
        )
        database.execute("INSERT INTO articles (id, fetch_status) VALUES (?, ?)", (i, status))
    database.commit()
    yield database
    database.close()


def _statuses(db: Database) -> list[tuple[str | None, str | None]]:
    return [
        tuple(r)
        for r in db.execute("SELECT fetch_status, prev_fetch_status FROM articles ORDER BY id")
    ]


def test_quarantines_review_and_reject_only_and_keeps_the_previous_status(db: Database) -> None:
    assert quarantine_articles(db) == 4
    assert _statuses(db) == [
        ("ok", None),
        (QUARANTINED, "ok"),
        (QUARANTINED, "ok"),
        (QUARANTINED, "failed"),
        (QUARANTINED, None),
    ]


def test_quarantine_is_idempotent_and_keeps_the_original_status(db: Database) -> None:
    quarantine_articles(db)
    assert quarantine_articles(db) == 0
    assert _statuses(db)[1] == (QUARANTINED, "ok")  # not overwritten with 'quarantined'


def test_release_restores_a_promoted_row_exactly(db: Database) -> None:
    before = _statuses(db)
    quarantine_articles(db)
    db.execute("UPDATE discovered_urls SET match_class = 'accept'")  # every pair promoted
    db.commit()

    assert release_articles(db) == 4
    assert _statuses(db) == before


def test_release_leaves_still_unaccepted_rows_quarantined(db: Database) -> None:
    quarantine_articles(db)
    assert release_articles(db) == 0
    assert _statuses(db)[1][0] == QUARANTINED


def test_sync_for_one_article_touches_only_that_row(db: Database) -> None:
    assert sync_quarantine(db, 2) == (1, 0)
    assert _statuses(db)[1] == (QUARANTINED, "ok")
    assert _statuses(db)[2] == ("ok", None)


def test_discovered_urls_is_never_modified(db: Database) -> None:
    before = db.execute("SELECT * FROM discovered_urls ORDER BY id").fetchall()
    quarantine_articles(db)
    release_articles(db)
    assert db.execute("SELECT * FROM discovered_urls ORDER BY id").fetchall() == before


def test_release_clears_a_stale_prev_status_left_by_a_refetch(db: Database) -> None:
    quarantine_articles(db)
    # a re-fetch upserts fetch_status over 'quarantined'; the pair is then promoted
    db.execute("UPDATE articles SET fetch_status = 'ok' WHERE id = 2")
    db.execute("UPDATE discovered_urls SET match_class = 'accept' WHERE id = 2")
    db.commit()

    release_articles(db)

    assert _statuses(db)[1] == ("ok", None)
