"""Financial Times connector — DDG primary, sitemap secondary, Wayback CDX tertiary."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx

from news_collector.connectors.base import BaseConnector, ConnectorConfig
from news_collector.models import DateRange, SitemapEntry

# FT article URLs: https://www.ft.com/content/<uuid>
_ARTICLE_RE = re.compile(r"https?://(?:www\.)?ft\.com/content/[0-9a-f-]{36}$")

# FT sitemap index
_FT_SITEMAP_INDEX = "https://www.ft.com/sitemap-index.xml"


class FTConnector(BaseConnector):
    """
    Financial Times strategy:
    - Primary: DDG site:ft.com (circumvents paywall for link discovery)
    - Secondary: Sitemap index at sitemap-index.xml
    - Tertiary: Wayback Machine CDX API
    Note: Article content is paywalled — this connector discovers URLs only.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(
            config=ConnectorConfig(
                domain="ft.com",
                requests_per_second=0.5,  # conservative — FT has rate limiting
                sitemap_roots=[_FT_SITEMAP_INDEX],
                ddg_site_filter="site:ft.com",
                has_paywall=True,
                wayback_fallback=True,
                headers={
                    "Accept": "application/xml,text/xml,application/xhtml+xml,*/*",
                },
            ),
            client=client,
        )

    def build_ddg_queries(self, company: str, ticker: str, date_range: DateRange) -> list[str]:
        queries = []
        for year in range(date_range.start.year, date_range.end.year + 1):
            queries.append(f"site:ft.com {ticker} {year}")
            queries.append(f'site:ft.com "{company}" {year}')
        return queries

    async def sitemap_urls(self, date_range: DateRange) -> AsyncIterator[SitemapEntry]:
        return
        yield

    def is_article_url(self, url: str) -> bool:
        return bool(_ARTICLE_RE.match(url))

    def url_matches_company(self, url: str, title: str, company: str, ticker: str) -> bool:
        pattern = self._company_pattern(company, ticker)
        return self._matches_any(pattern, title, url)
