"""SQLite access for the extraction pipeline.

Reads pending rows from `discovered_urls` (produced by the upstream
discovery crawler) and writes results into the `articles` table in the same
database. Connection handling and the schema itself live in `common.db` /
`common.schema` -- the single source of truth shared with `news_collector`;
this module keeps only the extractor-specific queries. See CLAUDE.md.
"""

import sqlite3

from portfolio_common.db import connect as _connect
from portfolio_common.db import enable_foreign_keys

# ARTICLES_SCHEMA / _migrate_legacy_sector_column are re-exported only for
# backward compatibility with pre-consolidation imports.
from portfolio_common.schema import (
    ARTICLE_COLUMNS,
    ARTICLES_SCHEMA,
    _migrate_legacy_sector_column,  # noqa: F401
    apply_schema,
    run_migrations,
)

__all__ = [
    "ARTICLES_SCHEMA",
    "ARTICLE_COLUMNS",
    "connect",
    "enable_foreign_keys",
    "ensure_articles_table",
    "get_pending_urls",
    "get_status_counts",
    "get_urls_by_status",
    "mark_status",
    "save_article",
]


def connect(db_path: str) -> sqlite3.Connection:
    """Open a connection configured the way this pipeline stage expects: FK
    enforcement on, rows returned as `sqlite3.Row`, `busy_timeout` set.
    """
    return _connect(db_path, foreign_keys=True)


def ensure_articles_table(conn: sqlite3.Connection) -> None:
    """Create the pipeline schema if absent and apply pending migrations.
    Idempotent -- safe to call on every startup.
    """
    apply_schema(conn)
    run_migrations(conn)
    conn.commit()


def get_urls_by_status(
    conn: sqlite3.Connection, statuses: list[str], limit: int | None = None
) -> list[sqlite3.Row]:
    """Fetch discovered_urls rows whose status is in `statuses`, e.g.
    ['pending'] for a normal run or ['failed'] to retry previous failures --
    no separate reset-to-pending step needed, unlike the API's
    /discovered/{id}/reset endpoint.
    """
    conn.row_factory = sqlite3.Row
    placeholders = ", ".join("?" for _ in statuses)
    # S608: `placeholders` is just a run of literal "?" characters (one per
    # `statuses` item) -- the actual values are bound as query params below,
    # never interpolated into the SQL text.
    sql = (
        f"SELECT id, url, domain, company, ticker, source, title "  # noqa: S608
        f"FROM discovered_urls WHERE status IN ({placeholders}) ORDER BY id"
    )
    params: list = list(statuses)
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def get_pending_urls(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    return get_urls_by_status(conn, ["pending"], limit=limit)


def get_status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM discovered_urls GROUP BY status"
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def save_article(conn: sqlite3.Connection, article: dict) -> None:
    placeholders = ", ".join("?" for _ in ARTICLE_COLUMNS)
    columns = ", ".join(ARTICLE_COLUMNS)
    values = [article.get(col) for col in ARTICLE_COLUMNS]
    # S608: `columns`/`placeholders` come from the fixed ARTICLE_COLUMNS tuple,
    # not caller input -- values are bound as query params, never interpolated.
    conn.execute(
        f"INSERT OR REPLACE INTO articles ({columns}) VALUES ({placeholders})",  # noqa: S608
        values,
    )
    conn.commit()


def mark_status(
    conn: sqlite3.Connection,
    url_id: int,
    status: str,
    http_status_code: int | None = None,
) -> None:
    conn.execute(
        "UPDATE discovered_urls SET status = ?, fetch_status_code = ? WHERE id = ?",
        (status, http_status_code, url_id),
    )
    conn.commit()
