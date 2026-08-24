"""Seeking Alpha connector — DDG primary (aggressive bot protection)."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncIterator

import httpx

from news_collector.connectors.base import BaseConnector, ConnectorConfig
from news_collector.models import DateRange, SitemapEntry

log = logging.getLogger(__name__)

_ARTICLE_RE = re.compile(r"https?://(?:www\.)?seekingalpha\.com/article/\d+-[a-z0-9-]+")

# Optional: SeekingAlpha official RapidAPI endpoint (requires key)
_RAPIDAPI_HOST = "seeking-alpha.p.rapidapi.com"
_RAPIDAPI_BASE = "https://seeking-alpha.p.rapidapi.com"


class SeekingAlphaConnector(BaseConnector):
    """
    Seeking Alpha strategy:
    - Primary: DDG site:seekingalpha.com (bot protection makes direct crawl unreliable)
    - Optional: Official RapidAPI (set SEEKINGALPHA_RAPIDAPI_KEY env var to enable)
    Note: Direct crawling will result in 403/CAPTCHA. DDG indexing is reliable.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._rapidapi_key = os.environ.get("SEEKINGALPHA_RAPIDAPI_KEY", "")
        super().__init__(
            config=ConnectorConfig(
                domain="seekingalpha.com",
                requests_per_second=1.0,
                ddg_site_filter="site:seekingalpha.com",
                sitemap_roots=[],
                rss_feeds=[],
            ),
            client=client,
        )

    def build_ddg_queries(self, company: str, ticker: str, date_range: DateRange) -> list[str]:
        queries = []
        for year in range(date_range.start.year, date_range.end.year + 1):
            queries.append(f"site:seekingalpha.com/article {ticker} {year}")
        # Analysis articles often use company name
        queries.append(f'site:seekingalpha.com "{ticker}" analysis')
        return queries

    async def sitemap_urls(self, date_range: DateRange) -> AsyncIterator[SitemapEntry]:
        return
        yield

    async def fetch_via_rapidapi(
        self,
        ticker: str,
        date_range: DateRange,
        page: int = 1,
        per_page: int = 40,
    ) -> list[dict]:
        """
        Fetch article metadata from SeekingAlpha official RapidAPI.
        Requires SEEKINGALPHA_RAPIDAPI_KEY environment variable.
        Returns list of raw article dicts with 'id', 'title', 'publishOn', 'slug'.
        """
        if not self._rapidapi_key:
            return []

        url = f"{_RAPIDAPI_BASE}/analysis/v2/list"
        params: dict[str, str | int] = {
            "id": ticker.lower(),
            "until": int(
                date_range.end.strftime("%s") if hasattr(date_range.end, "strftime") else 0
            ),
            "since": 0,
            "size": per_page,
            "number": page,
        }
        headers = {
            "X-RapidAPI-Key": self._rapidapi_key,
            "X-RapidAPI-Host": _RAPIDAPI_HOST,
        }
        try:
            resp = await self._client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("RapidAPI failed for %s: %s", ticker, exc)
            return []
        else:
            result: list[dict] = data.get("data", [])
            return result

    def is_article_url(self, url: str) -> bool:
        return bool(_ARTICLE_RE.match(url))

    def url_matches_company(self, url: str, title: str, company: str, ticker: str) -> bool:
        if ticker.lower() in url.lower():
            return True
        pattern = self._company_pattern(company, ticker)
        return self._matches_any(pattern, title)
