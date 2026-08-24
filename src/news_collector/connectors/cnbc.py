"""CNBC connector — sitemap primary, DDG fallback."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx

from news_collector.connectors.base import BaseConnector, ConnectorConfig
from news_collector.models import DateRange, SitemapEntry

_ARTICLE_RE = re.compile(r"https?://(?:www\.)?cnbc\.com/\d{4}/\d{2}/\d{2}/[a-z0-9-]+\.html$")


class CNBCConnector(BaseConnector):
    """
    CNBC strategy:
    - Primary: sitemap at https://www.cnbc.com/sitemapAll.xml (sitemap index)
    - Fallback: DDG site:cnbc.com
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(
            config=ConnectorConfig(
                domain="cnbc.com",
                requests_per_second=1.0,
                sitemap_roots=["https://www.cnbc.com/sitemapAll.xml"],
                ddg_site_filter="site:cnbc.com",
                headers={"Referer": "https://www.google.com/"},
            ),
            client=client,
        )

    def build_ddg_queries(self, company: str, ticker: str, date_range: DateRange) -> list[str]:
        base = f"site:cnbc.com {ticker}"
        year_queries = [
            f"{base} {year}" for year in range(date_range.start.year, date_range.end.year + 1)
        ]
        # Also search by company name for broader coverage
        name_query = f"site:cnbc.com {company} stock news"
        return [*year_queries, name_query]

    async def sitemap_urls(self, date_range: DateRange) -> AsyncIterator[SitemapEntry]:
        # Delegated to SitemapRSSStrategy; this stub satisfies the ABC
        # (strategy reads config.sitemap_roots directly)
        return
        yield  # make this an async generator

    def is_article_url(self, url: str) -> bool:
        return bool(_ARTICLE_RE.match(url))

    def url_matches_company(self, url: str, title: str, company: str, ticker: str) -> bool:
        pattern = self._company_pattern(company, ticker)
        return self._matches_any(pattern, url, title)
