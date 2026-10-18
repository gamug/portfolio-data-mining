"""SQLite-backed URL queue for checkpointing and resume."""

from __future__ import annotations

import contextlib
import csv
import logging
import sqlite3
from collections.abc import Iterator, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple, TypedDict

from portfolio_common.db import Allowlist, Database, Row, in_clause

from data_mining.db import connect
from data_mining.matching import CLASS_RANK
from data_mining.quarantine import release_articles
from data_mining.schema import apply_schema, run_migrations
from news_collector.models import DiscoveredURL


class Promotion(NamedTuple):
    """What one `enqueue_batch` call promoted: rows whose class rose, and how many of
    those were matcher-rejected rows put back to `pending`."""

    promoted: int = 0
    lifted: int = 0


class QueueStats(TypedDict):
    total: int
    by_status: dict[str, int]
    by_domain: dict[str, int]
    by_match_class: dict[str, int]


log = logging.getLogger(__name__)

_HTTP_SUCCESS_MIN = 200
_HTTP_SUCCESS_MAX = 300  # exclusive -- half-open [200, 300) range


# `(url, ticker) IN (...)` binds two variables per pair and SQLite caps the variables of one
# statement (32,766 on 3.40.1, 999 before 3.32), so the lookups below run in chunks. 5,000
# pairs is 10,000 variables, well under the cap, and large enough that a batch is a few queries.
_LOOKUP_CHUNK_PAIRS = 5_000


