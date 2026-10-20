"""Re-class the sitemap cache with the current matcher, with no network (T-072, Work item 15).

`T-071` stops storing the sitemap pairs the matcher rejects, so a later matcher change cannot
promote what was never stored. This is the way back: it reads the **current** entries of a
domain's cache (those the latest complete crawl saw, never an expired one), classes them again for
the given companies with the connector's matcher, and enqueues the pairs that are now `accept` or
`review` through `URLQueue.enqueue_batch`, which only ever promotes a stored row. It crawls
nothing: a domain with no complete crawl of the current roots that covers the range is reported as
an error, not refreshed. A stored row is never derived from the cache and never deleted.

Idempotent: a second run finds every pair stored at its class and inserts and promotes nothing.
Without `apply` it writes nothing (the database is opened read-only) and says what it would insert
and promote.

The classification is the full scan of today (about 4.6 s a ticker for `cnbc.com`); `T-071`'s token
index will replace it.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import httpx

from data_mining.matching import CLASS_RANK
from news_collector.connectors.base import BaseConnector
from news_collector.models import Company, DateRange, DiscoveredURL
from news_collector.sitemap_cache import SitemapCache, SitemapRun
from news_collector.storage.queue import URLQueue
from news_collector.strategies.sitemap import SitemapRSSStrategy

log = logging.getLogger(__name__)

# `(url, ticker)` pairs per read-only lookup: two bound values each, under SQLite's variable limit.
_LOOKUP_PAIRS = 400
# Pairs per `enqueue_batch` call.
_WRITE_BATCH = 5_000
# `accept` and `review` are what the re-class stores; `reject` is counted and never stored.
_STORED = ("accept", "review")


async def _never_crawl(_url: str) -> str:
    raise RuntimeError("the re-class from the cache never crawls")


@dataclass
class TickerResult:
    """What the re-class found for one (ticker, domain)."""

    ticker: str
    domain: str
    entries: int = 0
    classes: Counter[str] = field(default_factory=Counter)
    new: int = 0  # pairs not stored yet (inserted when applied)
    promotable: int = 0  # stored pairs whose class rises (promoted when applied)
    inserted: int = 0
    promoted: int = 0
    lifted: int = 0  # promoted rows that went from `skipped` back to `pending`


@dataclass
class ReclassReport:
    """The result of one re-class run, per ticker and in total."""

    applied: bool
    results: list[TickerResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def total(self, name: str) -> int:
        return sum(getattr(r, name) for r in self.results)

    def classes(self) -> Counter[str]:
        total: Counter[str] = Counter()
        for r in self.results:
            total.update(r.classes)
        return total


def _chunks(items: Sequence, size: int) -> Iterator[Sequence]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def stored_classes(db_path: str, pairs: Sequence[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """`match_class` of the stored `(url, ticker)` pairs, read through a read-only connection."""
    found: dict[tuple[str, str], str] = {}
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=30)
    try:
        for chunk in _chunks(list(pairs), _LOOKUP_PAIRS):
            marks = ",".join("(?,?)" for _ in chunk)
            # S608: `marks` is a run of "(?,?)" groups; the values are bound.
            rows = conn.execute(
                f"SELECT url, ticker, match_class FROM discovered_urls WHERE (url, ticker) IN ({marks})",  # noqa: S608
                [v for pair in chunk for v in pair],
            ).fetchall()
            found.update({(r[0], r[1]): r[2] for r in rows})
    finally:
        conn.close()
    return found


def _classify(
    connector: BaseConnector, run: SitemapRun, company: Company, date_range: DateRange
) -> tuple[int, list[DiscoveredURL]]:
    """The entries read for the company and all its pairs (every class), CPU work."""
    entries, _failures = run.entries(date_range)
    strategy = SitemapRSSStrategy(connector, cast(httpx.AsyncClient, None), run)
    return len(entries), strategy.classify_entries(entries, company.name, company.ticker)


async def reclass_from_cache(
    db_path: str,
    connectors: dict[str, BaseConnector],
    companies: Sequence[Company],
    date_range: DateRange,
    *,
    domains: Sequence[str] | None = None,
    apply: bool = False,
    progress: Callable[[TickerResult], object] | None = None,
) -> ReclassReport:
    """Re-class the cached entries of each domain for each company; see the module docstring.

    `domains` defaults to every connector that has `sitemap_roots`; a named domain without roots
    is an error line. `progress` is called after each (ticker, domain).
    """
    began = time.monotonic()
    report = ReclassReport(applied=apply)
    if not Path(db_path).is_file():  # a connection would create an empty file at a mistyped path
        report.errors.append(f"{db_path}: no such database (nothing was created)")
        return report
    wanted = (
        list(domains) if domains else [d for d, c in connectors.items() if c.config.sitemap_roots]
    )
    queue = URLQueue(db_path)
    if apply:
        queue.initialize()
    try:
        for domain in wanted:
            connector = connectors.get(domain)
            if connector is None or not connector.config.sitemap_roots:
                report.errors.append(f"{domain}: no sitemap cache for this domain")
                continue
            cache = SitemapCache(
                domain,
                connector.config.sitemap_roots,
                db_path,
                _never_crawl,
                connector.is_article_url,
            )
            run = await cache.begin(date_range, allow_crawl=False)
            if run.crawl is None:
                report.errors.extend(run.failures)
                continue
            log.info(
                "re-class %s from the crawl of %s (%d entries)",
                domain,
                run.crawl.started_at,
                len(run.rows),
            )
            for company in companies:
                result = TickerResult(company.ticker, domain)
                try:
                    await _one(queue, connector, run, company, date_range, apply, result)
                except sqlite3.Error as exc:  # a file without the queue tables, or a lock
                    report.errors.append(
                        f"{domain} {company.ticker}: database error: {exc}"
                        f" (written before it: {result.inserted} inserted, {result.promoted} promoted)"
                    )
                    if result.inserted or result.promoted:  # batches already committed stay counted
                        report.results.append(result)
                    continue
                log.info(
                    "re-class %s %s: %d entries, %s, new %d, promotable %d, inserted %d, promoted %d",
                    domain,
                    company.ticker,
                    result.entries,
                    dict(result.classes),
                    result.new,
                    result.promotable,
                    result.inserted,
                    result.promoted,
                )
                report.results.append(result)
                if progress is not None:
                    progress(result)
    finally:
        queue.close()
    report.elapsed_seconds = time.monotonic() - began
    return report


async def _one(
    queue: URLQueue,
    connector: BaseConnector,
    run: SitemapRun,
    company: Company,
    date_range: DateRange,
    apply: bool,
    result: TickerResult,
) -> None:
    """Fill `result`; on a database error it keeps what earlier batches already wrote."""
    result.entries, pairs = await asyncio.to_thread(_classify, connector, run, company, date_range)
    result.classes.update(p.match_class for p in pairs)
    stored = [p for p in pairs if p.match_class in _STORED]
    if not stored:
        return
    if apply:
        for chunk in _chunks(stored, _WRITE_BATCH):
            result.inserted += await asyncio.to_thread(queue.enqueue_batch, chunk)
            result.promoted += queue.last_promotion.promoted
            result.lifted += queue.last_promotion.lifted
        return
    known = await asyncio.to_thread(
        stored_classes, queue.db_path, sorted({(p.url, p.ticker) for p in stored})
    )
    best: dict[tuple[str, str], str] = {}
    for p in stored:  # the first entry of a URL wins, as `enqueue_batch` composes a batch
        best.setdefault((p.url, p.ticker), p.match_class)
    for key, cls in best.items():
        if key not in known:
            result.new += 1
        elif CLASS_RANK[cls] > CLASS_RANK[known[key]]:  # type: ignore[index]
            result.promotable += 1
