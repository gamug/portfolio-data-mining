"""Sitemap / RSS / Wayback CDX URL discovery strategy."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import date, datetime
from xml.etree import ElementTree as ET

import feedparser  # type: ignore
import httpx
from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from news_collector.connectors.base import BaseConnector
from news_collector.models import DateRange, DiscoveredURL, SitemapEntry
from news_collector.utils.http import fetch_text
from news_collector.utils.url import is_valid_http_url, normalize_url

log = logging.getLogger(__name__)

# XML namespace constants
_NS_SITEMAP = "http://www.sitemaps.org/schemas/sitemap/0.9"
_NS_NEWS = "http://www.google.com/schemas/sitemap-news/0.9"

# Wayback CDX API rows are [original, timestamp, statuscode]; we only read
# the first two fields, so anything shorter than that is unusable.
_MIN_CDX_ROW_FIELDS = 2


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
                    e for e in feed_entries if e.lastmod is None or date_range.contains(e.lastmod)
                )
            except Exception as exc:
                log.warning("RSS feed failed %s: %s", feed_url, exc)

        # 3. Optional Wayback CDX fallback
        if self._connector.config.wayback_fallback:
            try:
                cdx_entries = await self._wayback_cdx(self._connector.config.domain, date_range)
                entries.extend(cdx_entries)
            except Exception as exc:
                log.warning("Wayback CDX failed for %s: %s", self._connector.config.domain, exc)

        # 4. Deduplicate and filter by company relevance
        results = self._dedupe_and_filter(entries, company, ticker)

        log.info(
            "Sitemap/RSS discovered %d URLs for %s/%s",
            len(results),
            ticker,
            self._connector.config.domain,
        )
        return results

    def _dedupe_and_filter(
        self, entries: list[SitemapEntry], company: str, ticker: str
    ) -> list[DiscoveredURL]:
        """Normalize/dedupe raw sitemap+RSS+CDX entries, drop non-article URLs,
        and convert what's left to DiscoveredURL with its company-match class (a
        `reject` pair is kept, stored as skipped by the queue, T-056c)."""
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
            match = self._connector.explain_match(url, entry.title or "", company, ticker)
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
                    match_class=match.cls,
                    match_reason=match.reason,
                )
            )
        return results

    # ------------------------------------------------------------------
    # Sitemap traversal
    # ------------------------------------------------------------------

    async def _crawl_sitemaps(self, roots: list[str], date_range: DateRange) -> list[SitemapEntry]:
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
                # defusedxml guards against XXE/entity-expansion attacks --
                # sitemap XML is fetched from whatever domain is being
                # scraped, so it's untrusted input.
                root = DefusedET.fromstring(text)
            except (ET.ParseError, DefusedXmlException) as exc:
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
                t = item["published_parsed"]
                with contextlib.suppress(Exception):
                    pub_date = date(t.tm_year, t.tm_mon, t.tm_mday)
            entry = SitemapEntry(
                url=link,
                lastmod=pub_date,
                title=item.get("title"),
            )
            entry._from_rss = True  # type: ignore[attr-defined]
            entries.append(entry)
        return entries

    async def _wayback_cdx(self, domain: str, date_range: DateRange) -> list[SitemapEntry]:
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
        try:
            rows = json.loads(text)
        except json.JSONDecodeError:
            return []

        entries: list[SitemapEntry] = []
        for row in rows[1:]:  # first row is header
            if len(row) < _MIN_CDX_ROW_FIELDS:  # need at least [original, timestamp]
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
        return await fetch_text(self._client, url)


# ------------------------------------------------------------------
# XML parsing helpers
# ------------------------------------------------------------------


def _ns(tag: str) -> str:
    """Wrap tag with sitemap namespace."""
    return f"{{{_NS_SITEMAP}}}{tag}"


def _parse_sitemap_index(root: ET.Element, date_range: DateRange) -> list[str]:
    """Return child sitemap URLs from a <sitemapindex>; skip by lastmod if possible."""
    urls: list[str] = []
    unparseable: list[str] = []
    for sitemap_el in root.iter(_ns("sitemap")):
        loc_el = sitemap_el.find(_ns("loc"))
        if loc_el is None or not loc_el.text:
            continue
        lastmod_el = sitemap_el.find(_ns("lastmod"))
        if lastmod_el is not None and lastmod_el.text:
            lm = _parse_date(lastmod_el.text)
            if lm is None:
                unparseable.append(lastmod_el.text.strip())
            elif lm < date_range.start:
                continue  # entire child sitemap is too old
        urls.append(loc_el.text.strip())
    if unparseable:
        log.warning(
            "%d child sitemaps with an unparseable lastmod were kept (first: %r)",
            len(unparseable),
            unparseable[0],
        )
    return urls


def _parse_sitemap_leaf(root: ET.Element, date_range: DateRange) -> list[SitemapEntry]:
    """Return SitemapEntry list from a <urlset> leaf sitemap."""
    entries: list[SitemapEntry] = []
    unparseable: list[str] = []
    for url_el in root.iter(_ns("url")):
        loc_el = url_el.find(_ns("loc"))
        if loc_el is None or not loc_el.text:
            continue
        loc = loc_el.text.strip()

        lastmod: date | None = None
        lastmod_el = url_el.find(_ns("lastmod"))
        if lastmod_el is not None and lastmod_el.text:
            lastmod = _parse_date(lastmod_el.text)
            if lastmod is None:
                unparseable.append(lastmod_el.text.strip())

        if lastmod and not date_range.contains(lastmod):
            continue

        # Optional Google News title
        title: str | None = None
        news_title_el = url_el.find(f"{{{_NS_NEWS}}}news/{{{_NS_NEWS}}}title")
        if news_title_el is not None and news_title_el.text:
            title = news_title_el.text.strip()

        entries.append(SitemapEntry(url=loc, lastmod=lastmod, title=title))
    if unparseable:
        # kept (a date we cannot read cannot exclude an entry), but not silently
        log.warning(
            "%d sitemap entries with an unparseable lastmod were kept (first: %r)",
            len(unparseable),
            unparseable[0],
        )
    return entries


def _parse_date(text: str) -> date | None:
    """Parse an ISO-8601 date (`YYYY-MM-DD`, `YYYY-MM`, or a datetime with `Z` or an offset).

    The date is the one written in the text, not converted to UTC. Returns None for anything else.
    `datetime.fromisoformat` (Python 3.11+) also takes `20261018` and `2026-W42-7`; a bare year is None.
    (An earlier version cut the text to the length of the *format string*, so every real value failed.)
    """
    text = text.strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%Y-%m").date()
    except ValueError:
        return None
