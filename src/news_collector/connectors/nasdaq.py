"""Nasdaq connector — DDG only (no sitemap, T-076)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx

from news_collector.connectors.base import BaseConnector, ConnectorConfig
from news_collector.models import DateRange, SitemapEntry

_ARTICLE_RE = re.compile(r"https?://(?:www\.)?nasdaq\.com/articles/[a-z0-9-]+-\d{4}-\d{2}-\d{2}$")


class NasdaqConnector(BaseConnector):
    """
    Nasdaq strategy:
    - DDG site:nasdaq.com (the sitemap child /articles/sitemap.xml answers 403, so
      `sitemap_roots` is empty, T-076)
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(
            config=ConnectorConfig(
                domain="nasdaq.com",
                requests_per_second=1.0,
                sitemap_roots=[],  # the /articles/sitemap.xml child answers 403, /education/sitemap.xml 404 (2026-10-18, T-076)
                ddg_site_filter="site:nasdaq.com",
                headers={"Referer": "https://www.google.com/"},
            ),
            client=client,
        )

    def build_ddg_queries(self, company: str, ticker: str, date_range: DateRange) -> list[str]:
        return [
            f"site:nasdaq.com/articles {ticker} {year}"
            for year in range(date_range.start.year, date_range.end.year + 1)
        ]

    async def sitemap_urls(self, date_range: DateRange) -> AsyncIterator[SitemapEntry]:
        return
        yield

    def is_article_url(self, url: str) -> bool:
        return bool(_ARTICLE_RE.match(url))

    def url_matches_company(self, url: str, title: str, company: str, ticker: str) -> bool:
        return self.classify_match(url, title, company, ticker) != "reject"
