"""Sitemap / RSS / Wayback CDX URL discovery strategy."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import AsyncIterator
from xml.etree import ElementTree as ET

import httpx

from news_collector.connectors.base import BaseConnector
from news_collector.models import DateRange, DiscoveredURL, SitemapEntry
from news_collector.utils.url import normalize_url, is_valid_http_url

log = logging.getLogger(__name__)

# XML namespace constants
_NS_SITEMAP = "http://www.sitemaps.org/schemas/sitemap/0.9"
_NS_NEWS = "http://www.google.com/schemas/sitemap-news/0.9"


class SitemapRSSStrategy:
    """
    Discover article URLs from sitemap XML trees, RSS/Atom feeds, and the
    Wayback Machine CDX API (optional tertiary source).
    """

    def __init__(
        self,
        connector: BaseConnector,
        client: httpx.AsyncClient,
    ) -> None:
        self._connector = connector
        self._client = client

    async def discover(
        self,
        company: str,
        ticker: str,
        date_range: DateRange,
    ) -> list[DiscoveredURL]:
        """
        Enumerate article URLs from all configured sitemap/RSS sources.

        Preconditions:
        - connector.config has sitemap_roots or rss_feeds (otherwise returns [])
        Postconditions:
        - All returned URLs have source in {"sitemap", "rss", "api"}
        - pub_date set where available
        Loop Invariants:
        - visited_sitemaps grows; each sitemap URL processed exactly once
        """
        entries: list[SitemapEntry] = []

        # 1. Crawl sitemap trees
        if self._connector.config.sitemap_roots:
            entries.extend(
                await self._crawl_sitemaps(self._connector.config.sitemap_roots, date_range)
            )

        # 2. Parse RSS feeds
        for feed_url in self._connector.config.rss_feeds:
            try:
                feed_entries = await self._parse_rss(feed_url)
                entries.extend(
                    e for e in feed_entries
                    if e.lastmod is None or date_range.contains(e.lastmod)
                )
            except Exception as exc:
                log.warning("RSS feed failed %s: %s", feed_url, exc)

        # 3. Optional Wayback CDX fallback
        if self._connector.config.wayback_fallback:
            try:
                cdx_entries = await self._wayback_cdx(
                    self._connector.config.domain, date_range
                )
                entries.extend(cdx_entries)
            except Exception as exc:
                log.warning(
                    "Wayback CDX failed for %s: %s", self._connector.config.domain, exc
                )

        # 4. Deduplicate and filter by company relevance
        seen: set[str] = set()
        results: list[DiscoveredURL] = []
        for entry in entries:
            if not is_valid_http_url(entry.url):
                continue
            url = normalize_url(entry.url)
            if url in seen:
                continue
            if not self._connector.is_article_url(url):
                continue
            if not self._connector.url_matches_company(
                url, entry.title or "", company, ticker
            ):
                continue
            seen.add(url)
            src = "sitemap" if not getattr(entry, "_from_rss", False) else "rss"
            results.append(
                DiscoveredURL(
                    url=url,
                    domain=self._connector.config.domain,
                    company=company,
                    ticker=ticker,
                    source=src,  # type: ignore[arg-type]
                    discovered_at=datetime.utcnow(),
                    pub_date=entry.lastmod,
                    title=entry.title,
                )
            )

        log.info(
            "Sitemap/RSS discovered %d URLs for %s/%s",
            len(results),
            ticker,
            self._connector.config.domain,
        )
        return results

    # ------------------------------------------------------------------
    # Sitemap traversal
    # ------------------------------------------------------------------

    async def _crawl_sitemaps(
        self, roots: list[str], date_range: DateRange
    ) -> list[SitemapEntry]:
        """BFS over sitemap index tree; collect leaf entries within date range."""
        visited: set[str] = set()
        pending: list[str] = list(roots)
        entries: list[SitemapEntry] = []

        while pending:
            url = pending.pop(0)
            if url in visited:
                continue
            visited.add(url)

            try:
                text = await self._fetch(url)
            except Exception as exc:
                log.warning("Sitemap fetch failed %s: %s", url, exc)
                continue

            try:
                root = ET.fromstring(text)
            except ET.ParseError as exc:
                log.warning("Sitemap XML parse error %s: %s", url, exc)
                continue

            tag = root.tag.lower()
            if "sitemapindex" in tag:
                # Sitemap index — expand child sitemaps
                child_urls = _parse_sitemap_index(root, date_range)
                pending.extend(u for u in child_urls if u not in visited)
            else:
                # Leaf sitemap — collect article entries
                entries.extend(_parse_sitemap_leaf(root, date_range))

        return entries

    async def _parse_rss(self, feed_url: str) -> list[SitemapEntry]:
        """Parse RSS 2.0 or Atom feed and return entries."""
        import feedparser  # type: ignore

        text = await self._fetch(feed_url)
        # feedparser is synchronous; run in executor for large feeds
        loop = asyncio.get_event_loop()
        parsed = await loop.run_in_executor(None, feedparser.parse, text)

        entries: list[SitemapEntry] = []
        for item in parsed.get("entries", []):
            link = item.get("link") or ""
            if not link:
                continue
            pub_date: date | None = None
            if item.get("published_parsed"):
                import time
                t = item["published_parsed"]
                try:
                    pub_date = date(t.tm_year, t.tm_mon, t.tm_mday)
                except Exception:
                    pass
            entry = SitemapEntry(
                url=link,
                lastmod=pub_date,
                title=item.get("title"),
            )
            entry._from_rss = True  # type: ignore[attr-defined]
            entries.append(entry)
        return entries

    async def _wayback_cdx(
        self, domain: str, date_range: DateRange
    ) -> list[SitemapEntry]:
        """
        Query Wayback Machine CDX API for snapshots of a domain within date range.
        Returns SitemapEntry list with source metadata.
        """
        from_str = date_range.start.strftime("%Y%m%d")
        to_str = date_range.end.strftime("%Y%m%d")
        cdx_url = (
            f"https://web.archive.org/cdx/search/cdx"
            f"?url={domain}/*"
            f"&output=json"
            f"&fl=original,timestamp,statuscode"
            f"&filter=statuscode:200"
            f"&from={from_str}&to={to_str}"
            f"&collapse=urlkey"
            f"&limit=5000"
        )
        text = await self._fetch(cdx_url)
        import json

        try:
            rows = json.loads(text)
        except json.JSONDecodeError:
            return []

        entries: list[SitemapEntry] = []
        for row in rows[1:]:  # first row is header
            if len(row) < 2:
                continue
            original_url = row[0]
            timestamp = row[1]
            try:
                pub_date = date(int(timestamp[:4]), int(timestamp[4:6]), int(timestamp[6:8]))
            except (ValueError, IndexError):
                pub_date = None
            entries.append(SitemapEntry(url=original_url, lastmod=pub_date))
        return entries

    async def _fetch(self, url: str) -> str:
        from news_collector.utils.http import fetch_text

        return await fetch_text(self._client, url)


# ------------------------------------------------------------------
# XML parsing helpers
# ------------------------------------------------------------------


def _ns(tag: str) -> str:
    """Wrap tag with sitemap namespace."""
    return f"{{{_NS_SITEMAP}}}{tag}"


def _parse_sitemap_index(
    root: ET.Element, date_range: DateRange
) -> list[str]:
    """Return child sitemap URLs from a <sitemapindex>; skip by lastmod if possible."""
    urls: list[str] = []
    for sitemap_el in root.iter(_ns("sitemap")):
        loc_el = sitemap_el.find(_ns("loc"))
        if loc_el is None or not loc_el.text:
            continue
        lastmod_el = sitemap_el.find(_ns("lastmod"))
        if lastmod_el is not None and lastmod_el.text:
            try:
                lm = _parse_date(lastmod_el.text.strip())
                if lm and lm < date_range.start:
                    continue  # entire child sitemap is too old
            except Exception:
                pass
        urls.append(loc_el.text.strip())
    return urls


def _parse_sitemap_leaf(
    root: ET.Element, date_range: DateRange
) -> list[SitemapEntry]:
    """Return SitemapEntry list from a <urlset> leaf sitemap."""
    entries: list[SitemapEntry] = []
    for url_el in root.iter(_ns("url")):
        loc_el = url_el.find(_ns("loc"))
        if loc_el is None or not loc_el.text:
            continue
        loc = loc_el.text.strip()

        lastmod: date | None = None
        lastmod_el = url_el.find(_ns("lastmod"))
        if lastmod_el is not None and lastmod_el.text:
            try:
                lastmod = _parse_date(lastmod_el.text.strip())
            except Exception:
                pass

        if lastmod and not date_range.contains(lastmod):
            continue

        # Optional Google News title
        title: str | None = None
        news_title_el = url_el.find(f"{{{_NS_NEWS}}}news/{{{_NS_NEWS}}}title")
        if news_title_el is not None and news_title_el.text:
            title = news_title_el.text.strip()

        entries.append(SitemapEntry(url=loc, lastmod=lastmod, title=title))
    return entries


def _parse_date(text: str) -> date | None:
    """Parse ISO-8601 date string (YYYY-MM-DD or YYYY-MM-DDThh:mm:ssZ)."""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m"):
        try:
            dt = datetime.strptime(text[:len(fmt)], fmt)
            return dt.date()
        except ValueError:
            continue
    return None
