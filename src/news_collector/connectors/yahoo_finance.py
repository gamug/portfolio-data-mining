"""Yahoo Finance connector — yfinance API primary, DDG fallback."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime

import httpx
import yfinance as yf

from news_collector.connectors.base import BaseConnector, ConnectorConfig
from news_collector.models import DateRange, DiscoveredURL, SitemapEntry
from news_collector.utils.url import normalize_url

log = logging.getLogger(__name__)

_ARTICLE_RE = re.compile(r"https?://(?:finance\.)?yahoo\.com/(?:news|m)/[a-z0-9/_-]+")


class YahooFinanceConnector(BaseConnector):
    """
    Yahoo Finance strategy:
    - Primary: yfinance Ticker.news (unofficial API, returns JSON with article links)
    - Fallback: DDG site:finance.yahoo.com
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(
            config=ConnectorConfig(
                domain="finance.yahoo.com",
                requests_per_second=2.0,
                ddg_site_filter="site:finance.yahoo.com",
                uses_official_api=False,
                # No sitemap; yfinance handles link discovery
                sitemap_roots=[],
                rss_feeds=[],
            ),
            client=client,
        )

    def build_ddg_queries(self, company: str, ticker: str, date_range: DateRange) -> list[str]:
        return [
            f"site:finance.yahoo.com {ticker} news {year}"
            for year in range(date_range.start.year, date_range.end.year + 1)
        ]

    async def sitemap_urls(self, date_range: DateRange) -> AsyncIterator[SitemapEntry]:
        # Yahoo Finance has no public sitemap; return nothing
        return
        yield  # makes this an async generator (required by ABC)
        yield

    async def fetch_via_yfinance(self, ticker: str, date_range: DateRange) -> list[DiscoveredURL]:
        """
        Use yfinance Ticker.news to get article links for the given ticker.

        yfinance returns a list of news items with keys: title, link, publisher,
        providerPublishTime, type, thumbnail, relatedTickers.

        Runs synchronous yfinance call in executor.
        """
        try:
            loop = asyncio.get_event_loop()
            news_items = await loop.run_in_executor(None, _sync_yfinance_news, ticker)
        except Exception as exc:
            log.warning("yfinance news failed for %s: %s", ticker, exc)
            return []

        results: list[DiscoveredURL] = []
        for item in news_items:
            link = item.get("link") or ""
            if not link:
                continue
            # Filter to date range using providerPublishTime (Unix timestamp)
            pub_ts = item.get("providerPublishTime")
            pub_date = None
            if pub_ts:
                try:
                    pub_date = datetime.utcfromtimestamp(pub_ts).date()
                    if not date_range.contains(pub_date):
                        continue
                except Exception:
                    log.debug("Unparseable providerPublishTime %r, treating as undated", pub_ts)

            results.append(
                DiscoveredURL(
                    url=normalize_url(link),
                    domain="finance.yahoo.com",
                    company="",  # filled by orchestrator
                    ticker=ticker,
                    source="api",
                    discovered_at=datetime.utcnow(),
                    pub_date=pub_date,
                    title=item.get("title"),
                )
            )
        return results

    def is_article_url(self, url: str) -> bool:
        return bool(_ARTICLE_RE.match(url))

    def url_matches_company(self, url: str, title: str, company: str, ticker: str) -> bool:
        # yfinance results are already ticker-specific; for DDG results use pattern
        if ticker.lower() in url.lower():
            return True
        pattern = self._company_pattern(company, ticker)
        return self._matches_any(pattern, title)


def _sync_yfinance_news(ticker: str) -> list[dict]:
    t = yf.Ticker(ticker)
    return t.news or []
