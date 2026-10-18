"""Shared helpers for per-connector router handlers."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

import httpx
from fastapi import HTTPException

from data_mining.db import resolve_db_path
from news_collector.api.schemas import (
    DiscoveredURLOut,
    DiscoverRequest,
    DiscoverResponse,
    RunAllRequest,
)
from news_collector.companies import (
    CompanyLookupError,
    companies_from_names,
    lookup_companies,
    sp500_companies,
)
from news_collector.config import InvalidRangeError, build_date_range, default_start_date
from news_collector.connectors.base import BaseConnector
from news_collector.models import Company, DateRange, DiscoveredURL
from news_collector.storage.queue import URLQueue
from news_collector.strategies.ddg import DDGStrategy
from news_collector.strategies.sitemap import SitemapRSSStrategy, summarize_failures

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

    With tickers, company_names is positional (one name per ticker) and must match
    in length. When it is omitted, each ticker is looked up by exact symbol in the
    current S&P 500 list, then, for a ticker that has left the index, in the
    point-in-time store as of the request's start date (T-067). A ticker's name is
    never defaulted to the ticker: the matcher compares URLs to the name.

    Raises:
        HTTPException(422): company_names has a different length than tickers or a blank
            name, or a ticker is in neither source.
        HTTPException(502): the Wikipedia fetch/parse fails.
    """
    try:
        if not req.tickers:
            return await sp500_companies(client)
        tickers = [t.upper().strip() for t in req.tickers]
        if req.company_names:
            return companies_from_names(tickers, req.company_names)
        return await lookup_companies(tickers, client, req.start_date or default_start_date())
    except CompanyLookupError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


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
        match_class=url.match_class,
        match_reason=url.match_reason,
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
    if sitemap_strategy.failures:
        # a failed root or child is an error here too (T-076); there is no checkpoint on this route
        msg = f"Sitemap incomplete for {company.ticker}: {summarize_failures(sitemap_strategy.failures)}"
        log.warning(msg)
        errors.append(msg)

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
    try:
        date_range = build_date_range(req.start_date, req.end_date)
    except InvalidRangeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    companies = await resolve_companies(req, client)
    queue = URLQueue(resolve_db_path(req.db_path))
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
                start_date=date_range.start,
                end_date=date_range.end,
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
                    start_date=date_range.start,
                    end_date=date_range.end,
                )
            )
        else:
            responses.append(result)  # type: ignore[arg-type]

    return responses
