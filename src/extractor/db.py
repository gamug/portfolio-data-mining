"""SQLite access for the extraction pipeline.

Reads pending rows from `discovered_urls` (produced by the upstream
discovery crawler) and writes results into the `articles` table in the same
database. The connection engine (pragma policy, ATTACH/DETACH, statement
execution) lives in `portfolio_common.db.Database`; this domain's own
defaults (where the file lives, which pragma flags this stage needs) and
schema live in `data_mining.db` / `data_mining.schema` -- the single source
of truth shared with `news_collector`. This module keeps only the
extractor-specific queries. See CLAUDE.md.
"""

from portfolio_common.db import Database, Row, in_clause

from data_mining.db import connect as _connect
from data_mining.matching import CLASS_RANK, classify
from data_mining.schema import (
    ARTICLE_COLUMNS,
    ARTICLES_SCHEMA,
    apply_schema,
    run_migrations,
)

__all__ = [
    "ARTICLES_SCHEMA",
    "ARTICLE_COLUMNS",
    "connect",
    "enable_foreign_keys",
    "ensure_articles_table",
    "get_match_class_counts",
    "get_pending_urls",
    "get_status_counts",
    "get_urls_by_status",
    "mark_status",
    "reevaluate_match",
    "save_article",
]


def connect(db_path: str, *, check_same_thread: bool = True) -> Database:
    """Open a connection configured the way this pipeline stage expects: FK
    enforcement on, rows returned as `portfolio_common.db.Row`, `busy_timeout` set.

    `check_same_thread=False` lifts the engine's thread-binding -- needed by
    `apps/news_crawler_api.py`, whose async route handlers touch a
    connection opened in a different thread than FastAPI's sync-dependency
    resolver used.
    """
    return _connect(db_path, foreign_keys=True, check_same_thread=check_same_thread)


def enable_foreign_keys(db: Database) -> None:
    """Turn on FK enforcement on an already-open connection. Only needed for
    a connection that didn't go through `connect(..., foreign_keys=True)` in
    the first place -- e.g. a test fixture that opened its own. The one raw
    engine pragma kept in this repo; `connect(foreign_keys=True)` (which does
    the same thing inside `portfolio_common.db.Database`) is the normal path."""
    db.execute("PRAGMA foreign_keys = ON")


def ensure_articles_table(db: Database) -> None:
    """Create the pipeline schema if absent and apply pending migrations.
    Idempotent -- safe to call on every startup.
    """
    apply_schema(db)
    run_migrations(db)
    db.commit()


def get_urls_by_status(db: Database, statuses: list[str], limit: int | None = None) -> list[Row]:
    """Fetch discovered_urls rows whose status is in `statuses`, e.g.
    ['pending'] for a normal run or ['failed'] to retry previous failures --
    no separate reset-to-pending step needed, unlike the API's
    /discovered/{id}/reset endpoint.

    A matcher-rejected row (`status = 'skipped' AND match_class = 'reject'`) is
    never returned, whatever `statuses` asks for: a retry of `skipped` rows must
    not fetch a quarantined pair.
    """
    placeholders = in_clause(statuses)
    # S608: `placeholders` is just a run of literal "?" characters (one per
    # `statuses` item) -- the actual values are bound as query params below,
    # never interpolated into the SQL text.
    sql = (
        f"SELECT id, url, domain, company, ticker, source, title "  # noqa: S608
        f"FROM discovered_urls WHERE status IN {placeholders} "
        f"AND match_class != 'reject' ORDER BY id"
    )
    params: list = list(statuses)
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return db.execute(sql, params).fetchall()


def get_pending_urls(db: Database, limit: int | None = None) -> list[Row]:
    return get_urls_by_status(db, ["pending"], limit=limit)


def get_status_counts(db: Database) -> dict[str, int]:
    """Rows per status, leaving out matcher-rejected pairs (`match_class = 'reject'`):
    they were never work for the extractor, so they must not read as processed
    (`skipped`). See `get_match_class_counts` for them."""
    rows = db.execute(
        "SELECT status, COUNT(*) AS c FROM discovered_urls WHERE match_class != 'reject' "
        "GROUP BY status"
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def get_match_class_counts(db: Database) -> dict[str, int]:
    """Rows per company-matching class (`accept` / `review` / `reject`)."""
    rows = db.execute(
        "SELECT match_class, COUNT(*) AS c FROM discovered_urls GROUP BY match_class"
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def save_article(db: Database, article: dict) -> None:
    # `columns`/`placeholders` come from the fixed ARTICLE_COLUMNS tuple, not
    # caller input -- values are bound as query params, never interpolated.
    # (Not an Allowlist check: that guards a single caller-influenced
    # identifier, not a whole fixed internal constant like this one.)
    values = [article.get(col) for col in ARTICLE_COLUMNS]
    db.execute(
        db.dialect.upsert(
            "articles",
            ARTICLE_COLUMNS,
            conflict=("id",),
            update=[c for c in ARTICLE_COLUMNS if c != "id"],
        ),
        values,
    )
    db.commit()


def mark_status(
    db: Database,
    url_id: int,
    status: str,
    http_status_code: int | None = None,
) -> None:
    db.execute(
        "UPDATE discovered_urls SET status = ?, fetch_status_code = ? WHERE id = ?",
        (status, http_status_code, url_id),
    )
    db.commit()


def reevaluate_match(db: Database, url_id: int, fetched_title: str | None = None) -> bool:
    """Re-class a fetched pair with the stored title (and the fetched one) and
    promote it when the class rises (`review` -> `accept`). Never demotes.

    The UPDATE writes only `match_class`/`match_reason` and is guarded in SQL on the
    class that was read, so a concurrent writer (the collector's promotion) is never
    overwritten with a lower class. A `reject` row is left alone: it was never
    fetched, and lifting it is the queue's job (it also resets `status`).
    Returns True when a row was promoted.
    """
    row = db.execute(
        "SELECT url, title, company, ticker, match_class FROM discovered_urls WHERE id = ?",
        (url_id,),
    ).fetchone()
    if row is None or row["match_class"] == "reject":
        return False
    title = " ".join(t for t in (row["title"], fetched_title) if t)
    match = classify(row["url"], title, row["company"], row["ticker"])
    if CLASS_RANK[match.cls] <= CLASS_RANK[row["match_class"]]:
        return False
    cur = db.execute(
        "UPDATE discovered_urls SET match_class = ?, match_reason = ? "
        "WHERE id = ? AND match_class = ?",
        (match.cls, match.reason, url_id, row["match_class"]),
    )
    db.commit()
    return cur.rowcount == 1
