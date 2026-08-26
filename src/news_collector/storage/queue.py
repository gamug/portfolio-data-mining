"""SQLite-backed URL queue for checkpointing and resume."""

from __future__ import annotations

import contextlib
import csv
import logging
import os
import sqlite3
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import TypedDict

from common.db_backend import is_remote_url, open_connection
from news_collector.models import DiscoveredURL


class QueueStats(TypedDict):
    total: int
    by_status: dict[str, int]
    by_domain: dict[str, int]


log = logging.getLogger(__name__)

_HTTP_SUCCESS_MIN = 200
_HTTP_SUCCESS_MAX = 300  # exclusive -- half-open [200, 300) range


def _is_success_status(status_code: int) -> bool:
    return _HTTP_SUCCESS_MIN <= status_code < _HTTP_SUCCESS_MAX


_DDL = """
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

# Columns query() is allowed to ORDER BY — interpolated directly into SQL,
# so this must stay a closed whitelist rather than accepting arbitrary input.
_QUERY_ORDER_COLUMNS = frozenset(
    {"id", "url", "domain", "company", "ticker", "source", "discovered_at", "pub_date", "status"}
)


class URLQueue:
    """
    Persistent URL queue backed by SQLite.

    Thread-safe for single-process async use (writes serialized through
    synchronous sqlite3 connection; use in executor if needed).

    The UNIQUE(url, ticker) constraint ensures deduplication at insert time.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Create tables and indexes if they do not already exist.

        `self._db_path` doubles as $DATABASE_URL's raw value -- a
        `libsql://...` URL (with $TURSO_AUTH_TOKEN set) routes this through
        Turso instead of local SQLite. See common/db_backend.py.
        """
        remote = is_remote_url(self._db_path)
        if not remote:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = open_connection(
            self._db_path,
            auth_token=os.environ.get("TURSO_AUTH_TOKEN"),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        if not remote:
            # WAL/synchronous/busy_timeout are local-WAL-file concerns --
            # Turso rejects busy_timeout/journal_mode outright ("SQL not
            # allowed statement") and has no equivalent need for them since
            # it isn't a single-writer local file. See common/db_backend.py.
            #
            # Enable WAL for better concurrent read performance
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            # This DB file is shared with extractor and news_nlp (see
            # CLAUDE.md) -- each opens its own connection, and a writer that
            # finds the file locked by another connection's write
            # transaction otherwise gets an immediate
            # `sqlite3.OperationalError: database is locked` instead of a
            # retry. busy_timeout makes SQLite retry internally for up to
            # this many ms before raising. Not persistent (like
            # foreign_keys, unlike journal_mode), so every connection --
            # including this pipeline's own concurrent-reader connections --
            # needs to set it itself.
            self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_DDL)
        self._conn.commit()
        log.info("URLQueue initialized at %s", self._db_path)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def enqueue_batch(self, urls: Sequence[DiscoveredURL]) -> int:
        """
        Insert discovered URLs into the queue.

        Duplicates (same url + ticker) are silently ignored via INSERT OR IGNORE.

        As a side effect, sets `.id` on each element of `urls` in place to the
        primary key of its `discovered_urls` row (whether newly inserted or a
        pre-existing duplicate) — this is the one place a caller can cheaply
        learn the id of what it just tried to insert, which downstream stages
        need as their foreign key back to this table.

        Returns:
            Number of newly inserted rows.
        """
        assert self._conn is not None, "Call initialize() first"
        if not urls:
            return 0

        rows = [
            (
                u.url,
                u.domain,
                u.company,
                u.ticker,
                u.source,
                u.discovered_at.isoformat(),
                u.pub_date.isoformat() if u.pub_date else None,
                u.title,
                u.status,
                u.fetch_status_code,
            )
            for u in urls
        ]

        cursor = self._conn.cursor()
        cursor.executemany(
            """
            INSERT OR IGNORE INTO discovered_urls
                (url, domain, company, ticker, source, discovered_at,
                 pub_date, title, status, fetch_status_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._conn.commit()
        # sqlite3's cursor.rowcount sums modifications across all executemany
        # iterations (unlike `SELECT changes()`, which only reflects the last
        # statement executed), so it correctly counts total rows inserted.
        inserted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        log.debug("enqueue_batch: inserted=%d / submitted=%d", inserted, len(urls))

        # Look ids back up by (url, ticker) rather than relying on lastrowid —
        # executemany + INSERT OR IGNORE means lastrowid only reflects the final
        # statement, and ignored duplicates need their *existing* row's id too.
        placeholders = ",".join("(?,?)" for _ in urls)
        # S608: `placeholders` is just a run of literal "(?,?)" -- values are
        # bound as query params below, never interpolated into the SQL text.
        id_rows = self._conn.execute(
            f"SELECT id, url, ticker FROM discovered_urls WHERE (url, ticker) IN ({placeholders})",  # noqa: S608
            [v for u in urls for v in (u.url, u.ticker)],
        ).fetchall()
        id_by_key = {(r["url"], r["ticker"]): r["id"] for r in id_rows}
        for u in urls:
            u.id = id_by_key.get((u.url, u.ticker))
        return inserted

    def mark_fetched(self, url: str, status_code: int) -> None:
        """
        Update status for a URL after it has been fetched, matched by url string.

        NOTE: `url` alone is not unique in `discovered_urls` (only `(url, ticker)`
        is — the same article can be discovered under more than one ticker), so
        this updates every row sharing that url. Prefer mark_fetched_by_id() when
        the caller already has the row id (e.g. from get_pending()/query()), which
        is the intended way for downstream consumers to reference a specific row.
        """
        assert self._conn is not None
        new_status = "fetched" if _is_success_status(status_code) else "failed"
        self._conn.execute(
            "UPDATE discovered_urls SET status=?, fetch_status_code=? WHERE url=?",
            (new_status, status_code, url),
        )
        self._conn.commit()

    def mark_skipped(self, url: str, reason: str = "") -> None:
        """
        Mark URL as skipped (e.g. paywall, irrelevant after closer inspection),
        matched by url string. Same (url, ticker) caveat as mark_fetched() applies
        — prefer mark_skipped_by_id() when the row id is known.
        """
        assert self._conn is not None
        self._conn.execute(
            "UPDATE discovered_urls SET status='skipped', title=COALESCE(title, ?) WHERE url=?",
            (reason or None, url),
        )
        self._conn.commit()

    def mark_fetched_by_id(self, url_id: int, status_code: int) -> None:
        """
        Update status for a single `discovered_urls` row, referenced by its
        primary key `id`. This is the unambiguous counterpart to mark_fetched()
        — downstream consumers (e.g. the news crawler) that hold a foreign key
        to this table should use id-based updates rather than the url string.
        """
        assert self._conn is not None
        new_status = "fetched" if _is_success_status(status_code) else "failed"
        self._conn.execute(
            "UPDATE discovered_urls SET status=?, fetch_status_code=? WHERE id=?",
            (new_status, status_code, url_id),
        )
        self._conn.commit()

    def mark_skipped_by_id(self, url_id: int, reason: str = "") -> None:
        """Mark a single `discovered_urls` row skipped, referenced by its primary key `id`."""
        assert self._conn is not None
        self._conn.execute(
            "UPDATE discovered_urls SET status='skipped', title=COALESCE(title, ?) WHERE id=?",
            (reason or None, url_id),
        )
        self._conn.commit()

    def get_by_id(self, url_id: int) -> DiscoveredURL | None:
        """Fetch a single `discovered_urls` row by its primary key `id`, or None if not found."""
        assert self._conn is not None
        row = self._conn.execute("SELECT * FROM discovered_urls WHERE id=?", (url_id,)).fetchone()
        return _row_to_discovered_url(row) if row else None

    # ------------------------------------------------------------------
    # Checkpoint / resume
    #
    # Tracks which (ticker, domain) pairs have already finished discovery for
    # an exact date range, independent of discovered_urls' url-level dedup —
    # that only prevents duplicate rows, it doesn't skip re-running DDG/sitemap
    # work that already completed on a prior, interrupted run.
    # ------------------------------------------------------------------

    def completed_pairs(
        self, domains: Sequence[str], start_date: date, end_date: date
    ) -> set[tuple[str, str]]:
        """
        Return the (ticker, domain) pairs already marked complete for this exact
        date range, restricted to the given domains. Used by resume to filter the
        task matrix before re-running discovery.
        """
        assert self._conn is not None, "Call initialize() first"
        if not domains:
            return set()
        placeholders = ",".join("?" for _ in domains)
        # S608: `placeholders` is just a run of literal "?" -- values are
        # bound as query params below, never interpolated into the SQL text.
        rows = self._conn.execute(
            f"SELECT ticker, domain FROM discovery_progress "  # noqa: S608
            f"WHERE domain IN ({placeholders}) AND start_date=? AND end_date=?",
            [*domains, start_date.isoformat(), end_date.isoformat()],
        ).fetchall()
        return {(row["ticker"], row["domain"]) for row in rows}

    def mark_pair_completed(
        self,
        ticker: str,
        domain: str,
        start_date: date,
        end_date: date,
        inserted_count: int = 0,
    ) -> None:
        """Record that discovery finished for (ticker, domain) over this exact date range."""
        assert self._conn is not None, "Call initialize() first"
        self._conn.execute(
            """
            INSERT INTO discovery_progress
                (ticker, domain, start_date, end_date, completed_at, inserted_count)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (ticker, domain, start_date, end_date)
            DO UPDATE SET completed_at=excluded.completed_at, inserted_count=excluded.inserted_count
            """,
            (
                ticker,
                domain,
                start_date.isoformat(),
                end_date.isoformat(),
                datetime.utcnow().isoformat(),
                inserted_count,
            ),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_pending(
        self,
        domain: str | None = None,
        ticker: str | None = None,
        limit: int = 1000,
    ) -> list[DiscoveredURL]:
        """Return up to limit pending URLs, optionally filtered."""
        assert self._conn is not None
        query = "SELECT * FROM discovered_urls WHERE status='pending'"
        params: list = []
        if domain:
            query += " AND domain=?"
            params.append(domain)
        if ticker:
            query += " AND ticker=?"
            params.append(ticker)
        query += " ORDER BY discovered_at LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_discovered_url(r) for r in rows]

    def query(
        self,
        domain: str | None = None,
        ticker: str | None = None,
        company: str | None = None,
        status: str | None = None,
        source: str | None = None,
        url_contains: str | None = None,
        pub_date_from: date | None = None,
        pub_date_to: date | None = None,
        order_by: str = "discovered_at",
        descending: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[DiscoveredURL], int]:
        """
        Flexible read over the queue: any combination of filters, paginated.

        Unlike get_pending(), this is not restricted to status='pending' and
        supports substring/date-range/ordering filters for ad-hoc inspection.

        Returns:
            (rows for the requested page, total matching row count before pagination)
        """
        assert self._conn is not None, "Call initialize() first"
        if order_by not in _QUERY_ORDER_COLUMNS:
            raise ValueError(
                f"order_by must be one of {sorted(_QUERY_ORDER_COLUMNS)}, got {order_by!r}"
            )

        clauses: list[str] = []
        params: list = []
        if domain:
            clauses.append("domain=?")
            params.append(domain)
        if ticker:
            clauses.append("ticker=?")
            params.append(ticker)
        if company:
            clauses.append("company LIKE ?")
            params.append(f"%{company}%")
        if status:
            clauses.append("status=?")
            params.append(status)
        if source:
            clauses.append("source=?")
            params.append(source)
        if url_contains:
            clauses.append("url LIKE ?")
            params.append(f"%{url_contains}%")
        if pub_date_from:
            clauses.append("pub_date >= ?")
            params.append(pub_date_from.isoformat())
        if pub_date_to:
            clauses.append("pub_date <= ?")
            params.append(pub_date_to.isoformat())

        # S608: `where` is built only from the hardcoded "col=?"/"col LIKE ?"
        # fragments above (values are bound as query params, never
        # interpolated); `order_by` is checked against the _QUERY_ORDER_COLUMNS
        # allowlist above and `direction` is one of two hardcoded literals.
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        total = self._conn.execute(
            f"SELECT COUNT(*) FROM discovered_urls{where}",  # noqa: S608
            params,
        ).fetchone()[0]

        direction = "DESC" if descending else "ASC"
        page_query = (
            f"SELECT * FROM discovered_urls{where} "  # noqa: S608
            f"ORDER BY {order_by} {direction} LIMIT ? OFFSET ?"
        )
        rows = self._conn.execute(page_query, [*params, limit, offset]).fetchall()
        return [_row_to_discovered_url(r) for r in rows], total

    def stats(self) -> QueueStats:
        """Return summary counts for monitoring / CLI display."""
        assert self._conn is not None
        total = self._conn.execute("SELECT COUNT(*) FROM discovered_urls").fetchone()[0]
        by_status = {
            row["status"]: row["cnt"]
            for row in self._conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM discovered_urls GROUP BY status"
            ).fetchall()
        }
        by_domain = {
            row["domain"]: row["cnt"]
            for row in self._conn.execute(
                "SELECT domain, COUNT(*) AS cnt FROM discovered_urls GROUP BY domain"
            ).fetchall()
        }
        return {"total": total, "by_status": by_status, "by_domain": by_domain}

    def export_pending_csv(self, path: str | Path) -> int:
        """
        Write all pending URLs to a CSV file. Returns row count.

        `id` is the first column so the downstream fetcher/crawler that consumes
        this CSV can carry it as a foreign key back to this row (e.g. to report
        fetch status via mark_fetched_by_id()) instead of re-matching on url text.
        """
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT id, url, domain, company, ticker, source, pub_date, title "
            "FROM discovered_urls WHERE status='pending' ORDER BY ticker, domain"
        ).fetchall()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["id", "url", "domain", "company", "ticker", "source", "pub_date", "title"]
            )
            writer.writerows(rows)
        log.info("Exported %d pending URLs to %s", len(rows), path)
        return len(rows)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _row_to_discovered_url(row: sqlite3.Row) -> DiscoveredURL:
    pub_date: date | None = None
    if row["pub_date"]:
        with contextlib.suppress(ValueError):
            pub_date = date.fromisoformat(row["pub_date"])

    return DiscoveredURL(
        url=row["url"],
        domain=row["domain"],
        company=row["company"],
        ticker=row["ticker"],
        source=row["source"],  # type: ignore[arg-type]
        discovered_at=datetime.fromisoformat(row["discovered_at"]),
        pub_date=pub_date,
        title=row["title"],
        status=row["status"],  # type: ignore[arg-type]
        fetch_status_code=row["fetch_status_code"],
        id=row["id"],
    )