def _chunks[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _is_success_status(status_code: int) -> bool:
    return _HTTP_SUCCESS_MIN <= status_code < _HTTP_SUCCESS_MAX


# Columns query() is allowed to ORDER BY — interpolated directly into SQL,
# so this must stay a closed allowlist rather than accepting arbitrary input.
_QUERY_ORDER_COLUMNS = Allowlist(
    "id",
    "url",
    "domain",
    "company",
    "ticker",
    "source",
    "discovered_at",
    "pub_date",
    "status",
    "match_class",
)


class URLQueue:
    """
    Persistent URL queue backed by SQLite.

    Thread-safe for single-process async use (writes serialized through
    synchronous connection; use in executor if needed).

    The UNIQUE(url, ticker) constraint ensures deduplication at insert time.
    """

    def __init__(self, db_path: str | Path) -> None:
        # `str(None)` is "None": a caller that forgot to resolve its path would create a
        # file named `None` (T-065). Callers resolve with `data_mining.resolve_db_path`.
        if db_path is None or str(db_path).strip() in ("", "."):  # Path("") is "."
            raise ValueError("URLQueue needs a database path (use data_mining.resolve_db_path)")
        self._db_path = str(db_path)
        self._conn: Database | None = None
        # Read by the caller right after `enqueue_batch` (it has no await in it).
        self.last_promotion = Promotion()
        # Newly inserted matcher-rejected rows of the last `enqueue_batch` call.
        self.last_inserted_rejected = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Create tables and indexes if they do not already exist."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # WAL (persistent, set here once at the file level) + busy_timeout +
        # row_factory, all from the shared factory. This is the writer path;
        # extractor opens the same file with foreign_keys=ON instead.
        self._conn = connect(self._db_path, wal=True, check_same_thread=False)
        apply_schema(self._conn)
        run_migrations(self._conn)
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

        Duplicates (same url + ticker) are not inserted again, but they are not
        simply dropped either: a rediscovery can carry better evidence, so the stored
        row is *promoted* (never demoted): it keeps the higher `match_class`
        (`reject` < `review` < `accept`), takes the new `match_reason` with a higher
        class, fills a null `match_reason`/`query`, and a row first stored as `reject`
        (`status = 'skipped'`) goes back to `pending` once its class rises above
        `reject`. A `reject` row is written `status = 'skipped'` at insert, its
        `title` untouched, so it never reaches the fetch selection (`status='pending'`).

        As a side effect, sets `.id` on each element of `urls` in place to the
        primary key of its `discovered_urls` row (whether newly inserted or a
        pre-existing duplicate) — this is the one place a caller can cheaply
        learn the id of what it just tried to insert, which downstream stages
        need as their foreign key back to this table.

        Raises:
            ValueError: a `match_class` is not `accept`/`review`/`reject` (nothing is written).

        After the call `last_promotion` says how many stored rows were promoted and how
        many of those were lifted from `skipped` to `pending`, and
        `last_inserted_rejected` how many of the newly inserted rows are matcher-rejected
        (part of the returned count, never fetched).

        Returns:
            Number of newly inserted rows.
        """
        try:
            return self._enqueue_batch(urls)
        except sqlite3.Error:
            # The caller reports the error text only; the batch size is what says whether it is
            # the statement-size limit or something else. The rows of the batch may be stored
            # without ids or promotion if the failure came after the insert: retrying is safe
            # (INSERT OR IGNORE) and redoes the promotion.
            log.error("enqueue_batch failed for a batch of %d urls", len(urls), exc_info=True)
            raise

    def _enqueue_batch(self, urls: Sequence[DiscoveredURL]) -> int:
        assert self._conn is not None, "Call initialize() first"
        self.last_promotion = Promotion()
        self.last_inserted_rejected = 0
        if not urls:
            return 0
        for u in urls:
            if u.match_class not in CLASS_RANK:
                raise ValueError(f"invalid match_class {u.match_class!r} for {u.url}")
        new_rejected = self._count_new_rejected(urls)

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
                "skipped" if u.match_class == "reject" else u.status,
                u.fetch_status_code,
                u.match_class,
                u.match_reason,
                u.query,
            )
            for u in urls
        ]

        cursor = self._conn.executemany(
            self._conn.dialect.insert_or_ignore(
                "discovered_urls",
                (
                    "url",
                    "domain",
                    "company",
                    "ticker",
                    "source",
                    "discovered_at",
                    "pub_date",
                    "title",
                    "status",
                    "fetch_status_code",
                    "match_class",
                    "match_reason",
                    "query",
                ),
            ),
            rows,
        )
        self._conn.commit()
        # cursor.rowcount sums modifications across all executemany
        # iterations (unlike `SELECT changes()`, which only reflects the last
        # statement executed), so it correctly counts total rows inserted.
        inserted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        log.debug("enqueue_batch: inserted=%d / submitted=%d", inserted, len(urls))
        self.last_inserted_rejected = min(new_rejected, inserted)

        # Look ids back up by (url, ticker) rather than relying on lastrowid —
        # executemany + INSERT OR IGNORE means lastrowid only reflects the final
        # statement, and ignored duplicates need their *existing* row's id too.
        # One in_clause() group per (url, ticker) pair -- in_clause() itself
        # only sizes a flat "(?,?,...)" group, so this row-value "(a,b) IN
        # (...)" shape is built by joining one per-row group.
        id_rows: list[Row] = []
        for chunk in _chunks(urls, _LOOKUP_CHUNK_PAIRS):
            placeholders = ",".join(in_clause((u.url, u.ticker)) for u in chunk)
            # S608: `placeholders` is just a run of in_clause()-built "(?,?)"
            # groups -- values are bound as query params below, never
            # interpolated into the SQL text.
            id_rows += self._conn.execute(
                "SELECT id, url, ticker, status, match_class, match_reason, query "  # noqa: S608
                f"FROM discovered_urls WHERE (url, ticker) IN ({placeholders})",
                [v for u in chunk for v in (u.url, u.ticker)],
            ).fetchall()
        stored = {(r["url"], r["ticker"]): dict(r) for r in id_rows}
        for u in urls:
            row = stored.get((u.url, u.ticker))
            u.id = row["id"] if row else None
        self.last_promotion = self._promote_duplicates(urls, stored)
        return inserted

    def _count_new_rejected(self, urls: Sequence[DiscoveredURL]) -> int:
        """Matcher-rejected pairs of the batch that are not stored yet (read before the
        insert, since `INSERT OR IGNORE` does not say which rows it skipped)."""
        assert self._conn is not None
        rejected = sorted({(u.url, u.ticker) for u in urls if u.match_class == "reject"})
        stored = 0
        for chunk in _chunks(rejected, _LOOKUP_CHUNK_PAIRS):
            placeholders = ",".join(in_clause(pair) for pair in chunk)
            # S608: `placeholders` is a run of in_clause()-built "(?,?)" groups; values are bound.
            stored += len(
                self._conn.execute(
                    f"SELECT url, ticker FROM discovered_urls WHERE (url, ticker) IN ({placeholders})",  # noqa: S608
                    [v for pair in chunk for v in pair],
                ).fetchall()
            )
        return len(rejected) - stored

    def _promote_duplicates(
        self, urls: Sequence[DiscoveredURL], stored: dict[tuple[str, str], dict]
    ) -> Promotion:
        """Promote stored rows from the batch's evidence (see `enqueue_batch`).

        `stored` holds each pair's row as it was read after the insert; it is updated
        as the batch is walked, so two entries for one pair in one batch compose. The
        UPDATE is conditional in SQL, because another process (the extractor's
        `mark_status`) may have changed the row since the read: `status` is written
        only to lift a matcher-rejected row (still `skipped` and `reject` in the
        database), never copied from the read, and the write is skipped when the stored
        class is no longer the one that was read (a concurrent promotion wins). The counts
        returned are of writes that happened (`rowcount == 1`), and a row counts as lifted
        only if its status really is `pending` afterwards. A promoted row's article, if it has
        one and was quarantined, is released (`data_mining.quarantine`).
        """
        assert self._conn is not None
        wrote = False
        promoted = lifted = 0
        raised_ids: list[int] = []
        for u in urls:
            row = stored.get((u.url, u.ticker))
            if row is None:
                continue
            raised = CLASS_RANK[u.match_class] > CLASS_RANK[row["match_class"]]
            match_class = u.match_class if raised else row["match_class"]
            reason = u.match_reason if raised else row["match_reason"] or u.match_reason
            query = row["query"] or u.query
            # only a matcher-rejected row goes back to pending; a row skipped for
            # another reason (paywall) keeps its status
            lift = raised and row["match_class"] == "reject" and row["status"] == "skipped"
            if (match_class, reason, query) == (
                row["match_class"],
                row["match_reason"],
                row["query"],
            ):
                continue
            # One UPDATE per changed row (a small share of a batch) so the counts report
            # only writes that happened: the guard may have skipped this one.
            cursor = self._conn.execute(
                "UPDATE discovered_urls SET match_class=?, match_reason=?, query=?,"
                " status=CASE WHEN ?=1 AND status='skipped' AND match_class='reject'"
                " THEN 'pending' ELSE status END"
                " WHERE id=? AND match_class=?",
                (match_class, reason, query, int(lift), row["id"], row["match_class"]),
            )
            if cursor.rowcount != 1:
                continue
            wrote = True
            promoted += raised
            if raised:
                raised_ids.append(row["id"])
            row.update(match_class=match_class, match_reason=reason, query=query)
            if lift:
                now = self._conn.execute(
                    "SELECT status FROM discovered_urls WHERE id=?", (row["id"],)
                ).fetchone()
                row["status"] = now["status"]
                lifted += now["status"] == "pending"
        if wrote:
            self._conn.commit()
        # A fetched pair whose article was quarantined (T-057) reads `ok` again once promoted:
        # nothing re-fetches it, so nothing else would release it. A no-op for unfetched rows.
        # One statement and commit per promoted id: promotions are a small share of a batch (the
        # UPDATE loop above is per row too). Batch with in_clause() if a rediscovery ever promotes thousands.
        for url_id in raised_ids:
            release_articles(self._conn, url_id)
        if promoted:
            log.info("enqueue_batch: promoted=%d (lifted from skipped=%d)", promoted, lifted)
        return Promotion(promoted, lifted)

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
        placeholders = in_clause(domains)
        # S608: `placeholders` is an in_clause()-built "(?,?,...)" group --
        # values are bound as query params below, never interpolated into
        # the SQL text.
        rows = self._conn.execute(
            f"SELECT ticker, domain FROM discovery_progress "  # noqa: S608
            f"WHERE domain IN {placeholders} AND start_date=? AND end_date=?",
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
            self._conn.dialect.upsert(
                "discovery_progress",
                ("ticker", "domain", "start_date", "end_date", "completed_at", "inserted_count"),
                conflict=("ticker", "domain", "start_date", "end_date"),
                update=["completed_at", "inserted_count"],
            ),
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
        match_class: str | None = None,
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
        order_column = _QUERY_ORDER_COLUMNS.check(order_by)

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
        if match_class:
            clauses.append("match_class=?")
            params.append(match_class)
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
        # interpolated); `order_column` is checked against the
        # _QUERY_ORDER_COLUMNS allowlist above and `direction` is one of two
        # hardcoded literals.
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        total = self._conn.execute(
            f"SELECT COUNT(*) FROM discovered_urls{where}",  # noqa: S608
            params,
        ).fetchone()[0]

        direction = "DESC" if descending else "ASC"
        page_query = (
            f"SELECT * FROM discovered_urls{where} "  # noqa: S608
            f"ORDER BY {order_column} {direction} LIMIT ? OFFSET ?"
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
        # `by_status['skipped']` mixes matcher-rejected rows with paywall/irrelevant
        # skips; `by_match_class` tells them apart.
        by_match_class = {
            row["match_class"]: row["cnt"]
            for row in self._conn.execute(
                "SELECT match_class, COUNT(*) AS cnt FROM discovered_urls GROUP BY match_class"
            ).fetchall()
        }
        return {
            "total": total,
            "by_status": by_status,
            "by_domain": by_domain,
            "by_match_class": by_match_class,
        }

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


def _row_to_discovered_url(row: Row) -> DiscoveredURL:
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
        match_class=row["match_class"],
        match_reason=row["match_reason"],
        query=row["query"],
        id=row["id"],
    )
