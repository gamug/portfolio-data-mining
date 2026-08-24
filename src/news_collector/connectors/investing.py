"""Investing.com connector — DDG only (heavy Cloudflare / JS protection)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx

from news_collector.connectors.base import BaseConnector, ConnectorConfig
from news_collector.models import DateRange, SitemapEntry

# investing.com article URL patterns
_ARTICLE_RE = re.compile(
    r"https?://(?:www\.)?investing\.com/news/(?:stock-market-news|economy|forex-news|"
    r"commodities-news|crypto-news)/[a-z0-9-]+-\d+$"
)


class InvestingConnector(BaseConnector):
    """
    Investing.com strategy:
    - Primary: DDG site:investing.com
    - No sitemap crawl (site uses heavy JS rendering + Cloudflare protection)
    Note: Direct HTTP requests to investing.com will be blocked by Cloudflare.
          DDG has already indexed the content so search-based discovery is viable.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(
            config=ConnectorConfig(
                domain="investing.com",
                requests_per_second=1.0,
                ddg_site_filter="site:investing.com",
                # No sitemap — Cloudflare blocks crawlers
                sitemap_roots=[],
                rss_feeds=[],
            ),
            client=client,
        )

    def build_ddg_queries(self, company: str, ticker: str, date_range: DateRange) -> list[str]:
        queries = []
        for year in range(date_range.start.year, date_range.end.year + 1):
            queries.append(f"site:investing.com/news {ticker} {year}")
            queries.append(f'site:investing.com/news "{company}" {year}')
        return queries

    async def sitemap_urls(self, date_range: DateRange) -> AsyncIterator[SitemapEntry]:
        # No sitemap available
        return
        yield  # makes this an async generator (required by ABC)
        yield

    def is_article_url(self, url: str) -> bool:
        return bool(_ARTICLE_RE.match(url))

    def url_matches_company(self, url: str, title: str, company: str, ticker: str) -> bool:
        pattern = self._company_pattern(company, ticker)
        return self._matches_any(pattern, title, url)
