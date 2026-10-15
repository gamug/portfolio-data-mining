"""Reversible quarantine of fetched articles whose company match is not `accept` (T-057).

A fetched `(url, ticker)` pair classed `review` or `reject` has its `articles` row set to
`fetch_status = 'quarantined'`, the previous status kept in `articles.prev_fetch_status`.
`portfolio-nlp` reads only `fetch_status = 'ok'`, so a quarantined row is never processed,
and releasing it restores the row exactly. Nothing is deleted, `discovered_urls` is not
touched, and `accept` rows are never quarantined. The class lives on `discovered_urls`
(`match_class`, joined on `id`).

Query functions:
- `quarantine_articles(db, article_id=None)` -- quarantine the non-`accept` rows (one, or all).
- `release_articles(db, article_id=None)` -- restore quarantined rows whose class is `accept`.
- `sync_quarantine(db, article_id=None)` -- both; what the crawler runs after a fetch.
"""

from portfolio_common.db import Database

QUARANTINED = "quarantined"

__all__ = ["QUARANTINED", "quarantine_articles", "release_articles", "sync_quarantine"]


def _scope(article_id: int | None) -> tuple[str, tuple[int, ...]]:
    return ("", ()) if article_id is None else (" AND id = ?", (article_id,))


def quarantine_articles(db: Database, article_id: int | None = None) -> int:
    """Quarantine every `articles` row whose pair is not `accept`. Idempotent: a row that is
    already quarantined keeps its `prev_fetch_status`. Returns the rows changed."""
    extra, params = _scope(article_id)
    cur = db.execute(
        "UPDATE articles SET prev_fetch_status = fetch_status, fetch_status = ? "  # noqa: S608
        "WHERE (fetch_status IS NULL OR fetch_status != ?) "
        "AND id IN (SELECT id FROM discovered_urls WHERE match_class != 'accept')" + extra,
        (QUARANTINED, QUARANTINED, *params),
    )
    db.commit()
    return cur.rowcount


def release_articles(db: Database, article_id: int | None = None) -> int:
    """Restore every quarantined row whose pair is now `accept` (a promotion), putting back
    the status it had, and clear a stale `prev_fetch_status`. Returns the rows changed."""
    extra, params = _scope(article_id)
    # A re-fetch upserts `fetch_status` over 'quarantined' and leaves `prev_fetch_status` behind;
    # the CASE keeps that fresh status and only clears the stale value.
    cur = db.execute(
        "UPDATE articles SET fetch_status = CASE WHEN fetch_status = ? THEN prev_fetch_status "  # noqa: S608
        "ELSE fetch_status END, prev_fetch_status = NULL "
        "WHERE (fetch_status = ? OR prev_fetch_status IS NOT NULL) "
        "AND id IN (SELECT id FROM discovered_urls WHERE match_class = 'accept')" + extra,
        (QUARANTINED, QUARANTINED, *params),
    )
    db.commit()
    return cur.rowcount


def sync_quarantine(db: Database, article_id: int | None = None) -> tuple[int, int]:
    """Make `articles.fetch_status` agree with the class: quarantine non-`accept` rows, release
    promoted ones. Returns `(quarantined, released)`."""
    return quarantine_articles(db, article_id), release_articles(db, article_id)
