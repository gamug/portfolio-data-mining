"""Canonical DDL for the shared pipeline database.

`news_collector` owns `discovered_urls` + `discovery_progress`; `extractor`
owns `articles` (`FOREIGN KEY`-linked back to `discovered_urls` on the same
`id`). All three are defined here so the schema has one source of truth
instead of a copy in each module's connection code, plus forward-only,
idempotent migrations for databases created by an older version.

Bump `SCHEMA_VERSION` in lockstep with any DDL change and add a matching
step to `run_migrations()`.
"""

import sqlite3

SCHEMA_VERSION = 1

_DISCOVERY_DDL = """
CREATE TABLE IF NOT EXISTS discovered_urls (
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
);

CREATE INDEX IF NOT EXISTS idx_status  ON discovered_urls (status);
CREATE INDEX IF NOT EXISTS idx_domain  ON discovered_urls (domain);
CREATE INDEX IF NOT EXISTS idx_ticker  ON discovered_urls (ticker);

CREATE TABLE IF NOT EXISTS discovery_progress (
    ticker          TEXT    NOT NULL,
    domain          TEXT    NOT NULL,
    start_date      TEXT    NOT NULL,
    end_date        TEXT    NOT NULL,
    completed_at    TEXT    NOT NULL,
    inserted_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ticker, domain, start_date, end_date)
);

CREATE INDEX IF NOT EXISTS idx_progress_domain_range
    ON discovery_progress (domain, start_date, end_date);
"""

_ARTICLES_DDL = """
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
);
"""

# Insert-order column list for `articles`, kept next to the DDL it mirrors
# so the two can't drift.
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

# Backward-compatible alias for the pre-consolidation name.
ARTICLES_SCHEMA = _ARTICLES_DDL


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create every pipeline table + index if absent, and stamp
    `PRAGMA user_version`. Idempotent -- safe on every startup, from either
    stage's connection (each stage only writes its own tables, but defining
    the whole schema from one constant is what keeps the two in sync).
    """
    conn.executescript(_DISCOVERY_DDL)
    conn.executescript(_ARTICLES_DDL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def run_migrations(conn: sqlite3.Connection) -> None:
    """Bring a database created by an older version up to the current
    schema. Every step is forward-only and idempotent, so a stale caller
    can still open a DB a newer writer has migrated.
    """
    _migrate_legacy_sector_column(conn)


def _migrate_legacy_sector_column(conn: sqlite3.Connection) -> None:
    """`articles` predating `gics_sector`/`gics_sub_industry` had a single
    `sector` column. Add the new columns and drop the old one. No-op on a
    table that's already current or was just created by `apply_schema()`.
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
