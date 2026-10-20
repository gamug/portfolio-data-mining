"""Pydantic v2 request/response schemas for the News Collector API."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

_DATE_DEFAULT_NOTE = (
    " Omit start_date for $DISCOVERY_START_DATE (else 2022-01-01) and end_date for today, "
    "UTC (both evaluated per request). Pass the same end_date to continue a batch on a later "
    "day: resume skips only an exact range. start_date after end_date is a 422."
)

_TICKERS_NOTE = (
    "List of ticker symbols, e.g. ['AAPL', 'MSFT']. Omit (or pass an empty list) to run "
    "discovery across the entire S&P 500, fetched live from Wikipedia."
)


class DiscoverRequest(BaseModel):
    """Request body for single-domain discovery endpoints."""

    tickers: list[str] | None = Field(default=None, description=_TICKERS_NOTE)
    company_names: list[str] | None = Field(
        default=None,
        description=(
            "Optional company names, positional: one per ticker, same length (else 422). If omitted, each "
            "ticker is looked up by exact symbol in the current S&P 500, then as of start_date "
            "(a ticker found in neither is a 422); an empty list counts as omitted. Ignored when tickers is omitted."
        ),
    )
    start_date: date | None = Field(
        default=None, description="Inclusive start date for discovery." + _DATE_DEFAULT_NOTE
    )
    end_date: date | None = Field(
        default=None, description="Inclusive end date for discovery." + _DATE_DEFAULT_NOTE
    )
    db_path: str | None = Field(
        default=None, description="Path to the SQLite queue database (default: $DATABASE_URL)"
    )
    concurrency: int = Field(
        default=5, ge=1, le=20, description="Max tickers discovered in parallel for this domain"
    )
    refresh_sitemaps: bool = Field(
        default=False,
        description=(
            "Crawl the domain's sitemap even when its cache is at most 7 days old. Two forced "
            "requests at the same time make one crawl; the cache never narrows."
        ),
    )


class RunAllRequest(BaseModel):
    """Request body for the run-all orchestrator endpoint."""

    tickers: list[str] | None = Field(default=None, description=_TICKERS_NOTE)
    company_names: list[str] | None = Field(
        default=None,
        description=(
            "Optional company names, positional: one per ticker, same length (else 422). If omitted, each "
            "ticker is looked up by exact symbol in the current S&P 500, then as of start_date "
            "(a ticker found in neither is a 422); an empty list counts as omitted. Ignored when tickers is omitted."
        ),
    )
    start_date: date | None = Field(
        default=None, description="Inclusive start date." + _DATE_DEFAULT_NOTE
    )
    end_date: date | None = Field(
        default=None, description="Inclusive end date." + _DATE_DEFAULT_NOTE
    )
    domains: list[str] | None = Field(
        default=None,
        description="Subset of the 7 supported domains. None means all.",
    )
    db_path: str | None = Field(
        default=None, description="SQLite queue path (default: $DATABASE_URL)"
    )
    concurrency: int = Field(default=5, ge=1, le=20, description="Max parallel discovery tasks")
    refresh_sitemaps: bool = Field(
        default=False,
        description=(
            "Crawl the domain's sitemap even when its cache is at most 7 days old. Two forced "
            "requests at the same time make one crawl; the cache never narrows."
        ),
    )
    resume: bool = Field(
        default=True,
        description=(
            "Skip (ticker, domain) pairs already completed for this exact date range "
            "on a prior run, per the queue's checkpoint. On by default; set false to "
            "force a full redo."
        ),
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class DiscoveredURLOut(BaseModel):
    """Serializable representation of a single discovered URL."""

    # Primary key of the discovered_urls row. None only for rows returned from
    # a single-domain discovery response that hasn't gone through a DB read
    # back (see _to_out) — every row read via /discovery/pending or
    # /discovery/query always has one. Downstream consumers should hold this
    # as a foreign key back to this table rather than the (mutable, not
    # globally-unique) url string.
    id: int | None = None
    url: str
    domain: str
    company: str
    ticker: str
    source: str
    pub_date: date | None = None
    title: str | None = None
    # Company-matching class (`accept` / `review` / `reject`) and the signal behind it.
    match_class: str = "accept"
    match_reason: str | None = None


# About 4 minutes of classification for `cnbc.com` at 4.6 s a ticker.
MAX_RECLASS_TICKERS = 50


class ReclassRequest(BaseModel):
    """Request body for the re-class of the sitemap cache (T-072). No network, no crawl."""

    tickers: list[str] = Field(
        min_length=1,
        max_length=MAX_RECLASS_TICKERS,
        description=(
            f"Ticker symbols to re-class, e.g. ['AAPL', 'MSFT']. Required, at most "
            f"{MAX_RECLASS_TICKERS} as sent (duplicates count): the request has no progress and "
            "cannot be stopped. The whole S&P 500 is the CLI's job (`reclass-sitemap`)."
        ),
    )
    company_names: list[str] | None = Field(
        default=None,
        description="Optional company names, positional (one per ticker), as in the discovery requests.",
    )
    start_date: date | None = Field(
        default=None, description="Inclusive start date." + _DATE_DEFAULT_NOTE
    )
    end_date: date | None = Field(
        default=None, description="Inclusive end date." + _DATE_DEFAULT_NOTE
    )
    domains: list[str] | None = Field(
        default=None,
        description="Domains with a sitemap cache. None means every one that has roots.",
    )
    db_path: str | None = Field(
        default=None, description="SQLite queue path (default: $DATABASE_URL)"
    )
    apply: bool = Field(
        default=False,
        description=(
            "False (the default) is a dry run: nothing is written, the response says what would be "
            "inserted and promoted. True enqueues the pairs that are now accept/review (promote-only)."
        ),
    )


class ReclassTickerOut(BaseModel):
    ticker: str
    domain: str
    entries: int
    accept: int
    review: int
    reject: int = Field(description="Classed reject from the cache: counted, never stored.")
    would_insert: int = 0
    would_promote: int = 0
    inserted: int = 0
    promoted: int = 0
    lifted: int = Field(default=0, description="Promoted rows that went from skipped to pending.")


class ReclassResponse(BaseModel):
    """What the re-class of the sitemap cache found (and wrote, when `apply` was true)."""

    applied: bool
    tickers: list[ReclassTickerOut]
    accept: int
    review: int
    reject: int
    would_insert: int
    would_promote: int
    inserted: int
    promoted: int
    lifted: int
    errors: list[str]
    elapsed_seconds: float
    start_date: date = Field(description="Inclusive start of the range this run used.")
    end_date: date = Field(description="Inclusive end of the range this run used.")


class DiscoverResponse(BaseModel):
    """Response for a single (domain, ticker) discovery run."""

    domain: str
    ticker: str
    discovered: list[DiscoveredURLOut]
    ddg_count: int
    sitemap_count: int
    inserted: int
    elapsed_seconds: float
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Not errors: the sitemap cache was used although its refresh failed (the age of the "
            "crawl is in the text). The pair is checkpointed."
        ),
    )
    start_date: date = Field(description="Inclusive start of the range this run used.")
    end_date: date = Field(description="Inclusive end of the range this run used.")


class RunAllResponse(BaseModel):
    """Aggregated response from the full orchestrator run."""

    total_discovered: int
    total_inserted: int
    total_rejected: int = 0
    duplicate_count: int
    ddg_count: int
    sitemap_count: int
    by_domain: dict[str, int]
    by_company: dict[str, int]
    errors: list[str]
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Not errors: the sitemap cache was used although its refresh failed (the age of the "
            "crawl is in the text). The pair is checkpointed."
        ),
    )
    elapsed_seconds: float
    skipped_pairs: int = Field(
        default=0,
        description="(ticker, domain) pairs skipped because resume=True and already completed",
    )
    start_date: date = Field(description="Inclusive start of the range this run used.")
    end_date: date = Field(description="Inclusive end of the range this run used.")


class QueueStatsResponse(BaseModel):
    """Current state of the URL queue."""

    total: int
    by_status: dict[str, int] = Field(
        description="Rows per status; skipped includes matcher-rejected rows (see by_match_class)"
    )
    by_domain: dict[str, int]
    by_match_class: dict[str, int] = Field(
        description="Rows per company-matching class: accept, review, reject"
    )


class QueryResponse(BaseModel):
    """Paginated result of an ad-hoc queue query."""

    total: int = Field(description="Total rows matching the filters, before pagination")
    count: int = Field(description="Number of rows in this page")
    limit: int
    offset: int
    results: list[DiscoveredURLOut]
