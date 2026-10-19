"""The sitemap cache of one domain, in one process (T-070a, Work item 15).

A discovery run used to download the whole sitemap once per ticker and keep a row for every pair,
rejected or not. The cache crawls a domain's sitemap once, stores its in-range entries in
`sitemap_entries` (schema version 4) and lets every ticker read them. What it decides, and why, is
in `PLAN.md` Work item 15 ("T-068 result", "Implementation rules"):

- **Freshness:** the latest complete crawl is used while its `started_at` is at most 7 days old,
  whatever the run's end; the age is judged once, in `begin`, never in the middle of a run.
- **Refresh:** when there is no complete crawl, the roots or `CACHE_VERSION` changed (the
  `signature`), the run starts earlier than the crawl covers, the crawl is too old, or it is forced.
- **Complete:** every root and child fetched and parsed. A refresh upserts with
  `last_seen = the crawl's started_at`, never replaces, and never narrows the start; an incomplete
  crawl changes nothing but its `sitemap_crawls` row.
- **A failed refresh** (a) uses a fresh cache that covers the run, (b) uses an older one with a
  warning, (c) otherwise fails the pair, which is then not checkpointed.
- **Retention:** a complete refresh deletes entries not seen for 90 days and crawl rows older than
  that, except the latest complete one.

The guard is an `asyncio.Lock`: tickers and API requests of one process wait for one crawl. The
cross-process guard is T-070b; matching from the cache with a token index is T-071.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

from data_mining.db import connect
from news_collector.connectors.base import BaseConnector
from news_collector.models import DateRange, SitemapEntry
from news_collector.strategies.sitemap import crawl_sitemaps, summarize_failures
from news_collector.utils.http import fetch_text
from news_collector.utils.url import is_valid_http_url, normalize_url

log = logging.getLogger(__name__)

# Bumped when `is_article_url` or the URL normalization changes: the cached rows were filtered by
# the old rule. A test pins the result of both on a dozen URLs and fails on a change without a bump.
CACHE_VERSION = 1
FRESH_DAYS = 7
# After a failed refresh the process does not try again for this long unless the call is forced.
RETRY_AFTER = timedelta(minutes=5)
PURGE_DAYS = 90
SIGNATURE_LENGTH = 16
# A crawl keeps every entry from its start on: the run's end is applied when the rows are read.
OPEN_END = date.max

Row = tuple[str, str | None, str | None]  # normalized url, lastmod (ISO date), title


def utc_now() -> datetime:
    """Naive UTC, the form of every timestamp the cache tables hold."""
    return datetime.now(UTC).replace(tzinfo=None)


def stamp(moment: datetime) -> str:
    """UTC ISO-8601 with microseconds and no offset: sorts as text, one crawl per key."""
    return moment.isoformat(timespec="microseconds")


def signature(roots: list[str]) -> str:
    """What a crawl must match to be reused: the sorted roots and the cache version."""
    text = json.dumps([sorted(roots), CACHE_VERSION])
    return hashlib.sha256(text.encode()).hexdigest()[:SIGNATURE_LENGTH]


@dataclass(frozen=True)
class Crawl:
    """The latest complete crawl of a domain."""

    started_at: str
    range_start: date
    signature: str
    entries: int

    def age(self, now: datetime) -> timedelta:
        return now - datetime.fromisoformat(self.started_at)


@dataclass(frozen=True)
class SitemapRun:
    """What `begin` decided for one run: the entries to read, or why the pairs must fail.

    It travels with the run (the orchestrator, one API request), not on the shared cache, so a
    run that cannot use the cache never changes what another run reads. `rows` is parsed once
    per crawl and shared by every run that reads that crawl; nobody mutates it.
    """

    domain: str
    crawl: Crawl | None
    failures: tuple[str, ...] = ()
    rows: tuple[SitemapEntry, ...] = ()
    # The run went on with an older cache because the refresh failed (cases a and b): not an error,
    # the pairs are checkpointed, but whoever runs the discovery is told (T-070a).
    warnings: tuple[str, ...] = ()

    def entries(self, date_range: DateRange) -> tuple[list[SitemapEntry], list[str]]:
        """The current entries within the range and the failures the pair must report.

        A range that starts before the crawl covers is a failure, never a narrower match
        recorded as checked (case c).
        """
        if self.crawl is None:
            return [], list(self.failures)
        if date_range.start < self.crawl.range_start:
            return [], [f"sitemap cache of {self.domain} starts at {self.crawl.range_start}"]
        start, end = date_range.start, date_range.end
        return [e for e in self.rows if e.lastmod is None or start <= e.lastmod <= end], []


async def _no_client(_url: str) -> str:
    raise RuntimeError("no HTTP client")


class SitemapCache:
    """The cache of one domain: refreshes it when needed and serves its current entries."""

    def __init__(
        self,
        domain: str,
        roots: list[str],
        db_path: str,
        fetch: Callable[[str], Awaitable[str]],
        is_article_url: Callable[[str], bool],
        clock: Callable[[], datetime] = utc_now,
        retry_after: timedelta = RETRY_AFTER,
    ) -> None:
        self.domain = domain
        self._roots = list(roots)
        self._db_path = db_path
        self._fetch = fetch
        self._is_article_url = is_article_url
        self._clock = clock
        self._retry_after = retry_after
        self._failed_at: datetime | None = None  # the last failed refresh, for the back-off
        self._last_failures: list[str] = []
        self._signature = signature(roots)
        # an asyncio.Lock binds to the loop that first waits on it, and the cache lives as long as
        # the process: it gets a new lock when the running loop is another one
        self._lock = asyncio.Lock()
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._rows: tuple[SitemapEntry, ...] = ()
        self._rows_of: str | None = None  # `started_at` of the crawl `_rows` was read from
        self.crawls = 0  # crawls started by this object (tests, the log)

    @classmethod
    def for_connector(
        cls, connector: BaseConnector, client: httpx.AsyncClient, db_path: str
    ) -> SitemapCache:
        cache = cls(
            connector.config.domain,
            connector.config.sitemap_roots,
            db_path,
            _no_client,
            connector.is_article_url,
        )
        cache.use_client(client)
        return cache

    # ------------------------------------------------------------------
    # A run
    # ------------------------------------------------------------------

    def use_client(self, client: httpx.AsyncClient) -> None:
        """Fetch with this client from now on (the process's client may be replaced)."""

        async def fetch(url: str) -> str:
            return await fetch_text(client, url)

        self._fetch = fetch

    async def begin(self, date_range: DateRange, *, force: bool = False) -> SitemapRun:
        """Decide, once for a run, whether the cache is usable, refreshing it when it must be.

        Concurrent callers wait on the lock and find the crawl the first one made; a forced call
        is satisfied by any crawl that started after the call was made, so two forced requests
        make one crawl. It never raises: a database error is a failure every pair of the run
        reports (and is not checkpointed), so one domain cannot stop the others.
        """
        requested = self._clock()
        loop = asyncio.get_running_loop()
        if self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        try:
            async with self._lock:
                now = self._clock()
                latest = await asyncio.to_thread(self._latest_complete)
                refresh_failures: list[str] = []
                if self._must_refresh(latest, date_range.start, force, requested, now):
                    if self._backing_off(force, now):
                        refresh_failures = list(self._last_failures)  # the outage is still on
                    else:
                        refresh_failures = await self._refresh(date_range.start, latest)
                        self._failed_at = now if refresh_failures else None
                        self._last_failures = refresh_failures
                    latest = await asyncio.to_thread(self._latest_complete)
                return await self._run_for(latest, date_range, refresh_failures, now)
        except Exception as exc:
            log.warning("sitemap cache of %s unusable: %s", self.domain, exc)
            return SitemapRun(
                self.domain, None, (f"sitemap cache of {self.domain} unusable: {exc}",)
            )

    def _backing_off(self, force: bool, now: datetime) -> bool:
        """A refresh failed less than `retry_after` ago: do not crawl again (a forced call does)."""
        return (
            not force and self._failed_at is not None and now - self._failed_at < self._retry_after
        )

    def _must_refresh(
        self, latest: Crawl | None, start: date, force: bool, requested: datetime, now: datetime
    ) -> bool:
        if latest is None or latest.signature != self._signature or start < latest.range_start:
            return True
        if latest.age(now) > timedelta(days=FRESH_DAYS):
            return True
        return force and latest.started_at < stamp(requested)

    async def _run_for(
        self,
        latest: Crawl | None,
        date_range: DateRange,
        refresh_failures: list[str],
        now: datetime,
    ) -> SitemapRun:
        usable = (
            latest is not None
            and latest.signature == self._signature
            and latest.range_start <= date_range.start
        )
        if not usable or latest is None:
            reason = summarize_failures(refresh_failures) if refresh_failures else "no refresh ran"
            return SitemapRun(
                self.domain,
                None,
                (
                    f"sitemap cache of {self.domain} does not cover {date_range.start}: "
                    f"the refresh failed ({reason}) and no complete crawl matches",
                ),
            )
        warnings: tuple[str, ...] = ()
        if refresh_failures:
            age = latest.age(now)
            warning = (
                f"sitemap refresh of {self.domain} failed ({summarize_failures(refresh_failures)}); "
                f"using the crawl of {latest.started_at}, {age.total_seconds() / 86400:.1f} days old"
            )
            log.warning(warning)
            warnings = (warning,)
        if self._rows_of != latest.started_at:
            # the other process refreshed (or the first run): rebuild from the table
            self._rows = await asyncio.to_thread(self._load, latest.started_at)
            self._rows_of = latest.started_at
            log.info(
                "sitemap cache of %s: %d current entries from the crawl of %s",
                self.domain,
                len(self._rows),
                latest.started_at,
            )
        return SitemapRun(self.domain, latest, (), self._rows, warnings)

    # ------------------------------------------------------------------
    # The crawl
    # ------------------------------------------------------------------

    async def _refresh(self, start: date, latest: Crawl | None) -> list[str]:
        """Crawl and, when complete, upsert. Returns the failures (empty = complete)."""
        began = self._clock()
        # a refresh never narrows, but only a crawl made under the same rules is a basis
        same_rules = latest is not None and latest.signature == self._signature
        crawl_start = min(start, latest.range_start) if latest and same_rules else start
        self.crawls += 1
        log.info("sitemap crawl of %s from %s", self.domain, crawl_start)
        try:
            entries, failures = await crawl_sitemaps(
                self._fetch, self._roots, DateRange(start=crawl_start, end=OPEN_END)
            )
            rows = self._rows_from(entries) if not failures else {}
            await asyncio.to_thread(
                self._write, stamp(began), crawl_start, rows, len(entries), len(failures)
            )
        except Exception as exc:
            log.warning("sitemap refresh of %s raised: %s", self.domain, exc)
            return [f"sitemap refresh raised: {exc}"]
        log.info(
            "sitemap crawl of %s: %d entries stored, %d failure(s)",
            self.domain,
            len(rows),
            len(failures),
        )
        return failures

    def _rows_from(self, entries: list[SitemapEntry]) -> dict[str, Row]:
        """What is stored: valid, normalized, article URLs; the first entry of a URL wins."""
        rows: dict[str, Row] = {}
        for entry in entries:
            if not is_valid_http_url(entry.url):
                continue
            url = normalize_url(entry.url)
            if url in rows or not self._is_article_url(url):
                continue
            rows[url] = (url, entry.lastmod.isoformat() if entry.lastmod else None, entry.title)
        return rows

    # ------------------------------------------------------------------
    # Database (every call runs in a worker thread, on its own connection)
    # ------------------------------------------------------------------

    def _latest_complete(self) -> Crawl | None:
        db = connect(self._db_path)
        try:
            row = db.execute(
                "SELECT started_at, range_start, signature, entries FROM sitemap_crawls "
                "WHERE domain = ? AND complete = 1 ORDER BY finished_at DESC LIMIT 1",
                (self.domain,),
            ).fetchone()
        finally:
            db.close()
        if row is None:
            return None
        return Crawl(row[0], date.fromisoformat(row[1]), row[2], row[3] or 0)

    def _load(self, started_at: str) -> tuple[SitemapEntry, ...]:
        db = connect(self._db_path)
        try:
            rows = db.execute(
                "SELECT url, lastmod, title FROM sitemap_entries "
                "WHERE domain = ? AND last_seen = ? ORDER BY url",
                (self.domain, started_at),
            ).fetchall()
        finally:
            db.close()
        return tuple(
            SitemapEntry(url=r[0], lastmod=date.fromisoformat(r[1]) if r[1] else None, title=r[2])
            for r in rows
        )

    def _write(
        self, started_at: str, range_start: date, rows: dict[str, Row], seen: int, failures: int
    ) -> None:
        """One transaction: the crawl row, and for a complete crawl the upsert and the purge."""
        complete = failures == 0
        db = connect(self._db_path, wal=True)
        try:
            with db.transaction():
                if complete:
                    db.executemany(
                        "INSERT INTO sitemap_entries (domain, url, lastmod, title, first_seen, last_seen) "
                        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (domain, url) DO UPDATE SET "
                        "lastmod = excluded.lastmod, title = excluded.title, "
                        "last_seen = excluded.last_seen",
                        (
                            (self.domain, url, lastmod, title, started_at, started_at)
                            for url, lastmod, title in rows.values()
                        ),
                    )
                db.execute(
                    "INSERT INTO sitemap_crawls (domain, started_at, finished_at, complete, "
                    "range_start, signature, entries, failures) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.domain,
                        started_at,
                        stamp(self._clock()),
                        int(complete),
                        range_start.isoformat(),
                        self._signature,
                        len(rows) if complete else seen,
                        failures,
                    ),
                )
                cutoff = stamp(datetime.fromisoformat(started_at) - timedelta(days=PURGE_DAYS))
                if complete:
                    db.execute(
                        "DELETE FROM sitemap_entries WHERE domain = ? AND last_seen < ?",
                        (self.domain, cutoff),
                    )
                # crawl rows, complete or not: 90 days of history and the latest complete row
                db.execute(
                    "DELETE FROM sitemap_crawls WHERE domain = ? AND started_at < ? AND NOT "
                    "(complete = 1 AND started_at = (SELECT started_at FROM sitemap_crawls "
                    "WHERE domain = ? AND complete = 1 ORDER BY finished_at DESC LIMIT 1))",
                    (self.domain, cutoff, self.domain),
                )
        finally:
            db.close()


# One cache per (database, domain) in the process, so the orchestrator and every API request share
# one lock, one crawl and one set of rows. `begin` re-reads the table on each run, so the
# long-lived gateway sees a refresh made by another process. The client is the caller's: a
# replaced client is used from the next call, and nothing is keyed by it.
_CACHES: dict[tuple[str, str], SitemapCache] = {}


def shared_cache(
    connector: BaseConnector, client: httpx.AsyncClient, db_path: str
) -> SitemapCache | None:
    """The process's cache of the connector's domain, or None when it has no sitemap roots."""
    if not connector.config.sitemap_roots:
        return None
    key = (db_path, connector.config.domain)
    if key not in _CACHES:
        _CACHES[key] = SitemapCache.for_connector(connector, client, db_path)
    _CACHES[key].use_client(client)
    return _CACHES[key]


def reset_shared_caches() -> None:
    """Forget every shared cache (tests)."""
    _CACHES.clear()
