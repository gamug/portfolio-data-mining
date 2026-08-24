"""StockTwits connector — official public REST API primary, DDG fallback."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime

import httpx

from news_collector.connectors.base import BaseConnector, ConnectorConfig
from news_collector.models import DateRange, DiscoveredURL, SitemapEntry
from news_collector.utils.url import normalize_url

log = logging.getLogger(__name__)

# StockTwits article/post URL pattern
_POST_RE = re.compile(r"https?://(?:www\.)?stocktwits\.com/[a-zA-Z0-9_]+/message/\d+")
# API endpoint for symbol stream
_API_BASE = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
_MAX_PAGES = 30  # safeguard: 30 pages x 30 messages = 900 messages per ticker


class StockTwitsConnector(BaseConnector):
    """
    StockTwits strategy:
    - Primary: Official public REST API (no auth required for public streams)
      Endpoint: GET /api/2/streams/symbol/{ticker}.json?max={cursor}
    - Fallback: DDG site:stocktwits.com
    Note: API returns messages (twits) not traditional articles. We treat message
          URLs as "articles" for the purpose of this pipeline.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(
            config=ConnectorConfig(
                domain="stocktwits.com",
                requests_per_second=0.5,  # StockTwits is rate-sensitive
                ddg_site_filter="site:stocktwits.com",
                uses_official_api=True,
                sitemap_roots=[],
                rss_feeds=[],
            ),
            client=client,
        )

    def build_ddg_queries(self, company: str, ticker: str, date_range: DateRange) -> list[str]:
        return [
            f"site:stocktwits.com {ticker} {year}"
            for year in range(date_range.start.year, date_range.end.year + 1)
        ]

    async def sitemap_urls(self, date_range: DateRange) -> AsyncIterator[SitemapEntry]:
        return
        yield

    async def fetch_via_api(self, ticker: str, date_range: DateRange) -> list[DiscoveredURL]:
        """
        Paginate through the public StockTwits stream for a ticker.

        StockTwits API uses cursor-based pagination: each response contains a
        'cursor' dict with 'max' (oldest message id on current page).
        We iterate until we've gone past date_range.start or hit _MAX_PAGES.

        Returns list of DiscoveredURL with source="api".
        """
        url_template = _API_BASE.format(ticker=ticker)
        results: list[DiscoveredURL] = []
        cursor: int | None = None
        pages = 0

        while pages < _MAX_PAGES:
            params: dict = {"filter": "top"}
            if cursor:
                params["max"] = cursor

            try:
                await asyncio.sleep(1.0 / self.config.requests_per_second)
                resp = await self._client.get(url_template, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                log.warning("StockTwits API failed for %s page %d: %s", ticker, pages, exc)
                break

            messages: list[dict] = data.get("messages", [])
            if not messages:
                break

            page_results, reached_before_range = self._messages_to_urls(
                messages, ticker, date_range
            )
            results.extend(page_results)
            if reached_before_range:
                break

            # Advance cursor
            cursor_data = data.get("cursor", {})
            new_cursor = cursor_data.get("max")
            if not new_cursor or new_cursor == cursor:
                break
            cursor = new_cursor
            pages += 1

        log.info("StockTwits API fetched %d messages for %s", len(results), ticker)
        return results

    def _messages_to_urls(
        self, messages: list[dict], ticker: str, date_range: DateRange
    ) -> tuple[list[DiscoveredURL], bool]:
        """Convert one page of StockTwits messages to DiscoveredURLs.

        Returns (urls, reached_before_range) -- reached_before_range signals
        the caller to stop paginating once a message predates date_range.start.
        """
        results: list[DiscoveredURL] = []
        for msg in messages:
            created_at_str = msg.get("created_at", "")
            pub_date = None
            try:
                pub_dt = datetime.strptime(created_at_str, "%Y-%m-%dT%H:%M:%SZ")
                pub_date = pub_dt.date()
            except Exception:
                log.debug("Unparseable created_at %r, treating as undated", created_at_str)

            if pub_date and pub_date < date_range.start:
                return results, True

            if pub_date and not date_range.contains(pub_date):
                continue

            msg_id = msg.get("id")
            user = msg.get("user", {}).get("username", "unknown")
            msg_url = f"https://stocktwits.com/{user}/message/{msg_id}"

            results.append(
                DiscoveredURL(
                    url=normalize_url(msg_url),
                    domain="stocktwits.com",
                    company="",  # filled by orchestrator
                    ticker=ticker,
                    source="api",
                    discovered_at=datetime.utcnow(),
                    pub_date=pub_date,
                    title=msg.get("body", "")[:200] or None,
                )
            )
        return results, False

    def is_article_url(self, url: str) -> bool:
        return bool(_POST_RE.match(url))

    def url_matches_company(self, url: str, title: str, company: str, ticker: str) -> bool:
        # StockTwits messages are already ticker-specific from the API
        if ticker.lower() in url.lower():
            return True
        pattern = self._company_pattern(company, ticker)
        return self._matches_any(pattern, title)
