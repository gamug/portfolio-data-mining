"""Discovery orchestrator — coordinates DDG + Sitemap strategies across all (company, domain) pairs."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence

import httpx

from news_collector.config import default_end_date, default_start_date
from news_collector.connectors.base import BaseConnector
from news_collector.connectors.cnbc import CNBCConnector
from news_collector.connectors.ft import FTConnector
from news_collector.connectors.investing import InvestingConnector
from news_collector.connectors.nasdaq import NasdaqConnector
from news_collector.connectors.seeking_alpha import SeekingAlphaConnector
from news_collector.connectors.stocktwits import StockTwitsConnector
from news_collector.connectors.yahoo_finance import YahooFinanceConnector
from news_collector.models import Company, DateRange, DiscoveredURL, DiscoveryStats, PartialStats
from news_collector.storage.queue import URLQueue
from news_collector.strategies.ddg import DDGStrategy
from news_collector.strategies.sitemap import SitemapRSSStrategy

log = logging.getLogger(__name__)
# Separate logger name so progress/ETA lines can be filtered independently
# from the per-pair result lines emitted by _run_one.
progress_log = logging.getLogger(f"{__name__}.progress")


def _format_duration(seconds: float) -> str:
    """Render a duration as e.g. '1h02m03s', '2m03s', or '3s'."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def build_connectors(client: httpx.AsyncClient) -> dict[str, BaseConnector]:
    """Instantiate all domain connectors and return a domain → connector mapping."""
    return {
        "cnbc.com": CNBCConnector(client),
        "finance.yahoo.com": YahooFinanceConnector(client),
        "ft.com": FTConnector(client),
        "investing.com": InvestingConnector(client),
        "nasdaq.com": NasdaqConnector(client),
        "seekingalpha.com": SeekingAlphaConnector(client),
        "stocktwits.com": StockTwitsConnector(client),
    }


