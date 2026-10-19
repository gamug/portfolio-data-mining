"""Discovery orchestrator router — run-all endpoint and queue utilities."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from data_mining.db import resolve_db_path
from news_collector.api.deps import get_client
from news_collector.api.routers._common import _to_out, resolve_companies
from news_collector.api.schemas import (
    DiscoveredURLOut,
    QueryResponse,
    QueueStatsResponse,
    RunAllRequest,
    RunAllResponse,
)
from news_collector.config import InvalidRangeError, build_date_range
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
    try:
        date_range = build_date_range(req.start_date, req.end_date)
    except InvalidRangeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    companies = await resolve_companies(req, client)
    queue = URLQueue(resolve_db_path(req.db_path))

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
        refresh_sitemaps=req.refresh_sitemaps,
    )

    return RunAllResponse(
        total_discovered=stats.total_discovered,
        total_inserted=stats.total_inserted,
        total_rejected=stats.total_rejected,
        duplicate_count=stats.duplicate_count,
        ddg_count=stats.ddg_count,
        sitemap_count=stats.sitemap_count,
        skipped_pairs=stats.skipped_pairs,
        by_domain=stats.by_domain,
        by_company=stats.by_company,
        errors=stats.errors,
        elapsed_seconds=round(stats.elapsed_seconds, 3),
        start_date=date_range.start,
        end_date=date_range.end,
    )


@router.get("/stats", response_model=QueueStatsResponse)
async def queue_stats(
    db_path: str | None = Query(
        default=None, description="Path to the SQLite queue database (default: $DATABASE_URL)"
    ),
) -> QueueStatsResponse:
    """Return current queue statistics (total, by status, by domain)."""
    queue = URLQueue(resolve_db_path(db_path))
    queue.initialize()
    raw = queue.stats()
    return QueueStatsResponse(
        total=raw["total"],
        by_status=raw["by_status"],
        by_match_class=raw["by_match_class"],
        by_domain=raw["by_domain"],
    )


@router.get("/pending", response_model=list[DiscoveredURLOut])
async def get_pending(
    domain: str | None = Query(default=None, description="Filter by domain"),
    ticker: str | None = Query(default=None, description="Filter by ticker"),
    limit: int = Query(default=100, ge=1, le=5000, description="Maximum rows to return"),
    db_path: str | None = Query(
        default=None, description="Path to the SQLite queue database (default: $DATABASE_URL)"
    ),
) -> list[DiscoveredURLOut]:
    """Return pending URLs from the queue, with optional domain/ticker filters."""
    queue = URLQueue(resolve_db_path(db_path))
    queue.initialize()
    pending = queue.get_pending(domain=domain, ticker=ticker, limit=limit)
    return [_to_out(u) for u in pending]


@router.get("/query", response_model=QueryResponse)
async def query_urls(
    domain: str | None = Query(default=None, description="Filter by exact domain"),
    ticker: str | None = Query(default=None, description="Filter by exact ticker"),
    company: str | None = Query(
        default=None, description="Filter by company name (substring match)"
    ),
    status: str | None = Query(
        default=None,
        description=(
            "Filter by status: pending, fetched, failed, or skipped "
            "(skipped includes matcher-rejected rows, match_class=reject)"
        ),
    ),
    match_class: Literal["accept", "review", "reject"] | None = Query(
        default=None, description="Filter by company-matching class: accept, review, or reject"
    ),
    source: str | None = Query(
        default=None, description="Filter by discovery source: ddg, sitemap, rss, or api"
    ),
    url_contains: str | None = Query(default=None, description="Filter by substring of the URL"),
    pub_date_from: date | None = Query(
        default=None, description="Only rows with pub_date >= this date"
    ),
    pub_date_to: date | None = Query(
        default=None, description="Only rows with pub_date <= this date"
    ),
    order_by: str = Query(
        default="discovered_at",
        description="Column to sort by: id, url, domain, company, ticker, source, discovered_at, pub_date, status, match_class",
    ),
    descending: bool = Query(default=True, description="Sort descending (default) or ascending"),
    limit: int = Query(default=100, ge=1, le=5000, description="Maximum rows to return"),
    offset: int = Query(default=0, ge=0, description="Rows to skip, for pagination"),
    db_path: str | None = Query(
        default=None, description="Path to the SQLite queue database (default: $DATABASE_URL)"
    ),
) -> QueryResponse:
    """
    Ad-hoc query over the URL queue.

    Unlike /pending, this is not restricted to status='pending' — combine any
    filters to inspect the database directly (e.g. failed fetches for a ticker,
    or everything discovered via a given source in a date range).
    """
    queue = URLQueue(resolve_db_path(db_path))
    queue.initialize()
    try:
        rows, total = queue.query(
            domain=domain,
            ticker=ticker,
            company=company,
            status=status,
            source=source,
            match_class=match_class,
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
    return QueryResponse(
        total=total, count=len(results), limit=limit, offset=offset, results=results
    )
