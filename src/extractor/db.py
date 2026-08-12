"""SQLite access for the extraction pipeline.

Reads pending rows from `discovered_urls` (produced by the upstream
discovery crawler) and writes results into a new `articles` table in the
same database. See CLAUDE.md for the schema rationale.
"""
import sqlite3

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


def connect(db_path: str) -> sqlite3.Connection:
    """Open a connection configured the way this pipeline expects: FK
    enforcement on, rows returned as sqlite3.Row.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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


def get_pending_urls(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    sql = (
        "SELECT id, url, domain, company, ticker, source, title "
        "FROM discovered_urls WHERE status = 'pending' ORDER BY id"
    )
    params = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    return conn.execute(sql, params).fetchall()


def get_status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM discovered_urls GROUP BY status"
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def save_article(conn: sqlite3.Connection, article: dict) -> None:
    placeholders = ", ".join("?" for _ in ARTICLE_COLUMNS)
    columns = ", ".join(ARTICLE_COLUMNS)
    values = [article.get(col) for col in ARTICLE_COLUMNS]
    conn.execute(
        f"INSERT OR REPLACE INTO articles ({columns}) VALUES ({placeholders})",
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
