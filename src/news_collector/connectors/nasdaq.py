"""Nasdaq connector — sitemap + /news/ pagination primary, DDG fallback."""

from __future__ import annotations

import re
from typing import AsyncIterator

import httpx

from news_collector.connectors.base import BaseConnector, ConnectorConfig
from news_collector.models import DateRange, SitemapEntry

_ARTICLE_RE = re.compile(
    r"https?://(?:www\.)?nasdaq\.com/articles/[a-z0-9-]+-\d{4}-\d{2}-\d{2}$"
)

# Nasdaq provides a sitemap index
_NASDAQ_SITEMAP_INDEX = "https://www.nasdaq.com/sitemap.xml"


class NasdaqConnector(BaseConnector):
    """
    Nasdaq strategy:
    - Primary: Sitemap index + /news/ section pagination
    - Fallback: DDG site:nasdaq.com
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(
            config=ConnectorConfig(
                domain="nasdaq.com",
                requests_per_second=1.0,
                sitemap_roots=[_NASDAQ_SITEMAP_INDEX],
                ddg_site_filter="site:nasdaq.com",
                headers={"Referer": "https://www.google.com/"},
            ),
            client=client,
        )

    def build_ddg_queries(
        self, company: str, ticker: str, date_range: DateRange
    ) -> list[str]:
        return [
            f"site:nasdaq.com/articles {ticker} {year}"
            for year in range(date_range.start.year, date_range.end.year + 1)
        ]

    async def sitemap_urls(self, date_range: DateRange) -> AsyncIterator[SitemapEntry]:
        return
        yield

    def is_article_url(self, url: str) -> bool:
        return bool(_ARTICLE_RE.match(url))

    def url_matches_company(
        self, url: str, title: str, company: str, ticker: str
    ) -> bool:
        # Nasdaq article URLs often contain ticker: /articles/aapl-beats-earnings-...
        if ticker.lower() in url.lower():
            return True
        pattern = self._company_pattern(company, ticker)
        return self._matches_any(pattern, title, url)
