"""Fakes and helpers of the sitemap cache tests (T-070a): a fake site, a clock, builders."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock
from xml.sax.saxutils import escape

import httpx

from data_mining.matching import Match
from news_collector.api.schemas import DiscoverRequest
from news_collector.connectors.cnbc import CNBCConnector
from news_collector.models import DateRange
from news_collector.sitemap_cache import SitemapCache, SitemapRun
from news_collector.strategies.sitemap import SitemapRSSStrategy

DOMAIN = "example.test"
ROOT = "https://example.test/sitemap.xml"
INDEX_ROOT = "https://example.test/index.xml"
CHILD_A = "https://example.test/a.xml"
CHILD_B = "https://example.test/b.xml"
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
START = date(2022, 1, 1)
RANGE = DateRange(start=START, end=date(2026, 8, 10))
T0 = datetime(2026, 10, 1, 12, 0, 0)
DAYS_FRESH = 7
DAYS_PURGE = 90
THREE_CRAWLS = 3
TWO = 2
LOOP_TICKS = 3
MANY_ROWS = 30_000
FIVE = 5
RETRY_MINUTES = 5  # the default back-off


NEWS = 'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"'


def leaf(*items: tuple[str, str | None] | tuple[str, str | None, str]) -> str:
    """A urlset; an item with a third element carries a `news:title`."""
    urls = ""
    for item in items:
        url, lastmod = item[0], item[1]
        title = item[2] if len(item) > TWO else None
        urls += f"<url><loc>{escape(url)}</loc>"
        urls += f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        urls += f"<news:news><news:title>{escape(title)}</news:title></news:news>" if title else ""
        urls += "</url>"
    return f"<urlset {NS} {NEWS}>{urls}</urlset>"


def index(*children: str) -> str:
    return (
        f"<sitemapindex {NS}>"
        + "".join(f"<sitemap><loc>{c}</loc></sitemap>" for c in children)
        + "</sitemapindex>"
    )


A1, A2, A3 = (f"https://example.test/a-{i}" for i in (1, 2, 3))
OLD = "https://example.test/a-old"


class Site:
    """A fake sitemap: pages by URL, a count of fetches per URL, a URL that can start failing."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.fetches: dict[str, int] = {}
        self.down: set[str] = set()
        self.delay = 0.0  # seconds a fetch takes: a crawl that another process can overlap

    async def fetch(self, url: str) -> str:
        self.fetches[url] = self.fetches.get(url, 0) + 1
        await asyncio.sleep(self.delay)  # a real fetch yields: concurrent callers interleave here
        if url in self.down or url not in self.pages:
            raise httpx.HTTPStatusError("403", request=MagicMock(), response=MagicMock())
        return self.pages[url]

    def crawls(self) -> int:
        return self.fetches.get(ROOT, 0)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        self.now += timedelta(milliseconds=1)  # every call is a distinct instant
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


def make(db: str, site: Site, clock: Clock, roots: list[str] | None = None) -> SitemapCache:
    return SitemapCache(DOMAIN, roots or [ROOT], db, site.fetch, lambda _u: True, clock)


def rows(db: str, sql: str, *params: object) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def urls_of(entries: list) -> list[str]:
    return sorted(e.url for e in entries)


def read(run: SitemapRun, date_range: DateRange = RANGE) -> tuple[list[str], list[str]]:
    entries, failures = run.entries(date_range)
    return urls_of(entries), failures


def standard_site() -> Site:
    return Site({ROOT: leaf((A1, "2024-05-01"), (A2, "2023-01-01"), (OLD, "2019-01-01"))})


# --- one crawl per domain, in one process ---------------------------------------------------


def strategy_for(run: SitemapRun | None, site: Site) -> SitemapRSSStrategy:
    connector = MagicMock()
    connector.config.sitemap_roots = [ROOT]
    connector.config.rss_feeds = []
    connector.config.wayback_fallback = False
    connector.config.domain = DOMAIN
    connector.is_article_url.return_value = True
    connector.explain_match.return_value = Match("accept", "name")
    strategy = SitemapRSSStrategy(connector, MagicMock(), run)
    strategy._fetch = site.fetch  # type: ignore[method-assign]
    return strategy


def fake_connector(client: object | None = None) -> MagicMock:
    connector = MagicMock()
    connector.config.sitemap_roots = [ROOT]
    connector.config.rss_feeds = []
    connector.config.wayback_fallback = False
    connector.config.domain = DOMAIN
    connector.is_article_url.return_value = True
    connector.explain_match.return_value = Match("accept", "name")
    connector._client = client if client is not None else MagicMock()
    return connector


def request(db: str, *tickers: str, **kw: object) -> DiscoverRequest:
    return DiscoverRequest(
        tickers=list(tickers),
        company_names=[f"{t} Inc." for t in tickers],
        db_path=db,
        start_date=START,
        end_date=RANGE.end,
        **kw,  # type: ignore[arg-type]
    )


# --- titles and undated entries through the cache ---------------------------------------------------

CNBC_URL = "https://www.cnbc.com/2024/05/01/stock-picks-for-the-week.html"  # no company in the slug
CNBC_PLAIN = "https://www.cnbc.com/2024/05/02/stock-picks-for-the-month.html"
CNBC_UNDATED = "https://www.cnbc.com/2024/05/03/stock-picks-undated.html"
TITLE = "Apple Inc. reports record earnings"
ODD_TITLE = 'Café & Co\'s "Q4" <results> \u2014 \u6d4b\u8bd5'


def cnbc_strategy(site: Site, cache: SitemapRun | None) -> SitemapRSSStrategy:
    connector = CNBCConnector(MagicMock())
    connector.config.sitemap_roots = [ROOT]
    connector.config.rss_feeds = []
    connector.config.wayback_fallback = False
    strategy = SitemapRSSStrategy(connector, MagicMock(), cache)
    strategy._fetch = site.fetch  # type: ignore[method-assign]
    return strategy


def seen(pairs: list) -> list[tuple]:
    return sorted((p.url, p.match_class, p.match_reason, p.title, p.pub_date) for p in pairs)


def child_crawl(db: str, results: object) -> None:
    """The body of one of two real processes (T-070b): a slow crawl of a one-entry sitemap through
    `SitemapCache`, reporting (fetches made here, entries read, failures) on `results`."""
    fetches = 0
    page = leaf(("https://example.test/a-1", "2024-05-01"))

    async def fetch(_url: str) -> str:
        nonlocal fetches
        fetches += 1
        await asyncio.sleep(1.5)  # slow: the other process arrives while it runs
        return page

    async def main() -> tuple[int, int, int]:
        cache = SitemapCache("example.test", [ROOT], db, fetch, lambda _u: True, poll=0.05)
        run = await cache.begin(RANGE)
        return fetches, len(run.entries(RANGE)[0]), len(run.failures)

    results.put(asyncio.run(main()))  # type: ignore[attr-defined]