class DiscoveryOrchestrator:
    """
    Top-level coordinator for hybrid news link discovery.

    Drives DDGStrategy and SitemapRSSStrategy concurrently for each
    (company, domain) pair, deduplicates results in-memory, then persists
    to the URLQueue.
    """

    def __init__(
        self,
        connectors: dict[str, BaseConnector],
        queue: URLQueue,
        concurrency: int = 5,
    ) -> None:
        self._connectors = connectors
        self._queue = queue
        self._concurrency = concurrency
        # Shared HTTP client reference stored on connectors
        # Per-domain semaphores: 1 concurrent task per domain prevents hammering
        self._domain_sems: dict[str, asyncio.Semaphore] = {
            domain: asyncio.Semaphore(1) for domain in connectors
        }

    async def run(
        self,
        companies: Sequence[Company],
        domains: Sequence[str] | None = None,
        date_range: DateRange | None = None,
        resume: bool = True,
    ) -> DiscoveryStats:
        """
        Run discovery for all (company, domain) combinations.

        Args:
            companies: List of S&P 500 companies to discover news for.
            domains: Subset of domains to run. Defaults to all configured domains.
            date_range: Date range for article filtering. Defaults to 2022-01-01 to today.
            resume: If True (the default), skip (ticker, domain) pairs already completed
                for this exact date_range on a prior run, per the queue's
                discovery_progress checkpoint. Completion is always recorded regardless
                of this flag — resume only controls whether that record is consulted to
                skip work. Pass False to force a full redo of every pair.

        Returns:
            Aggregated DiscoveryStats.
        """
        if date_range is None:
            date_range = DateRange(start=default_start_date(), end=default_end_date())

        active_domains = list(domains) if domains else list(self._connectors.keys())
        active_domains = [d for d in active_domains if d in self._connectors]

        self._queue.initialize()

        stats = DiscoveryStats()
        t0 = time.monotonic()

        # Global concurrency semaphore limits total parallel tasks
        global_sem = asyncio.Semaphore(self._concurrency)

        all_pairs = [(company, domain) for company in companies for domain in active_domains]

        # Checked unconditionally (not just when resume=True) so the initial
        # summary can always report prior progress, even on a run that won't
        # act on it.
        completed_so_far = self._queue.completed_pairs(
            active_domains, date_range.start, date_range.end
        )
        already_done_count = sum(1 for c, d in all_pairs if (c.ticker, d) in completed_so_far)
        already_done_pct = (already_done_count / len(all_pairs) * 100) if all_pairs else 0.0

        skip_pairs = completed_so_far if resume else set()
        pending_pairs = [(c, d) for c, d in all_pairs if (c.ticker, d) not in skip_pairs]
        stats.skipped_pairs = len(all_pairs) - len(pending_pairs)
        total_tasks = len(pending_pairs)

        log.info(
            "\n\n======================================================================\n"
            "Discovery run starting\n"
            "  Domains          : %d (%s)\n"
            "  Companies        : %d\n"
            "  Date range       : %s -> %s\n"
            "  Queries to run   : %d\n"
            "  Advance so far   : %d/%d (%.1f%%) already completed in a prior run%s\n"
            "======================================================================\n",
            len(active_domains),
            ", ".join(active_domains),
            len(companies),
            date_range.start.isoformat(),
            date_range.end.isoformat(),
            total_tasks,
            already_done_count,
            len(all_pairs),
            already_done_pct,
            "" if resume else " (--no-resume passed - redoing everything, not skipped)",
        )

        # Progress is tracked here (not inside _run_one) because only run() knows
        # the total task count and elapsed-so-far needed to estimate an ETA.
        completed = 0

        # baseline_completed folds in pairs already finished on a *prior* run so the
        # displayed advance continues from where it left off instead of resetting to
        # 0% on every --resume run. Only meaningful when resume=True: that's the only
        # case where those pairs are actually being skipped rather than redone this
        # session (if resume=False, pending_pairs == all_pairs and completed alone
        # already spans 0 -> grid_total correctly).
        baseline_completed = already_done_count if resume else 0
        grid_total = len(all_pairs)

        async def _tracked(company: Company, domain: str) -> PartialStats:
            nonlocal completed
            progress_log.info("-> %s / %s: started", company.ticker, domain)
            result = await self._run_one_with_sem(global_sem, company, domain, date_range)
            completed += 1
            elapsed = time.monotonic() - t0
            # ETA is based on this session's own throughput (elapsed/completed) since
            # that's the only timing data available - only remaining/total_tasks (this
            # session's work) feed it, not the cross-run grid totals below.
            avg_per_task = elapsed / completed
            remaining = total_tasks - completed
            eta_seconds = avg_per_task * remaining
            grid_completed = baseline_completed + completed
            pct = (grid_completed / grid_total * 100) if grid_total else 100.0
            progress_log.info(
                "\n\n======================================================================\n"
                "[%d/%d] %.1f%% complete - finished %s / %s - elapsed %s - "
                "%d task(s) remaining - ETA %s\n"
                "======================================================================\n",
                grid_completed,
                grid_total,
                pct,
                company.ticker,
                domain,
                _format_duration(elapsed),
                remaining,
                _format_duration(eta_seconds) if remaining else "0s",
            )
            return result

        tasks = [_tracked(company, domain) for company, domain in pending_pairs]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                log.error("Discovery task raised: %s", result)
                stats.errors.append(str(result))
            elif isinstance(result, PartialStats):
                stats.merge(result)

        stats.elapsed_seconds = time.monotonic() - t0
        log.info(
            "Discovery complete: inserted=%d, duplicates=%d, elapsed=%.1fs",
            stats.total_inserted,
            stats.duplicate_count,
            stats.elapsed_seconds,
        )
        return stats

    async def _run_one_with_sem(
        self,
        global_sem: asyncio.Semaphore,
        company: Company,
        domain: str,
        date_range: DateRange,
    ) -> PartialStats:
        async with global_sem:
            return await self._run_one(company, domain, date_range)

    async def _run_one(
        self,
        company: Company,
        domain: str,
        date_range: DateRange,
    ) -> PartialStats:
        """
        Discover URLs for a single (company, domain) pair.

        Runs DDG + Sitemap strategies in parallel, merges results, deduplicates,
        and inserts into the queue.
        """
        partial = PartialStats(company=company.name, domain=domain)
        connector = self._connectors[domain]
        domain_sem = self._domain_sems[domain]

        log.debug("Discovering %s / %s", company.ticker, domain)

        # Build shared HTTP client from connector
        client = connector._client

        ddg_strategy = DDGStrategy(connector, domain_sem)
        sitemap_strategy = SitemapRSSStrategy(connector, client)

        # Run both strategies concurrently
        try:
            ddg_task = ddg_strategy.discover(company.name, company.ticker, date_range)
            sitemap_task = sitemap_strategy.discover(company.name, company.ticker, date_range)

            ddg_results, sitemap_results = await asyncio.gather(
                ddg_task, sitemap_task, return_exceptions=True
            )
        except Exception as exc:
            partial.error = str(exc)
            log.warning("Discovery failed for %s/%s: %s", company.ticker, domain, exc)
            return partial

        # Handle per-strategy exceptions gracefully
        ddg_urls: list[DiscoveredURL] = []
        if isinstance(ddg_results, Exception):
            log.warning("DDG failed for %s/%s: %s", company.ticker, domain, ddg_results)
        else:
            ddg_urls = ddg_results  # type: ignore[assignment]

        sitemap_urls: list[DiscoveredURL] = []
        if isinstance(sitemap_results, Exception):
            log.warning("Sitemap failed for %s/%s: %s", company.ticker, domain, sitemap_results)
        else:
            sitemap_urls = sitemap_results  # type: ignore[assignment]

        # StockTwits and Yahoo Finance have API fetch methods — use them too
        api_urls = await self._fetch_via_extra_api(connector, domain, company, date_range)

        # Merge and deduplicate by normalized URL
        deduped, duplicate_count = self._merge_and_dedupe(ddg_urls, sitemap_urls, api_urls)

        # Persist to queue
        inserted = self._queue.enqueue_batch(deduped)

        # Checkpoint: record this (ticker, domain) pair as done for this date range
        # so a future resume run can skip it. Recorded unconditionally (not gated
        # on `resume`) so any run leaves a usable checkpoint for the next one.
        self._queue.mark_pair_completed(
            company.ticker, domain, date_range.start, date_range.end, inserted
        )

        partial.ddg_count = len(ddg_urls)
        partial.sitemap_count = len(sitemap_urls) + len(api_urls)
        partial.inserted_count = inserted
        partial.duplicate_count = duplicate_count

        log.info(
            "%s / %s: ddg=%d sitemap/api=%d inserted=%d dupes=%d",
            company.ticker,
            domain,
            partial.ddg_count,
            partial.sitemap_count,
            inserted,
            duplicate_count,
        )
        return partial

    async def _fetch_via_extra_api(
        self,
        connector: BaseConnector,
        domain: str,
        company: Company,
        date_range: DateRange,
    ) -> list[DiscoveredURL]:
        """StockTwits and Yahoo Finance have dedicated API fetch methods on
        top of the DDG/sitemap strategies -- use them when available."""
        api_urls: list[DiscoveredURL] = []
        if domain == "stocktwits.com" and isinstance(connector, StockTwitsConnector):
            try:
                api_urls = await connector.fetch_via_api(company.ticker, date_range)
            except Exception as exc:
                log.warning("StockTwits API failed for %s: %s", company.ticker, exc)
                return api_urls
        elif domain == "finance.yahoo.com" and isinstance(connector, YahooFinanceConnector):
            try:
                api_urls = await connector.fetch_via_yfinance(company.ticker, date_range)
            except Exception as exc:
                log.warning("yfinance failed for %s: %s", company.ticker, exc)
                return api_urls
        else:
            return api_urls

        for u in api_urls:
            u.company = company.name
        return api_urls

    @staticmethod
    def _merge_and_dedupe(
        ddg_urls: list[DiscoveredURL],
        sitemap_urls: list[DiscoveredURL],
        api_urls: list[DiscoveredURL],
    ) -> tuple[list[DiscoveredURL], int]:
        """Merge results from all strategies and dedupe by normalized URL.
        Returns (deduped urls, duplicate count)."""
        all_urls = ddg_urls + sitemap_urls + api_urls
        seen: set[str] = set()
        deduped: list[DiscoveredURL] = []
        for u in all_urls:
            if u.url not in seen:
                seen.add(u.url)
                deduped.append(u)
        return deduped, len(all_urls) - len(deduped)
