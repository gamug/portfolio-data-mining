"""Discovery orchestrator router — run-all endpoint and queue utilities."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from news_collector.api.deps import get_client
from news_collector.api.routers._common import _to_out, resolve_companies
from news_collector.api.schemas import (
    DiscoveredURLOut,
    QueryResponse,
    QueueStatsResponse,
    RunAllRequest,
    RunAllResponse,
)
from news_collector.config import default_end_date, default_start_date
from news_collector.models import DateRange
from news_collector.orchestrator import DiscoveryOrchestrator, build_connectors
from news_collector.storage.queue import URLQueue

router = APIRouter(prefix="/discovery", tags=["Discovery"])


@router.post("/run-all", response_model=RunAllResponse)
async def run_all(
    req: RunAllRequest,
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
) -> RunAllResponse:
    """
    Run the full DiscoveryOrchestrator across all (or a selected subset of) domains.

    Drives DDG + Sitemap strategies concurrently for every (company, domain) pair,
    deduplicates results, and persists them to the SQLite queue. If req.tickers is
    omitted, runs across the entire S&P 500, fetched live from Wikipedia.
    """
    companies = await resolve_companies(req, client)

    date_range = DateRange(
        start=req.start_date or default_start_date(),
        end=req.end_date or default_end_date(),
    )
    queue = URLQueue(req.db_path)

    connectors = build_connectors(client)
    orchestrator = DiscoveryOrchestrator(
        connectors=connectors,
        queue=queue,
        concurrency=req.concurrency,
    )

    stats = await orchestrator.run(
        companies=companies,
        domains=req.domains,
        date_range=date_range,
        resume=req.resume,
    )

    return RunAllResponse(
        total_discovered=stats.total_discovered,
        total_inserted=stats.total_inserted,
        duplicate_count=stats.duplicate_count,
        ddg_count=stats.ddg_count,
        sitemap_count=stats.sitemap_count,
        skipped_pairs=stats.skipped_pairs,
        by_domain=stats.by_domain,
        by_company=stats.by_company,
        errors=stats.errors,
        elapsed_seconds=round(stats.elapsed_seconds, 3),
    )


@router.get("/stats", response_model=QueueStatsResponse)
async def queue_stats(
    db_path: str = Query(default="data/urls.db", description="Path to the SQLite queue database"),
) -> QueueStatsResponse:
    """Return current queue statistics (total, by status, by domain)."""
    queue = URLQueue(db_path)
    queue.initialize()
    raw = queue.stats()
    return QueueStatsResponse(
        total=raw["total"],
        by_status=raw["by_status"],
        by_domain=raw["by_domain"],
    )


@router.get("/pending", response_model=list[DiscoveredURLOut])
async def get_pending(
    domain: str | None = Query(default=None, description="Filter by domain"),
    ticker: str | None = Query(default=None, description="Filter by ticker"),
    limit: int = Query(default=100, ge=1, le=5000, description="Maximum rows to return"),
    db_path: str = Query(default="data/urls.db", description="Path to the SQLite queue database"),
) -> list[DiscoveredURLOut]:
    """Return pending URLs from the queue, with optional domain/ticker filters."""
    queue = URLQueue(db_path)
    queue.initialize()
    pending = queue.get_pending(domain=domain, ticker=ticker, limit=limit)
    return [_to_out(u) for u in pending]


@router.get("/query", response_model=QueryResponse)
async def query_urls(
    domain: str | None = Query(default=None, description="Filter by exact domain"),
    ticker: str | None = Query(default=None, description="Filter by exact ticker"),
    company: str | None = Query(default=None, description="Filter by company name (substring match)"),
    status: str | None = Query(
        default=None, description="Filter by status: pending, fetched, failed, or skipped"
    ),
    source: str | None = Query(default=None, description="Filter by discovery source: ddg, sitemap, rss, or api"),
    url_contains: str | None = Query(default=None, description="Filter by substring of the URL"),
    pub_date_from: date | None = Query(default=None, description="Only rows with pub_date >= this date"),
    pub_date_to: date | None = Query(default=None, description="Only rows with pub_date <= this date"),
    order_by: str = Query(
        default="discovered_at",
        description="Column to sort by: id, url, domain, company, ticker, source, discovered_at, pub_date, status",
    ),
    descending: bool = Query(default=True, description="Sort descending (default) or ascending"),
    limit: int = Query(default=100, ge=1, le=5000, description="Maximum rows to return"),
    offset: int = Query(default=0, ge=0, description="Rows to skip, for pagination"),
    db_path: str = Query(default="data/urls.db", description="Path to the SQLite queue database"),
) -> QueryResponse:
    """
    Ad-hoc query over the URL queue.

    Unlike /pending, this is not restricted to status='pending' — combine any
    filters to inspect the database directly (e.g. failed fetches for a ticker,
    or everything discovered via a given source in a date range).
    """
    queue = URLQueue(db_path)
    queue.initialize()
    try:
        rows, total = queue.query(
            domain=domain,
            ticker=ticker,
            company=company,
            status=status,
            source=source,
            url_contains=url_contains,
            pub_date_from=pub_date_from,
            pub_date_to=pub_date_to,
            order_by=order_by,
            descending=descending,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    results = [_to_out(u) for u in rows]
    return QueryResponse(total=total, count=len(results), limit=limit, offset=offset, results=results)
