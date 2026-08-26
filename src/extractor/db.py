"""SQLite access for the extraction pipeline.

Reads pending rows from `discovered_urls` (produced by the upstream
discovery crawler) and writes results into a new `articles` table in the
same database. See CLAUDE.md for the schema rationale.
"""

import os
import sqlite3

from common.db_backend import is_remote_url, open_connection

ARTICLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id                 INTEGER PRIMARY KEY,  -- FK to discovered_urls.id
    ticker             TEXT,
    company            TEXT,
    gics_sector        TEXT,
    gics_sub_industry  TEXT,
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

ARTICLE_COLUMNS = (
    "id",
    "ticker",
    "company",
    "gics_sector",
    "gics_sub_industry",
    "title",
    "author",
    "pub_date",
    "body_text",
    "word_count",
    "language",
    "source_domain",
    "extraction_method",
    "fetch_status",
    "http_status_code",
    "fetched_at",
)


def enable_foreign_keys(conn: sqlite3.Connection) -> None:
    """Turn on FK constraint enforcement for this connection.

    SQLite declares FK constraints in the schema (see ARTICLES_SCHEMA) but,
    for backward-compatibility reasons, does not enforce them unless this
    pragma is set on every connection -- it is not a persistent DB setting.
    Without it, `articles.id -> discovered_urls.id` is documentation only,
    not a guarantee.
    """
    conn.execute("PRAGMA foreign_keys = ON")


# This DB file is shared with news_collector and news_nlp (see CLAUDE.md).
# Without a busy_timeout, a connection that finds the file locked by another
# one's write transaction gets an immediate
# `sqlite3.OperationalError: database is locked` instead of a retry -- most
# likely here, since this stage runs a high-throughput loop of individual
# save_article()/mark_status() commits while news_collector may still be
# writing discovered_urls concurrently. Not persistent (like foreign_keys,
# unlike journal_mode), so it has to be set on every connection.
BUSY_TIMEOUT_MS = 30_000


def connect(db_path: str) -> sqlite3.Connection:
    """Open a connection configured the way this pipeline expects: FK
    enforcement on, rows returned as sqlite3.Row.

    `db_path` doubles as $DATABASE_URL's raw value -- a `libsql://...` URL
    (with $TURSO_AUTH_TOKEN set) routes this through Turso instead of local
    SQLite. See common/db_backend.py.
    """
    conn = open_connection(db_path, auth_token=os.environ.get("TURSO_AUTH_TOKEN"))
    conn.row_factory = sqlite3.Row
    if not is_remote_url(db_path):
        # busy_timeout is a local-WAL-file concern -- Turso rejects it
        # outright ("SQL not allowed statement"). See common/db_backend.py.
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    enable_foreign_keys(conn)
    return conn


def _migrate_legacy_sector_column(conn: sqlite3.Connection) -> None:
    """Bring a pre-existing `articles` table (created before gics_sector/
    gics_sub_industry existed) up to the current schema. Idempotent -- safe
    to call on every startup, including against a table that's already
    current or was just freshly created by ARTICLES_SCHEMA.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)").fetchall()}
    if not columns:
        return  # table doesn't exist yet -- nothing to migrate
    if "gics_sector" not in columns:
        conn.execute("ALTER TABLE articles ADD COLUMN gics_sector TEXT")
    if "gics_sub_industry" not in columns:
        conn.execute("ALTER TABLE articles ADD COLUMN gics_sub_industry TEXT")
    if "sector" in columns:
        conn.execute("ALTER TABLE articles DROP COLUMN sector")


def ensure_articles_table(conn: sqlite3.Connection) -> None:
    conn.execute(ARTICLES_SCHEMA)
    _migrate_legacy_sector_column(conn)
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
