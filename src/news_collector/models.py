"""Core data models for news link discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

# ---------------------------------------------------------------------------
# Domain configuration
# ---------------------------------------------------------------------------

# DDG timelimit day-count thresholds -- see DateRange.ddg_timelimit() below.
_DDG_TIMELIMIT_WEEK_DAYS = 7
_DDG_TIMELIMIT_MONTH_DAYS = 31
_DDG_TIMELIMIT_YEAR_DAYS = 366  # leap-year-safe

SUPPORTED_DOMAINS: frozenset[str] = frozenset(
    {
        "cnbc.com",
        "finance.yahoo.com",
        "ft.com",
        "investing.com",
        "nasdaq.com",
        "seekingalpha.com",
        "stocktwits.com",
    }
)


@dataclass
class DateRange:
    """Inclusive date range for article discovery."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"start {self.start} must be <= end {self.end}")

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    def ddg_timelimit(self) -> str | None:
        """
        Map date range to DDG timelimit string.
        DDG supports: "d" (day), "w" (week), "m" (month), "y" (year).
        For multi-year ranges covering 2022-present we return None and rely on
        query-level date terms instead.
        """
        delta = self.end - self.start
        if delta.days <= 1:
            return "d"
        if delta.days <= _DDG_TIMELIMIT_WEEK_DAYS:
            return "w"
        if delta.days <= _DDG_TIMELIMIT_MONTH_DAYS:
            return "m"
        if delta.days <= _DDG_TIMELIMIT_YEAR_DAYS:
            return "y"
        return None  # wider than a year — handle via query text


@dataclass
class Company:
    """A single S&P 500 constituent."""

    ticker: str
    name: str
    sector: str = ""
    aliases: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ticker = self.ticker.upper().strip()
        if not self.aliases:
            self.aliases = [self.ticker, self.name]


# ---------------------------------------------------------------------------
# Discovered URL
# ---------------------------------------------------------------------------

DiscoverySource = Literal["ddg", "sitemap", "rss", "api"]
URLStatus = Literal["pending", "fetched", "failed", "skipped"]

# Query params that are pure tracking noise and should be stripped
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "ref",
        "referrer",
        "_r",
        "ncid",
        "cid",
        "src",
    }
)


@dataclass
class DiscoveredURL:
    """A single article URL discovered by one of the discovery strategies."""

    url: str
    domain: str
    company: str
    ticker: str
    source: DiscoverySource
    discovered_at: datetime = field(default_factory=datetime.utcnow)
    pub_date: date | None = None
    title: str | None = None
    status: URLStatus = "pending"
    fetch_status_code: int | None = None
    # Primary key of the `discovered_urls` row, once persisted. None for an
    # in-memory instance that hasn't been through URLQueue.enqueue_batch()/read
    # back from the queue yet — SQLite assigns it on insert, we don't invent one
    # client-side. This is the stable identifier downstream stages (the news
    # crawler/fetcher, and anything built on top of it) should hold a foreign
    # key to, rather than the mutable `url` string: url + ticker is unique but
    # a bare url is not (the same article can be discovered under more than
    # one ticker), so `id` is the only column safe to reference 1:1.
    id: int | None = None


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


@dataclass
class PartialStats:
    """Stats for a single (company, domain) discovery task."""

    company: str
    domain: str
    ddg_count: int = 0
    sitemap_count: int = 0
    inserted_count: int = 0
    duplicate_count: int = 0
    error: str | None = None


@dataclass
class DiscoveryStats:
    """Aggregated stats across all discovery tasks."""

    total_discovered: int = 0
    total_inserted: int = 0
    duplicate_count: int = 0
    ddg_count: int = 0
    sitemap_count: int = 0
    by_domain: dict[str, int] = field(default_factory=dict)
    by_company: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    skipped_pairs: int = 0

    def merge(self, partial: PartialStats) -> None:
        discovered = partial.ddg_count + partial.sitemap_count
        self.total_discovered += discovered
        self.total_inserted += partial.inserted_count
        self.duplicate_count += partial.duplicate_count
        self.ddg_count += partial.ddg_count
        self.sitemap_count += partial.sitemap_count
        self.by_domain[partial.domain] = (
            self.by_domain.get(partial.domain, 0) + partial.inserted_count
        )
        self.by_company[partial.company] = (
            self.by_company.get(partial.company, 0) + partial.inserted_count
        )
        if partial.error:
            self.errors.append(f"{partial.company}/{partial.domain}: {partial.error}")


# ---------------------------------------------------------------------------
# Internal sitemap parsing model
# ---------------------------------------------------------------------------


@dataclass
class SitemapEntry:
    """A single entry parsed from a sitemap XML or RSS feed."""

    url: str
    lastmod: date | None = None
    title: str | None = None
    changefreq: str | None = None
