"""Shared helpers for per-connector router handlers."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

import httpx
from fastapi import HTTPException

from news_collector.api.schemas import (
    DiscoveredURLOut,
    DiscoverRequest,
    DiscoverResponse,
    RunAllRequest,
)
from news_collector.config import default_end_date, default_start_date
from news_collector.connectors.base import BaseConnector
from news_collector.models import Company, DateRange, DiscoveredURL
from news_collector.sp500 import fetch_sp500_companies
from news_collector.storage.queue import URLQueue
from news_collector.strategies.ddg import DDGStrategy
from news_collector.strategies.sitemap import SitemapRSSStrategy

log = logging.getLogger(__name__)


async def resolve_companies(
    req: DiscoverRequest | RunAllRequest, client: httpx.AsyncClient
) -> list[Company]:
    """
    Build the Company list for a request.

    If req.tickers is omitted (or empty), fetches the full S&P 500 list live
    from Wikipedia instead — company_names is ignored in that case, since
    Wikipedia already supplies full company names. Shared by run_domain_discovery
    and the /discovery/run-all handler so both orchestration paths resolve
    companies identically.

    Raises:
        HTTPException(502): if req.tickers is omitted and the Wikipedia fetch/parse fails.
    """
    if not req.tickers:
        try:
            return await fetch_sp500_companies(client)
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to resolve S&P 500 company list from Wikipedia: {exc}",
            ) from exc

    names = req.company_names or []
    companies = []
    for i, raw_ticker in enumerate(req.tickers):
        ticker = raw_ticker.upper().strip()
        name = names[i].strip() if i < len(names) else ticker
        companies.append(Company(ticker=ticker, name=name))
    return companies


def _to_out(url: DiscoveredURL) -> DiscoveredURLOut:
    return DiscoveredURLOut(
        id=url.id,
        url=url.url,
        domain=url.domain,
        company=url.company,
        ticker=url.ticker,
        source=url.source,
        pub_date=url.pub_date,
        title=url.title,
    )


async def _discover_one_ticker(
    company: Company,
    connector: BaseConnector,
    client: httpx.AsyncClient,
    date_range: DateRange,
    extra_fetch: Callable | None = None,
) -> tuple[list[DiscoveredURL], list[DiscoveredURL], list[DiscoveredURL], list[str]]:
    """
    Run DDG + Sitemap for one ticker on a single connector.

    Optionally calls extra_fetch(company.ticker, date_range) for API-based connectors.
    Returns (ddg_urls, sitemap_urls, api_urls, errors).
    """
    domain_sem = asyncio.Semaphore(1)
    ddg_strategy = DDGStrategy(connector, domain_sem)
    sitemap_strategy = SitemapRSSStrategy(connector, client)
    errors: list[str] = []

    ddg_task = ddg_strategy.discover(company.name, company.ticker, date_range)
    sitemap_task = sitemap_strategy.discover(company.name, company.ticker, date_range)

    ddg_result, sitemap_result = await asyncio.gather(
        ddg_task, sitemap_task, return_exceptions=True
    )

    ddg_urls: list[DiscoveredURL] = []
    if isinstance(ddg_result, Exception):
        msg = f"DDG failed for {company.ticker}: {ddg_result}"
        log.warning(msg)
        errors.append(msg)
    else:
        ddg_urls = ddg_result  # type: ignore[assignment]

    sitemap_urls: list[DiscoveredURL] = []
    if isinstance(sitemap_result, Exception):
        msg = f"Sitemap failed for {company.ticker}: {sitemap_result}"
        log.warning(msg)
        errors.append(msg)
    else:
        sitemap_urls = sitemap_result  # type: ignore[assignment]

    api_urls: list[DiscoveredURL] = []
    if extra_fetch is not None:
        try:
            api_urls = await extra_fetch(company.ticker, date_range)
            for u in api_urls:
                u.company = company.name
        except Exception as exc:
            msg = f"API fetch failed for {company.ticker}: {exc}"
            log.warning(msg)
            errors.append(msg)

    return ddg_urls, sitemap_urls, api_urls, errors


async def run_domain_discovery(
    req: DiscoverRequest,
    connector: BaseConnector,
    client: httpx.AsyncClient,
    domain: str,
    extra_fetch: Callable | None = None,
) -> list[DiscoverResponse]:
    """
    Core logic shared by all per-domain endpoint handlers.

    Runs DDG + Sitemap (+ optional API) for every ticker in the request (or, if
    req.tickers is omitted, for the entire S&P 500 fetched live from Wikipedia),
    deduplicates, persists to the queue, and returns DiscoverResponse per ticker.
    """
    companies = await resolve_companies(req, client)
    date_range = DateRange(
        start=req.start_date or default_start_date(),
        end=req.end_date or default_end_date(),
    )
    queue = URLQueue(req.db_path)
    queue.initialize()

    # Bounds how many tickers are discovered in parallel for this single domain —
    # without it, a large tickers[] fans out unbounded (every ticker at once).
    concurrency_sem = asyncio.Semaphore(req.concurrency)

    async def _handle(company: Company) -> DiscoverResponse:
        async with concurrency_sem:
            t0 = time.monotonic()
            ddg_urls, sitemap_urls, api_urls, errors = await _discover_one_ticker(
                company, connector, client, date_range, extra_fetch
            )

            all_urls = ddg_urls + sitemap_urls + api_urls
            seen: set[str] = set()
            deduped: list[DiscoveredURL] = []
            for u in all_urls:
                if u.url not in seen:
                    seen.add(u.url)
                    deduped.append(u)

            inserted = queue.enqueue_batch(deduped)

            return DiscoverResponse(
                domain=domain,
                ticker=company.ticker,
                discovered=[_to_out(u) for u in deduped],
                ddg_count=len(ddg_urls),
                sitemap_count=len(sitemap_urls) + len(api_urls),
                inserted=inserted,
                elapsed_seconds=round(time.monotonic() - t0, 3),
                errors=errors,
            )

    results = await asyncio.gather(*[_handle(c) for c in companies], return_exceptions=True)

    responses: list[DiscoverResponse] = []
    for company, result in zip(companies, results, strict=False):
        if isinstance(result, Exception):
            log.error("Discovery failed for %s/%s: %s", company.ticker, domain, result)
            responses.append(
                DiscoverResponse(
                    domain=domain,
                    ticker=company.ticker,
                    discovered=[],
                    ddg_count=0,
                    sitemap_count=0,
                    inserted=0,
                    elapsed_seconds=0.0,
                    errors=[str(result)],
                )
            )
        else:
            responses.append(result)  # type: ignore[arg-type]

    return responses
