"""Canonical DDL for `data/urls.db`, the shared crawl-pipeline database.

`news_collector` owns `discovered_urls` + `discovery_progress`; `extractor`
owns `articles` (`FOREIGN KEY`-linked back to `discovered_urls` on the same
`id`). All three are defined here so the schema has one source of truth
instead of a copy in each module's connection code, plus forward-only,
idempotent migrations for databases created by an older version.

Version history: 1 the original tables; 2 `discovered_urls.match_class`/`match_reason`/`query`
(PLAN Work item 10, T-056b); 3 `articles.prev_fetch_status` (T-057); 4 the sitemap cache tables
`sitemap_entries` and `sitemap_crawls` (PLAN Work item 15, T-069; additive, nothing existing changes).

Bump `SCHEMA_VERSION` in lockstep with any DDL change and add a matching
step to `run_migrations()`.

Query functions (the only SQL text for this database's schema lives here):
- `apply_schema(db)` -- create every table/index if absent, stamp the schema version.
- `run_migrations(db)` -- bring an older database up to the current schema.
"""

from portfolio_common.db import Database

SCHEMA_VERSION = 4  # 2: discovered_urls match_class/match_reason/query (T-056b); 3: articles.prev_fetch_status (T-057); 4: sitemap cache (T-069)

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
    prev_fetch_status  TEXT,  -- set only by data_mining.quarantine (T-057)
    FOREIGN KEY (id) REFERENCES discovered_urls (id)
);
"""

# The sitemap cache (Work item 15): the in-range entries of a domain's sitemap and one row per
# crawl. Additive: no existing table or column changes, and no reader of `discovered_urls` or
# `articles` is affected. `sitemap_crawls.signature` hashes the domain's `sitemap_roots` and a
# cache version; a crawl in progress is a row with `finished_at IS NULL`. `WITHOUT ROWID` is
# SQLite's (the engine is SQLite today, see `portfolio_common.db`). This text is the DDL that
# `PLAN.md` Work item 15 shows for G2 and that `scripts/sitemap_preselect_check.py` measured
# (the script imports this constant: there is one copy of the DDL).
#
# Formats, which the design compares as strings: `lastmod` is an ISO date `YYYY-MM-DD` (NULL when
# the sitemap has none); `started_at`, `finished_at`, `first_seen`, `last_seen` and `range_start`
# are UTC ISO-8601 with microseconds and no offset (`2026-10-19T10:00:00.000000`; `range_start`
# is a date); `last_seen` equals the `started_at` of the crawl that saw the entry, which the
# schema does not enforce; `complete` is 0 or 1.
#
# Queries (their SQL is in `news_collector.sitemap_cache`, and a test runs it on this schema): the
# latest complete crawl is found by the primary key `(domain, started_at)`, already in the order the
# lookup wants; `ix_sitemap_crawls_latest` is not used by it (T-070b ordered by `started_at`, not by
# `finished_at`); `ix_sitemap_crawls_open` serves the crawl in progress.
SITEMAP_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS sitemap_entries (
    domain TEXT NOT NULL, url TEXT NOT NULL, lastmod TEXT, title TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, PRIMARY KEY (domain, url)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_sitemap_entries_lastmod ON sitemap_entries (domain, lastmod);
CREATE TABLE IF NOT EXISTS sitemap_crawls (
    domain TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    complete INTEGER NOT NULL CHECK (complete IN (0, 1)), range_start TEXT NOT NULL,
    signature TEXT NOT NULL,
    entries INTEGER, failures INTEGER, PRIMARY KEY (domain, started_at)
);
CREATE INDEX IF NOT EXISTS ix_sitemap_crawls_latest ON sitemap_crawls (domain, complete, finished_at);
CREATE INDEX IF NOT EXISTS ix_sitemap_crawls_open ON sitemap_crawls (domain, started_at)
    WHERE finished_at IS NULL;
"""

# Rollback of schema version 4: the indexes go with their tables, nothing else is touched.
SITEMAP_CACHE_ROLLBACK = """
DROP TABLE IF EXISTS sitemap_crawls;
DROP TABLE IF EXISTS sitemap_entries;
"""
PREVIOUS_SCHEMA_VERSION = 3

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

# `prev_fetch_status` is deliberately not listed above: the `save_article` upsert writes
# exactly these columns, so a re-fetch never touches it; only `data_mining.quarantine` does.

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
    db.create_schema(SITEMAP_CACHE_DDL)
    db.set_schema_version(SCHEMA_VERSION)


def run_migrations(db: Database) -> None:
    """Bring a database created by an older version up to the current
    schema. Every step is forward-only and idempotent, so a stale caller
    can still open a DB a newer writer has migrated.
    """
    _migrate_legacy_sector_column(db)
    _migrate_match_columns(db)
    _migrate_prev_fetch_status(db)
    _migrate_sitemap_cache(db)


def rollback_sitemap_cache(db: Database) -> None:
    """Undo schema version 4: drop the two cache tables and stamp version 3. Not called by any
    application path; it is the rollback the maintainer approves at G2, and a test runs it.

    Refuses a database that is not at version 4, so an operator cannot lower a later version or
    run it twice by mistake.
    """
    if db.schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"rollback_sitemap_cache expects schema version {SCHEMA_VERSION}, "
            f"found {db.schema_version}"
        )
    db.create_schema(SITEMAP_CACHE_ROLLBACK)
    db.set_schema_version(PREVIOUS_SCHEMA_VERSION)


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


def _migrate_prev_fetch_status(db: Database) -> None:
    """`articles` predating the quarantine (schema version < 3) has no
    `prev_fetch_status`. Add it, empty. No-op on a current table."""
    if not db.table_columns("articles"):
        return  # table doesn't exist yet -- nothing to migrate
    db.ensure_columns("articles", {"prev_fetch_status": "TEXT"})


def _migrate_sitemap_cache(db: Database) -> None:
    """A database of schema version < 4 has no sitemap cache. Create the two tables and their
    indexes, empty. No-op on a current database (every statement is `IF NOT EXISTS`) and on an
    empty one, like the other steps (`apply_schema` creates the whole schema there)."""
    if not db.table_columns("discovered_urls"):
        return  # table doesn't exist yet -- nothing to migrate
    db.create_schema(SITEMAP_CACHE_DDL)
