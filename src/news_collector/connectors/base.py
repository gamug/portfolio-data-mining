"""Abstract base connector interface."""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

from data_mining.matching import Match, MatchClass, classify
from news_collector.models import DateRange, SitemapEntry
from news_collector.utils.http import fetch_text

log = logging.getLogger(__name__)

# Below this length a company's first word (e.g. "3M", "AT&T") is too short
# to be a reliable standalone match token, so fall back to the full name.
_MIN_TOKEN_LEN = 3


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
    - url_matches_company → False only when the article is classed `reject`
      for company/ticker (see `classify_match`)

    Matching is done once, in `data_mining.matching` (PLAN Work item 10, T-055),
    and reached through `classify_match`; connectors no longer carry their own
    ticker/first-word rule.
    """

    def __init__(
        self,
        config: ConnectorConfig,
        client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self._client = client
        self._semaphore = asyncio.Semaphore(max(1, int(config.requests_per_second)))

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
    async def sitemap_urls(self, date_range: DateRange) -> AsyncIterator[SitemapEntry]:
        """
        Async-generate SitemapEntry objects for all article URLs available
        via sitemap/RSS/API that fall within date_range.

        Postconditions:
        - Only yields entries whose lastmod is within date_range (or None)
        - Yields nothing when config.sitemap_roots and rss_feeds are both empty
        """
        # Unreachable, but its mere presence tells mypy this is an async
        # generator (matching every real override below, which use the same
        # `return` / `yield` pair), not a coroutine that returns an
        # AsyncIterator.
        return
        yield

    @abstractmethod
    def is_article_url(self, url: str) -> bool:
        """
        Return True if url points to a news article (not a tag/category page).

        Postconditions:
        - Idempotent for the same url
        - Never returns True for the bare domain root
        """

    @abstractmethod
    def url_matches_company(self, url: str, title: str, company: str, ticker: str) -> bool:
        """
        Return False only when the article is certainly not about company/ticker.

        Postconditions:
        - Equals `classify_match(...) != "reject"`: `accept` and `review` pairs are
          kept (a `review` pair has only weak evidence and is judged downstream, never
          excluded here)
        - A ticker never matches as a raw URL substring, and an ordinary-word ticker,
          a generic first word or an abbreviation alone is never `accept` (a cashtag
          `$ON` is `review`; only an exchange form such as `NYSE: ON` is `accept`)
        - Only the URL path/query and the title are read, never the host
        """

    def classify_match(self, url: str, title: str, company: str, ticker: str) -> MatchClass:
        """Class a `(url, ticker)` pair `accept` / `review` / `reject` (shared rule)."""
        return classify(url, title, company, ticker).cls

    def explain_match(self, url: str, title: str, company: str, ticker: str) -> Match:
        """Like `classify_match` but also returns the signal that decided the class."""
        return classify(url, title, company, ticker)

    # ------------------------------------------------------------------
    # Shared helpers available to all connectors
    # ------------------------------------------------------------------

    async def _fetch(self, url: str) -> str:
        """Rate-limited HTTP GET returning response text."""
        async with self._semaphore:
            return await fetch_text(self._client, url, extra_headers=self.config.headers)

    def _company_pattern(self, company: str, ticker: str) -> re.Pattern[str]:
        """Compile the legacy ``ticker OR first word`` regex.

        No longer used for matching (see `classify_match`); kept because
        `scripts/audit_ticker_tagging.py` reuses it to reproduce what the old rule
        accepted.
        """
        # Use the first word of company name if it's long enough (>3 chars)
        first_word = company.split(maxsplit=1)[0] if company.split() else company
        token = first_word if len(first_word) > _MIN_TOKEN_LEN else company
        # Escape for regex safety
        escaped_name = re.escape(token)
        escaped_ticker = re.escape(ticker)
        return re.compile(
            rf"\b({escaped_ticker}|{escaped_name})\b",
            re.IGNORECASE,
        )
