"""Canonical DDL for `data/urls.db`, the shared crawl-pipeline database.

`news_collector` owns `discovered_urls` + `discovery_progress`; `extractor`
owns `articles` (`FOREIGN KEY`-linked back to `discovered_urls` on the same
`id`). All three are defined here so the schema has one source of truth
instead of a copy in each module's connection code, plus forward-only,
idempotent migrations for databases created by an older version.

Version history: 1 the original tables; 2 `discovered_urls.match_class`/`match_reason`/`query`
(PLAN Work item 10, T-056b).

Bump `SCHEMA_VERSION` in lockstep with any DDL change and add a matching
step to `run_migrations()`.

Query functions (the only SQL text for this database's schema lives here):
- `apply_schema(db)` -- create every table/index if absent, stamp the schema version.
- `run_migrations(db)` -- bring an older database up to the current schema.
"""

from portfolio_common.db import Database

SCHEMA_VERSION = 2  # 2: discovered_urls.match_class/match_reason/query (T-056b)

# `{autoincrement_pk}` is filled in from the engine's dialect by
# `apply_schema` (str.replace); everything else is standard SQL.
_DISCOVERY_DDL = """
CREATE TABLE IF NOT EXISTS discovered_urls (
    id              {autoincrement_pk},
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
    match_class     TEXT    NOT NULL DEFAULT 'accept'
                    CHECK (match_class IN ('accept', 'review', 'reject')),
    match_reason    TEXT,
    query           TEXT,
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


def apply_schema(db: Database) -> None:
    """Create every pipeline table + index if absent, and stamp the schema
    version. Idempotent -- safe on every startup, from either stage's
    connection (each stage only writes its own tables, but defining the whole
    schema from one constant is what keeps the two in sync).
    """
    db.create_schema(_DISCOVERY_DDL.replace("{autoincrement_pk}", db.dialect.autoincrement_pk))
    db.create_schema(_ARTICLES_DDL)
    db.set_schema_version(SCHEMA_VERSION)


def run_migrations(db: Database) -> None:
    """Bring a database created by an older version up to the current
    schema. Every step is forward-only and idempotent, so a stale caller
    can still open a DB a newer writer has migrated.
    """
    _migrate_legacy_sector_column(db)
    _migrate_match_columns(db)


# `discovered_urls.status` and `match_class` together: a matcher-rejected pair is
# `status = 'skipped' AND match_class = 'reject'`, written at insert and never
# fetched; `mark_skipped` (paywall, irrelevant after inspection) leaves `match_class`
# alone. Existing rows get the default `accept`; T-057's re-tag reclassifies them.
MATCH_COLUMNS = {
    "match_class": "TEXT NOT NULL DEFAULT 'accept' CHECK (match_class IN ('accept', 'review', 'reject'))",
    "match_reason": "TEXT",
    "query": "TEXT",
}


def _migrate_match_columns(db: Database) -> None:
    """`discovered_urls` predating the matcher (schema version 1) has no
    `match_class`/`match_reason`/`query`. Add them and the `idx_match_class`
    index (kept out of `_DISCOVERY_DDL` because that script would run it against
    the old table before the column exists). No-op on a current table.
    """
    if not db.table_columns("discovered_urls"):
        return  # table doesn't exist yet -- nothing to migrate
    db.ensure_columns("discovered_urls", MATCH_COLUMNS)
    db.execute("CREATE INDEX IF NOT EXISTS idx_match_class ON discovered_urls (match_class)")


def _migrate_legacy_sector_column(db: Database) -> None:
    """`articles` predating `gics_sector`/`gics_sub_industry` had a single
    `sector` column. Add the new columns and drop the old one. No-op on a
    table that's already current or was just created by `apply_schema()`.
    """
    columns = set(db.table_columns("articles"))
    if not columns:
        return  # table doesn't exist yet -- nothing to migrate
    db.ensure_columns("articles", {"gics_sector": "TEXT", "gics_sub_industry": "TEXT"})
    if "sector" in columns:
        # SQLite >= 3.35 DROP COLUMN; a one-time destructive step a non-SQLite
        # migration path would express differently.
        db.execute("ALTER TABLE articles DROP COLUMN sector")
