"""Abstract base connector interface."""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

from news_collector.models import DateRange, SitemapEntry  # noqa: F401 – re-exported for connectors

log = logging.getLogger(__name__)


@dataclass
class ConnectorConfig:
    """All per-domain configuration that a connector needs."""

    domain: str
    requests_per_second: float = 1.0
    headers: dict[str, str] = field(default_factory=dict)
    sitemap_roots: list[str] = field(default_factory=list)
    rss_feeds: list[str] = field(default_factory=list)
    # Added to every DDG query: e.g. "site:cnbc.com"
    ddg_site_filter: str = ""
    uses_official_api: bool = False
    has_paywall: bool = False
    # Use Wayback Machine CDX API as tertiary source
    wayback_fallback: bool = False
    # Maximum DDG results per query
    ddg_max_results: int = 50


class BaseConnector(ABC):
    """
    Contract that every domain connector must fulfill.

    Subclasses implement:
    - build_ddg_queries   → query strings for DDGStrategy
    - sitemap_urls        → async generator of (url, lastmod) for SitemapRSSStrategy
    - is_article_url      → True when url points to a real article
    - url_matches_company → True when article is relevant to company/ticker
    """

    def __init__(
        self,
        config: ConnectorConfig,
        client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self._client = client
        self._semaphore = asyncio.Semaphore(
            max(1, int(config.requests_per_second))
        )

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def build_ddg_queries(
        self,
        company: str,
        ticker: str,
        date_range: DateRange,
    ) -> list[str]:
        """
        Return a list of DDG query strings scoped to this domain.

        Postconditions:
        - Returns at least one query string
        - Each query includes self.config.ddg_site_filter when non-empty
        """

    @abstractmethod
    async def sitemap_urls(
        self, date_range: DateRange
    ) -> AsyncIterator[SitemapEntry]:
        """
        Async-generate SitemapEntry objects for all article URLs available
        via sitemap/RSS/API that fall within date_range.

        Postconditions:
        - Only yields entries whose lastmod is within date_range (or None)
        - Yields nothing when config.sitemap_roots and rss_feeds are both empty
        """

    @abstractmethod
    def is_article_url(self, url: str) -> bool:
        """
        Return True if url points to a news article (not a tag/category page).

        Postconditions:
        - Idempotent for the same url
        - Never returns True for the bare domain root
        """

    @abstractmethod
    def url_matches_company(
        self, url: str, title: str, company: str, ticker: str
    ) -> bool:
        """
        Return True if the article URL or title is relevant to company/ticker.

        Postconditions:
        - Case-insensitive match
        - Matching on ticker OR significant company name token is sufficient
        """

    # ------------------------------------------------------------------
    # Shared helpers available to all connectors
    # ------------------------------------------------------------------

    async def _fetch(self, url: str) -> str:
        """Rate-limited HTTP GET returning response text."""
        from news_collector.utils.http import fetch_text

        async with self._semaphore:
            return await fetch_text(self._client, url, extra_headers=self.config.headers)

    def _company_pattern(self, company: str, ticker: str) -> re.Pattern[str]:
        """Compile a regex that matches company name tokens or ticker."""
        # Use the first word of company name if it's long enough (>3 chars)
        first_word = company.split()[0] if company.split() else company
        token = first_word if len(first_word) > 3 else company
        # Escape for regex safety
        escaped_name = re.escape(token)
        escaped_ticker = re.escape(ticker)
        return re.compile(
            rf"\b({escaped_ticker}|{escaped_name})\b",
            re.IGNORECASE,
        )

    def _matches_any(
        self,
        pattern: re.Pattern[str],
        *texts: str,
    ) -> bool:
        return any(pattern.search(t) for t in texts if t)
