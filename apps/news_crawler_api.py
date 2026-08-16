#!/usr/bin/env python
"""Dev/test REST API for the S&P 500 news extraction pipeline — pipeline
stage 2 (full-text extraction). Source: news-crawler/app.py. See
docs/modules/news-crawler.md.

Thin FastAPI wrapper around `extractor.*` (parsing, DB, scheduling -- all
already unit-tested, see tests/extractor) and the same logic
`cli/news_crawler_cli.py` drives directly (no server) for batch runs.
Exists so individual pieces of the pipeline can be exercised interactively
over HTTP while
developing/debugging, without needing a full CLI run for every check -- e.g.
re-extract one URL, preview how a raw HTML blob would be parsed, inspect DB
state, or reset a row back to 'pending' to retry it.

NOT meant for production/public exposure: no auth, and several endpoints
either do live outbound HTTP fetches or mutate the database. Run locally
only. See docs/modules/news-crawler.md for the pipeline design this wraps.

Run:
    .venv\\Scripts\\python.exe apps\\news_crawler_api.py
    -> http://127.0.0.1:8002/docs for interactive Swagger UI
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel

from extractor.db import (
    enable_foreign_keys,
    ensure_articles_table,
    get_status_counts,
    get_urls_by_status,
    mark_status,
    save_article,
)
from extractor.parse import (
    extract_body_text,
    extract_jsonld_article,
    is_probably_paywalled,
    is_thin_content,
    word_count,
)
from extractor.pipeline import process_one
from extractor.reference import load_gics_map
from extractor.scheduler import DomainScheduler

load_dotenv()

# $DATABASE_URL is a filesystem path today (this is still SQLite) -- kept as
# an env var so pointing this at a real connection string later is a
# one-line env change, not a code change. Falls back to the pre-existing
# default when unset. Same path news_collector/news_nlp default to, since
# all three pipeline stages share one physical database file.
DEFAULT_DB = os.environ.get("DATABASE_URL", "data/urls.db")

# Same defaults as run_extraction.py: cnbc.com gets its own budget since it's
# ~86% of the corpus. One scheduler shared across requests, same as the CLI
# shares one across its whole run.
_scheduler = DomainScheduler(default_concurrency=2, domain_concurrency={"cnbc.com": 4})

app = FastAPI(
    title="news-crawler extraction API (dev/test)",
    description="Local-only wrapper over the S&P 500 article extraction pipeline. See CLAUDE.md.",
    version="0.1.0",
)


# ---------------------------------------------------------------- wiring --

def get_conn():
    # Not extractor.db.connect(): FastAPI resolves this sync generator
    # dependency in a threadpool worker thread, but async route handlers
    # (e.g. /extract/{id}) then use the yielded connection from the event
    # loop thread. sqlite3 connections are thread-bound by default, so a
    # connection opened the normal way raises ProgrammingError as soon as an
    # async endpoint touches it. check_same_thread=False lifts that
    # restriction; safe here because each request gets its own short-lived
    # connection (opened and closed within the same request, never shared
    # across requests) and every write already commits immediately (see
    # db.py), so there's no concurrent-write risk to guard against.
    conn = sqlite3.connect(DEFAULT_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    enable_foreign_keys(conn)
    ensure_articles_table(conn)
    try:
        yield conn
    finally:
        conn.close()


_gics_map_cache: dict[str, dict[str, str]] | None = None


async def get_gics_map() -> dict[str, dict[str, str]]:
    """Lazily fetched once per process lifetime, then cached -- avoids
    hitting Wikipedia on every request while the dev server is running.
    load_gics_map() already degrades to {} on failure, so a bad fetch just
    means empty GICS fields, not a broken endpoint.
    """
    global _gics_map_cache
    if _gics_map_cache is None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            _gics_map_cache = await load_gics_map(client)
    return _gics_map_cache


# ---------------------------------------------------------- response models

class StatusCounts(BaseModel):
    counts: dict[str, int]
    total: int


class DiscoveredURL(BaseModel):
    id: int
    url: str
    domain: str
    company: str
    ticker: str
    source: str
    status: str
    title: Optional[str] = None
    fetch_status_code: Optional[int] = None


class Article(BaseModel):
    id: int
    ticker: Optional[str] = None
    company: Optional[str] = None
    gics_sector: Optional[str] = None
    gics_sub_industry: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    pub_date: Optional[str] = None
    body_text: Optional[str] = None
    word_count: Optional[int] = None
    language: Optional[str] = None
    source_domain: Optional[str] = None
    extraction_method: Optional[str] = None
    fetch_status: Optional[str] = None
    http_status_code: Optional[int] = None
    fetched_at: Optional[str] = None


class BatchExtractResult(BaseModel):
    requested: int
    processed: int
    counts: dict[str, int]


class ResetResult(BaseModel):
    id: int
    status: str


class ParsePreviewRequest(BaseModel):
    html: Optional[str] = None
    url: Optional[str] = None
    domain: Optional[str] = None  # only read when `html` is given; needed for the paywall check


class ParsePreviewResponse(BaseModel):
    title: Optional[str]
    author: Optional[str]
    pub_date: Optional[str]
    body_text: Optional[str]
    word_count: int
    is_thin_content: bool
    is_probably_paywalled: bool
    http_status_code: Optional[int] = None


# --------------------------------------------------------------- endpoints

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status", response_model=StatusCounts)
def status(conn: sqlite3.Connection = Depends(get_conn)):
    """Same numbers run_extraction.py prints at startup: discovered_urls
    grouped by status."""
    counts = get_status_counts(conn)
    return StatusCounts(counts=counts, total=sum(counts.values()))


@app.get("/discovered", response_model=list[DiscoveredURL])
def list_discovered(
    status: Optional[str] = None,
    ticker: Optional[str] = None,
    domain: Optional[str] = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    conn: sqlite3.Connection = Depends(get_conn),
):
    sql = (
        "SELECT id, url, domain, company, ticker, source, status, title, fetch_status_code "
        "FROM discovered_urls WHERE 1=1"
    )
    params: list = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker)
    if domain:
        sql += " AND domain = ?"
        params.append(domain)
    sql += " ORDER BY id LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = conn.execute(sql, params).fetchall()
    return [DiscoveredURL(**dict(row)) for row in rows]


@app.get("/discovered/{url_id}", response_model=DiscoveredURL)
def get_discovered(url_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute(
        "SELECT id, url, domain, company, ticker, source, status, title, fetch_status_code "
        "FROM discovered_urls WHERE id = ?",
        (url_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"discovered_urls id={url_id} not found")
    return DiscoveredURL(**dict(row))


@app.post("/discovered/{url_id}/reset", response_model=ResetResult)
def reset_discovered(url_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    """Flip a row back to 'pending' (clearing fetch_status_code) so it can be
    re-extracted -- handy for testing after a code change, without hand-editing
    the DB."""
    row = conn.execute("SELECT id FROM discovered_urls WHERE id = ?", (url_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"discovered_urls id={url_id} not found")
    mark_status(conn, url_id, "pending", http_status_code=None)
    return ResetResult(id=url_id, status="pending")


@app.get("/articles", response_model=list[Article])
def list_articles(
    fetch_status: Optional[str] = None,
    ticker: Optional[str] = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    conn: sqlite3.Connection = Depends(get_conn),
):
    sql = "SELECT * FROM articles WHERE 1=1"
    params: list = []
    if fetch_status:
        sql += " AND fetch_status = ?"
        params.append(fetch_status)
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker)
    sql += " ORDER BY id LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = conn.execute(sql, params).fetchall()
    return [Article(**dict(row)) for row in rows]


@app.get("/articles/{article_id}", response_model=Article)
def get_article(article_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"article id={article_id} not found")
    return Article(**dict(row))


@app.post("/extract/run", response_model=BatchExtractResult)
async def extract_run(
    limit: int = Query(
        5, ge=1, le=50,
        description="Capped low on purpose -- this is a test endpoint, not a batch driver. "
                     "Use run_extraction.py for full/unattended runs.",
    ),
    status: str = Query(
        "pending",
        description="discovered_urls.status to select rows from -- 'pending' for a normal "
                     "run, 'failed' to retry URLs that previously errored (http_status>=400 "
                     "or a network failure) without a separate /discovered/{id}/reset call.",
    ),
    conn: sqlite3.Connection = Depends(get_conn),
    gics_map: dict = Depends(get_gics_map),
):
    """Same loop run_extraction.py runs, over a small number of rows in the
    given `status` -- for exercising the batch path (scheduler + save +
    status flip together) without kicking off a long unattended run from
    the API.

    Registered ABOVE /extract/{url_id} on purpose: FastAPI matches routes in
    registration order, and a literal path like /extract/run must be
    declared before the {url_id}: int catch-all or it never gets a chance to
    match (int-parsing "run" fails with a 422 instead)."""
    rows = get_urls_by_status(conn, [status], limit=limit)

    counts: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for row in rows:
            article = await process_one(
                client,
                _scheduler,
                dict(row),
                gics_sector=gics_map.get(row["ticker"], {}).get("sector"),
                gics_sub_industry=gics_map.get(row["ticker"], {}).get("sub_industry"),
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
            save_article(conn, article)
            mark_status(conn, article["id"], article["fetch_status"], article["http_status_code"])
            counts[article["fetch_status"]] = counts.get(article["fetch_status"], 0) + 1

    return BatchExtractResult(requested=len(rows), processed=sum(counts.values()), counts=counts)


@app.post("/extract/{url_id}", response_model=Article)
async def extract_one(
    url_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
    gics_map: dict = Depends(get_gics_map),
):
    """Run the full fetch -> parse -> classify -> save pipeline for exactly
    one discovered_urls row, regardless of its current status. The single
    most useful endpoint for testing a code change against a real page
    without running the whole batch CLI."""
    row = conn.execute(
        "SELECT id, url, domain, company, ticker, source, title FROM discovered_urls WHERE id = ?",
        (url_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"discovered_urls id={url_id} not found")

    async with httpx.AsyncClient(timeout=30.0) as client:
        article = await process_one(
            client,
            _scheduler,
            dict(row),
            gics_sector=gics_map.get(row["ticker"], {}).get("sector"),
            gics_sub_industry=gics_map.get(row["ticker"], {}).get("sub_industry"),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
    save_article(conn, article)
    mark_status(conn, article["id"], article["fetch_status"], article["http_status_code"])
    return Article(**article)


@app.post("/parse/preview", response_model=ParsePreviewResponse)
async def parse_preview(payload: ParsePreviewRequest):
    """Run just the parsing/classification logic (JSON-LD + meta fallback,
    trafilatura body extraction, thin-content/paywall checks) against either
    raw HTML you paste in, or a URL fetched live -- with NO database writes.
    Fastest way to test parse.py changes against a tricky real page."""
    if not payload.html and not payload.url:
        raise HTTPException(400, "Provide either `html` or `url`")

    html = payload.html
    domain = payload.domain
    http_status_code = None

    if payload.url:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(payload.url, follow_redirects=True)
        html = response.text
        http_status_code = response.status_code
        domain = domain or httpx.URL(payload.url).host

    jsonld = extract_jsonld_article(html) # type: ignore
    body_text = extract_body_text(html) # type: ignore

    return ParsePreviewResponse(
        title=jsonld["title"],
        author=jsonld["author"],
        pub_date=jsonld["pub_date"],
        body_text=body_text,
        word_count=word_count(body_text),
        is_thin_content=is_thin_content(body_text),
        is_probably_paywalled=is_probably_paywalled(html, domain or ""), # type: ignore
        http_status_code=http_status_code,
    )


@app.get("/gics")
async def gics(gics_map: dict = Depends(get_gics_map)):
    return gics_map


@app.get("/gics/{ticker}")
async def gics_for_ticker(ticker: str, gics_map: dict = Depends(get_gics_map)):
    entry = gics_map.get(ticker.upper())
    if entry is None:
        raise HTTPException(404, f"No GICS mapping for ticker={ticker}")
    return {"ticker": ticker.upper(), **entry}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8002, reload=False)
