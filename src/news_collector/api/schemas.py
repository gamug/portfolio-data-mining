"""Pydantic v2 request/response schemas for the News Collector API."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

_DATE_DEFAULT_NOTE = (
    " Omit to fall back to the DISCOVERY_START_DATE/DISCOVERY_END_DATE env vars "
    "(evaluated per-request), or 2022-01-01/today if those are unset."
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
            "Optional company names parallel to tickers. If omitted, ticker is used as name. "
            "Ignored when tickers is omitted (Wikipedia already supplies full company names)."
        ),
    )
    start_date: date | None = Field(
        default=None, description="Inclusive start date for discovery." + _DATE_DEFAULT_NOTE
    )
    end_date: date | None = Field(
        default=None, description="Inclusive end date for discovery." + _DATE_DEFAULT_NOTE
    )
    db_path: str = Field(default="data/urls.db", description="Path to the SQLite queue database")
    concurrency: int = Field(
        default=5, ge=1, le=20, description="Max tickers discovered in parallel for this domain"
    )


class RunAllRequest(BaseModel):
    """Request body for the run-all orchestrator endpoint."""

    tickers: list[str] | None = Field(default=None, description=_TICKERS_NOTE)
    company_names: list[str] | None = Field(
        default=None,
        description=(
            "Optional company names parallel to tickers. "
            "Ignored when tickers is omitted (Wikipedia already supplies full company names)."
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
    db_path: str = Field(default="data/urls.db", description="SQLite queue path")
    concurrency: int = Field(default=5, ge=1, le=20, description="Max parallel discovery tasks")
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


class RunAllResponse(BaseModel):
    """Aggregated response from the full orchestrator run."""

    total_discovered: int
    total_inserted: int
    duplicate_count: int
    ddg_count: int
    sitemap_count: int
    by_domain: dict[str, int]
    by_company: dict[str, int]
    errors: list[str]
    elapsed_seconds: float
    skipped_pairs: int = Field(
        default=0,
        description="(ticker, domain) pairs skipped because resume=True and already completed",
    )


class QueueStatsResponse(BaseModel):
    """Current state of the URL queue."""

    total: int
    by_status: dict[str, int]
    by_domain: dict[str, int]


class QueryResponse(BaseModel):
    """Paginated result of an ad-hoc queue query."""

    total: int = Field(description="Total rows matching the filters, before pagination")
    count: int = Field(description="Number of rows in this page")
    limit: int
    offset: int
    results: list[DiscoveredURLOut]
