"""SQLite access layer: schema creation + read/write helpers for the NLP pipeline.

Reads from the existing `articles` table and writes to three results tables --
`article_sentiment`, `article_entities`, and `article_category` -- each keyed
by article_id. Also provides read-only query helpers backing the FastAPI
query endpoints.
"""

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

# Called here (not just in apps/news_nlp_api.py) so DATABASE_URL is honored
# by every entrypoint that imports this module -- including the standalone
# `python -m news_nlp.setup` / `python -m news_nlp.pipeline` CLI paths,
# which never go through the FastAPI app. Safe to call more than once.
load_dotenv()

# $DATABASE_URL is a filesystem path today (this is still SQLite) -- kept as
# an env var, not a hardcoded literal, so pointing this at a real connection
# string later (e.g. a hosted Postgres/libSQL DSN) is a one-line env change,
# not a code change. Falls back to the pre-existing default when unset.
#
# A relative value (the .env.example default, "data/urls.db") is resolved
# against the project root rather than left relative to the process's CWD --
# unlike the other DATABASE_URL call sites (extractor/, news_collector/),
# this module gets imported by standalone CLI entrypoints
# (`python -m news_nlp.setup`/`.pipeline`) that aren't guaranteed to be
# launched from the repo root, so a CWD-relative path would silently point
# at the wrong file depending on where the command was run from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_db_path_env = os.environ.get("DATABASE_URL")
DB_PATH = (
    (Path(_db_path_env) if Path(_db_path_env).is_absolute() else _PROJECT_ROOT / _db_path_env)
    if _db_path_env
    else _PROJECT_ROOT / "data" / "urls.db"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS article_sentiment (
    article_id INTEGER PRIMARY KEY REFERENCES articles(id),
    label TEXT NOT NULL,
    score REAL NOT NULL,
    positive REAL NOT NULL,
    negative REAL NOT NULL,
    neutral REAL NOT NULL,
    model_name TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS article_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES articles(id),
    entity_type TEXT NOT NULL,   -- PER / LOC / ORG
    text TEXT NOT NULL,          -- surface span, e.g. "Apple Inc."
    start_char INTEGER NOT NULL,
    end_char INTEGER NOT NULL,
    score REAL,
    model_name TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_article_entities_article_id
    ON article_entities(article_id);

CREATE TABLE IF NOT EXISTS article_summary (
    article_id   INTEGER PRIMARY KEY REFERENCES articles(id),
    summary_text TEXT NOT NULL,
    num_chunks   INTEGER NOT NULL,
    model_name   TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sector_summary (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    gics_sector       TEXT NOT NULL,
    gics_sub_industry TEXT NOT NULL,
    week_start        TEXT NOT NULL,
    week_end          TEXT NOT NULL,
    summary_text      TEXT NOT NULL,
    num_articles      INTEGER NOT NULL,
    num_companies     INTEGER NOT NULL,
    model_name        TEXT NOT NULL,
    processed_at      TEXT NOT NULL,
    UNIQUE (gics_sector, gics_sub_industry, week_start)
);

-- One row per article: the winning category (or 'other') plus the full
-- 9-way NLI score distribution, so low-confidence 'other' picks are
-- auditable and CATEGORY_CONFIDENCE_THRESHOLD can be retuned later without
-- reprocessing. See docs/category-taxonomy.md for what each column means
-- and where the taxonomy came from.
CREATE TABLE IF NOT EXISTS article_category (
    article_id INTEGER PRIMARY KEY REFERENCES articles(id),
    label TEXT NOT NULL,   -- winning category slug, or 'other'
    score REAL NOT NULL,   -- winning slug's NLI entailment probability (pre-threshold)
    earnings_performance REAL NOT NULL,
    mergers_acquisitions REAL NOT NULL,
    leadership_governance REAL NOT NULL,
    legal_regulatory REAL NOT NULL,
    product_innovation REAL NOT NULL,
    capital_shareholder_returns REAL NOT NULL,
    labor_human_capital REAL NOT NULL,
    market_analyst_sentiment REAL NOT NULL,
    partnerships_business_dev REAL NOT NULL,
    model_name TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
"""


# This DB file is shared with news_collector and extractor (see CLAUDE.md).
# Without a busy_timeout, a connection that finds the file locked by another
# one's write transaction gets an immediate
# `sqlite3.OperationalError: database is locked` instead of a retry. Not
# persistent (like foreign_keys, unlike journal_mode), so it has to be set
# on every connection.
BUSY_TIMEOUT_MS = 30_000


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    # Row objects support both key access (row["col"], used by the query
    # helpers below) and positional unpacking (used by existing call sites
    # like `for article_id, body_text in fetch_pending_articles(...)`).
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


_PENDING_ARTICLE_TABLES = {"article_sentiment", "article_entities"}


def fetch_pending_articles(
    conn: sqlite3.Connection, table: str, limit: int | None = None
) -> list[sqlite3.Row]:
    """Return (id, body_text) rows from `articles` not yet present in `table`,
    restricted to successfully fetched, non-empty articles."""
    if table not in _PENDING_ARTICLE_TABLES:
        raise ValueError(f"table must be one of {sorted(_PENDING_ARTICLE_TABLES)}, got {table!r}")
    # S608: `table` is checked against the _PENDING_ARTICLE_TABLES allowlist above.
    sql = f"""
        SELECT a.id, a.body_text
        FROM articles a
        LEFT JOIN {table} r ON r.article_id = a.id
        WHERE r.article_id IS NULL
          AND a.fetch_status = 'ok'
          AND a.body_text IS NOT NULL
          AND TRIM(a.body_text) != ''
        ORDER BY a.id
    """  # noqa: S608
    params: list = []
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return conn.execute(sql, params).fetchall()


def fetch_pending_category_articles(
    conn: sqlite3.Connection, limit: int | None = None
) -> list[sqlite3.Row]:
    """Return (id, title, body_text) rows from `articles` not yet present in
    article_category, same eligibility filter as fetch_pending_articles. A
    dedicated query (not a widened fetch_pending_articles) since that
    function's (id, body_text) two-tuple shape is unpacked directly at the
    sentiment/NER call sites -- widening it would break those."""
    sql = """
        SELECT a.id, a.title, a.body_text
        FROM articles a
        LEFT JOIN article_category r ON r.article_id = a.id
        WHERE r.article_id IS NULL
          AND a.fetch_status = 'ok'
          AND a.body_text IS NOT NULL
          AND TRIM(a.body_text) != ''
        ORDER BY a.id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def write_sentiment(
    conn: sqlite3.Connection,
    article_id: int,
    label: str,
    score: float,
    positive: float,
    negative: float,
    neutral: float,
    model_name: str,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO article_sentiment
           (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (article_id, label, score, positive, negative, neutral, model_name, now_iso()),
    )


def write_category(
    conn: sqlite3.Connection,
    article_id: int,
    label: str,
    score: float,
    scores: dict[str, float],
    model_name: str,
) -> None:
    """`scores` must have one entry per src.news_nlp.categories.CATEGORY_SLUGS
    slug (the full 9-way distribution) -- `label`/`score` are the winning
    slug (or 'other') and its probability, kept separately from the raw
    distribution so a human correction (see corrections.update_category) can
    change the winner without touching the audit trail."""
    conn.execute(
        """INSERT OR REPLACE INTO article_category
           (article_id, label, score, earnings_performance, mergers_acquisitions,
            leadership_governance, legal_regulatory, product_innovation,
            capital_shareholder_returns, labor_human_capital, market_analyst_sentiment,
            partnerships_business_dev, model_name, processed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            article_id,
            label,
            score,
            scores["earnings_performance"],
            scores["mergers_acquisitions"],
            scores["leadership_governance"],
            scores["legal_regulatory"],
            scores["product_innovation"],
            scores["capital_shareholder_returns"],
            scores["labor_human_capital"],
            scores["market_analyst_sentiment"],
            scores["partnerships_business_dev"],
            model_name,
            now_iso(),
        ),
    )


def write_entities(
    conn: sqlite3.Connection, article_id: int, entities: list[dict], model_name: str
) -> None:
    # Idempotency: clear any prior entities for this article before inserting fresh ones.
    conn.execute("DELETE FROM article_entities WHERE article_id = ?", (article_id,))
    ts = now_iso()
    conn.executemany(
        """INSERT INTO article_entities
           (article_id, entity_type, text, start_char, end_char, score, model_name, processed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                article_id,
                e["entity_type"],
                e["text"],
                e["start_char"],
                e["end_char"],
                e.get("score"),
                model_name,
                ts,
            )
            for e in entities
        ],
    )


def fetch_pending_company_summaries(
    conn: sqlite3.Connection, limit: int | None = None
) -> list[sqlite3.Row]:
    """Return raw fields for articles ready for c_summary generation: a
    successful fetch (http_status_code=200), a computed sentiment, at least
    one qualifying entity (score>0.8, non-numeric), and no article_summary
    row yet. Adapts query.sql's `source_text` CTE -- the two INNER JOINs mean
    an article with sentiment but zero qualifying entities is never selected
    here, same as the original query.

    Returns raw columns rather than the assembled template text: SQLite
    string literals don't interpret \\n as an escape (unlike Python), so
    template assembly happens in build_company_summary_input() instead.
    """
    sql = """
        WITH entities AS (
            SELECT article_id, GROUP_CONCAT(text, ', ') AS entities
            FROM article_entities
            WHERE score > 0.8 AND text NOT GLOB '[0-9]'
            GROUP BY article_id
        )
        SELECT
            a.id AS article_id, a.ticker, a.company, a.gics_sector, a.gics_sub_industry,
            a.title, a.body_text, s.label AS sentiment_label, s.score AS sentiment_confidence,
            e.entities
        FROM articles a
        INNER JOIN article_sentiment s ON s.article_id = a.id
        INNER JOIN entities e ON e.article_id = a.id
        LEFT JOIN article_summary asum ON asum.article_id = a.id
        WHERE a.http_status_code = 200 AND asum.article_id IS NULL
        ORDER BY a.id
    """
    params: list = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def build_company_summary_input(row: sqlite3.Row) -> str:
    """Assemble the METADATA/NLP FEATURES/TEXT BODY template (query.sql's
    intent) with real newlines, for one row from fetch_pending_company_summaries."""
    return (
        f"METADATA:\nTicker-{row['ticker']}\nCompany-{row['company']}\n\n"
        f"NLP FEATURES:\nSentiment-{row['sentiment_label']} "
        f"Confidence-{row['sentiment_confidence']}\nEntities-{row['entities']}\n\n"
        f"TEXT BODY:\nTitle-{row['title']}\nBody-{row['body_text']}"
    )


def write_company_summary(
    conn: sqlite3.Connection, article_id: int, summary_text: str, num_chunks: int, model_name: str
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO article_summary
           (article_id, summary_text, num_chunks, model_name, processed_at)
           VALUES (?, ?, ?, ?, ?)""",
        (article_id, summary_text, num_chunks, model_name, now_iso()),
    )


# Monday-start ISO week containing a given date, via SQLite's 'weekday N'
# modifier (0=Sunday): shift forward to the next Sunday (a no-op if the date
# already is one), then step back 6 days to land on that week's Monday.
_WEEK_START_EXPR = "date({col}, 'weekday 0', '-6 days')"
_WEEK_END_EXPR = "date({col}, 'weekday 0')"


def fetch_pending_sector_weeks(
    conn: sqlite3.Connection, limit: int | None = None
) -> list[sqlite3.Row]:
    """Return (gics_sector, gics_sub_industry, week_start, week_end) tuples
    ready for sector_summary generation: closed weeks (week_end already in
    the past, so a partial week is never summarized and later regenerated)
    with at least one c_summary'd article, not yet in sector_summary.
    Weeks are bucketed off pub_date, falling back to fetched_at when
    pub_date is NULL.
    """
    date_col = "COALESCE(a.pub_date, a.fetched_at)"
    week_start_expr = _WEEK_START_EXPR.format(col=date_col)
    week_end_expr = _WEEK_END_EXPR.format(col=date_col)
    # S608: week_start_expr/week_end_expr come from the hardcoded
    # _WEEK_START_EXPR/_WEEK_END_EXPR templates, not caller input.
    sql = f"""
        SELECT
            a.gics_sector AS gics_sector,
            a.gics_sub_industry AS gics_sub_industry,
            {week_start_expr} AS week_start,
            {week_end_expr} AS week_end
        FROM article_summary asum
        JOIN articles a ON a.id = asum.article_id
        WHERE a.gics_sector IS NOT NULL AND a.gics_sub_industry IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM sector_summary ss
              WHERE ss.gics_sector = a.gics_sector
                AND ss.gics_sub_industry = a.gics_sub_industry
                AND ss.week_start = {week_start_expr}
          )
        GROUP BY a.gics_sector, a.gics_sub_industry, week_start
        HAVING week_end < date('now')
        ORDER BY week_start, a.gics_sector, a.gics_sub_industry
    """  # noqa: S608
    params: list = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def fetch_company_summaries_for_sector_week(
    conn: sqlite3.Connection, gics_sector: str, gics_sub_industry: str, week_start: str
) -> list[sqlite3.Row]:
    """Return the article_summary rows (with company/ticker) contributing to
    one (gics_sector, gics_sub_industry, week_start) sector_summary."""
    date_col = "COALESCE(a.pub_date, a.fetched_at)"
    week_start_expr = _WEEK_START_EXPR.format(col=date_col)
    # S608: week_start_expr comes from the hardcoded _WEEK_START_EXPR
    # template, not caller input; gics_sector/sub_industry/week_start below
    # are bound as query params.
    sql = f"""
        SELECT asum.article_id, asum.summary_text, a.ticker, a.company
        FROM article_summary asum
        JOIN articles a ON a.id = asum.article_id
        WHERE a.gics_sector = ? AND a.gics_sub_industry = ?
          AND {week_start_expr} = ?
        ORDER BY a.company, asum.article_id
    """  # noqa: S608
    return conn.execute(sql, (gics_sector, gics_sub_industry, week_start)).fetchall()


def build_sector_summary_input(
    gics_sector: str, gics_sub_industry: str, rows: list[sqlite3.Row]
) -> str:
    """Assemble the sector-level reduce input: one METADATA header for the
    whole group (sector/subsector is constant across it) followed by each
    contributing company's c_summary -- a deliberate deviation from
    query.sql's per-row header repetition, agreed with the user."""
    lines = [f"{r['ticker']} ({r['company']}): {r['summary_text']}" for r in rows]
    return (
        f"METADATA:\nSector-{gics_sector}\nSubsector-{gics_sub_industry}\n\n"
        f"COMPANY SUMMARIES:\n" + "\n".join(lines)
    )


def write_sector_summary(
    conn: sqlite3.Connection,
    gics_sector: str,
    gics_sub_industry: str,
    week_start: str,
    week_end: str,
    summary_text: str,
    num_articles: int,
    num_companies: int,
    model_name: str,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO sector_summary
           (gics_sector, gics_sub_industry, week_start, week_end, summary_text,
            num_articles, num_companies, model_name, processed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            gics_sector,
            gics_sub_industry,
            week_start,
            week_end,
            summary_text,
            num_articles,
            num_companies,
            model_name,
            now_iso(),
        ),
    )


def list_sector_summaries(
    conn: sqlite3.Connection,
    sector: str | None = None,
    sub_industry: str | None = None,
    week_start: str | None = None,
) -> list[dict]:
    sql = "SELECT * FROM sector_summary WHERE 1=1"
    params: list = []
    if sector:
        sql += " AND gics_sector = ?"
        params.append(sector)
    if sub_industry:
        sql += " AND gics_sub_industry = ?"
        params.append(sub_industry)
    if week_start:
        sql += " AND week_start = ?"
        params.append(week_start)
    sql += " ORDER BY week_start DESC, gics_sector, gics_sub_industry"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


_SENTIMENT_STATS_GROUP_EXPR = {
    "company": "a.company",
    "year": "strftime('%Y', a.pub_date)",
    "month": "strftime('%Y-%m', a.pub_date)",
}


def list_articles(
    conn: sqlite3.Connection,
    company: str | None = None,
    ticker: str | None = None,
    sentiment: str | None = None,
    category: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    sql = """
        SELECT a.id, a.company, a.ticker, a.title, a.pub_date,
               s.label AS sentiment_label, s.score AS sentiment_score,
               c.label AS category_label,
               (SELECT COUNT(*) FROM article_entities e WHERE e.article_id = a.id) AS entity_count
        FROM articles a
        LEFT JOIN article_sentiment s ON s.article_id = a.id
        LEFT JOIN article_category c ON c.article_id = a.id
        WHERE 1=1
    """
    params: list = []
    if company:
        sql += " AND a.company = ?"
        params.append(company)
    if ticker:
        sql += " AND a.ticker = ?"
        params.append(ticker)
    if sentiment:
        sql += " AND s.label = ?"
        params.append(sentiment)
    if category:
        sql += " AND c.label = ?"
        params.append(category)
    if date_from:
        sql += " AND a.pub_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND a.pub_date <= ?"
        params.append(date_to)
    sql += " ORDER BY a.pub_date DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_article_detail(conn: sqlite3.Connection, article_id: int) -> dict | None:
    article = conn.execute(
        """SELECT id, company, ticker, title, author, pub_date, word_count, source_domain
           FROM articles WHERE id = ?""",
        (article_id,),
    ).fetchone()
    if article is None:
        return None

    sentiment_row = conn.execute(
        """SELECT label, score, positive, negative, neutral, model_name
           FROM article_sentiment WHERE article_id = ?""",
        (article_id,),
    ).fetchone()

    entity_rows = conn.execute(
        """SELECT id, entity_type, text, start_char, end_char, score
           FROM article_entities WHERE article_id = ? ORDER BY start_char""",
        (article_id,),
    ).fetchall()

    summary_row = conn.execute(
        """SELECT summary_text, num_chunks, model_name
           FROM article_summary WHERE article_id = ?""",
        (article_id,),
    ).fetchone()

    category_row = conn.execute(
        """SELECT label, score, earnings_performance, mergers_acquisitions, leadership_governance,
                  legal_regulatory, product_innovation, capital_shareholder_returns,
                  labor_human_capital, market_analyst_sentiment, partnerships_business_dev, model_name
           FROM article_category WHERE article_id = ?""",
        (article_id,),
    ).fetchone()

    return {
        **dict(article),
        "sentiment": dict(sentiment_row) if sentiment_row else None,
        "entities": [dict(r) for r in entity_rows],
        "summary": dict(summary_row) if summary_row else None,
        "category": dict(category_row) if category_row else None,
    }


def sentiment_stats(
    conn: sqlite3.Connection,
    company: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    group_by: str | None = None,
) -> list[dict]:
    # S608: `select_group` only ever comes from the fixed
    # _SENTIMENT_STATS_GROUP_EXPR.get(...) lookup or the hardcoded fallback
    # literal below -- never from `group_by` directly.
    group_expr = _SENTIMENT_STATS_GROUP_EXPR.get(group_by) if group_by else None
    select_group = f"{group_expr} AS group_key," if group_expr else "NULL AS group_key,"
    sql = f"""
        SELECT {select_group}
               SUM(CASE WHEN s.label = 'positive' THEN 1 ELSE 0 END) AS positive,
               SUM(CASE WHEN s.label = 'negative' THEN 1 ELSE 0 END) AS negative,
               SUM(CASE WHEN s.label = 'neutral' THEN 1 ELSE 0 END) AS neutral,
               COUNT(*) AS total
        FROM article_sentiment s
        JOIN articles a ON a.id = s.article_id
        WHERE 1=1
    """  # noqa: S608
    params: list = []
    if company:
        sql += " AND a.company = ?"
        params.append(company)
    if date_from:
        sql += " AND a.pub_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND a.pub_date <= ?"
        params.append(date_to)
    if group_expr:
        sql += f" GROUP BY {group_expr} ORDER BY group_key"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def entity_stats(
    conn: sqlite3.Connection,
    company: str | None = None,
    entity_type: str | None = None,
    top: int = 20,
) -> list[dict]:
    sql = """
        SELECT e.text, e.entity_type, COUNT(*) AS count
        FROM article_entities e
        JOIN articles a ON a.id = e.article_id
        WHERE 1=1
    """
    params: list = []
    if company:
        sql += " AND a.company = ?"
        params.append(company)
    if entity_type:
        sql += " AND e.entity_type = ?"
        params.append(entity_type)
    sql += " GROUP BY e.text, e.entity_type ORDER BY count DESC LIMIT ?"
    params.append(top)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def category_stats(
    conn: sqlite3.Connection,
    company: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Per-label article counts. Unlike sentiment_stats' fixed 3-way pivot
    (justified there by sentiment's permanently-fixed 3-class contract), 10
    label values read better as label/count rows -- same shape as
    entity_stats."""
    sql = """
        SELECT c.label, COUNT(*) AS count
        FROM article_category c
        JOIN articles a ON a.id = c.article_id
        WHERE 1=1
    """
    params: list = []
    if company:
        sql += " AND a.company = ?"
        params.append(company)
    if date_from:
        sql += " AND a.pub_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND a.pub_date <= ?"
        params.append(date_to)
    sql += " GROUP BY c.label ORDER BY count DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]
