"""The per-domain sitemap cache in one process (T-070a)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import time
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import httpx
import pytest

import news_collector.sitemap_cache as sc
from data_mining.db import connect
from data_mining.matching import Match
from news_collector.api.routers import _common
from news_collector.connectors.cnbc import CNBCConnector
from news_collector.models import Company, DateRange
from news_collector.orchestrator import DiscoveryOrchestrator
from news_collector.sitemap_cache import SitemapCache, shared_cache
from news_collector.storage.queue import URLQueue
from news_collector.strategies.sitemap import SitemapRSSStrategy
from news_collector.utils.url import is_valid_http_url, normalize_url

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
LOOP_TICKS = 5
MANY_ROWS = 30_000


def leaf(*items: tuple[str, str | None]) -> str:
    urls = "".join(
        f"<url><loc>{u}</loc>" + (f"<lastmod>{m}</lastmod>" if m else "") + "</url>"
        for u, m in items
    )
    return f"<urlset {NS}>{urls}</urlset>"


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

    async def fetch(self, url: str) -> str:
        self.fetches[url] = self.fetches.get(url, 0) + 1
        await asyncio.sleep(0)  # a real fetch yields: concurrent callers interleave here
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


@pytest.fixture
def db(tmp_path: Path) -> Iterator[str]:
    sc.reset_shared_caches()
    queue = URLQueue(str(tmp_path / "cache.db"))
    queue.initialize()
    queue.close()
    yield queue.db_path
    sc.reset_shared_caches()


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


async def read(cache: SitemapCache, date_range: DateRange = RANGE) -> tuple[list[str], list[str]]:
    entries, failures = await cache.entries(date_range)
    return urls_of(entries), failures


def standard_site() -> Site:
    return Site({ROOT: leaf((A1, "2024-05-01"), (A2, "2023-01-01"), (OLD, "2019-01-01"))})


# --- the pinned filter (moved with the code from the T-068 script) ------------------------------

# (URL, normalized or None when not a valid http URL, is it an article): pinned by CACHE_VERSION.
PINNED = [
    ("https://www.cnbc.com/2024/05/01/kla-corp-q4.html",
     "https://www.cnbc.com/2024/05/01/kla-corp-q4.html", True),
    ("https://WWW.CNBC.com/2024/05/01/kla-corp-q4.html/?utm_source=x",
     "https://www.cnbc.com/2024/05/01/kla-corp-q4.html", True),
    ("https://www.cnbc.com/2024/05/01/kla-corp-q4.html?&qsearchterm=kla",
     "https://www.cnbc.com/2024/05/01/kla-corp-q4.html?qsearchterm=kla", False),
    ("https://www.cnbc.com/markets/", "https://www.cnbc.com/markets", False),
    ("https://www.cnbc.com/quotes/KLAC", "https://www.cnbc.com/quotes/KLAC", False),
    ("https://www.cnbc.com/video/2024/05/01/some-clip.html",
     "https://www.cnbc.com/video/2024/05/01/some-clip.html", False),
    ("https://www.cnbc.com/id/100727362", "https://www.cnbc.com/id/100727362", False),
    ("https://www.cnbc.com/2024/05/01/", "https://www.cnbc.com/2024/05/01", False),
    ("https://www.cnbc.com/pro/2024/05/01/story.html",
     "https://www.cnbc.com/pro/2024/05/01/story.html", False),
    ("https://www.cnbc.com/2024/05/01/story.html#comments",
     "https://www.cnbc.com/2024/05/01/story.html", True),
    ("ftp://www.cnbc.com/2024/05/01/story.html", None, False),
    ("https://www.cnbc.com/2024/05/01/story.html/",
     "https://www.cnbc.com/2024/05/01/story.html", True),
]  # fmt: skip
PINNED_HASH = {
    1: "df39ae1d4b8d8e20b841cfee9802d80ac63a385168351253a9684c585a618bed"
}  # CACHE_VERSION -> sha256 of PINNED


def test_a_filter_change_needs_a_new_cache_version() -> None:
    connector = CNBCConnector(None)  # type: ignore[arg-type]
    for url, normalized, article in PINNED:
        got = normalize_url(url) if is_valid_http_url(url) else None
        assert got == normalized, url
        assert (got is not None and connector.is_article_url(got)) is article, url
    digest = hashlib.sha256(repr(PINNED).encode()).hexdigest()
    # Changing a result means editing PINNED, which changes the digest: add the new digest
    # under a bumped `CACHE_VERSION` (and the cached rows are re-crawled on the signature).
    assert PINNED_HASH[sc.CACHE_VERSION] == digest


def test_the_signature_follows_the_roots_and_the_version(monkeypatch: pytest.MonkeyPatch) -> None:
    base = sc.signature([ROOT, INDEX_ROOT])
    assert sc.signature([INDEX_ROOT, ROOT]) == base  # sorted
    assert sc.signature([ROOT]) != base
    assert sc.signature([ROOT, INDEX_ROOT, CHILD_A]) != base
    monkeypatch.setattr(sc, "CACHE_VERSION", sc.CACHE_VERSION + 1)
    assert sc.signature([ROOT, INDEX_ROOT]) != base


# --- one crawl per domain, in one process ---------------------------------------------------


def strategy_for(cache: SitemapCache | None, site: Site) -> SitemapRSSStrategy:
    connector = MagicMock()
    connector.config.sitemap_roots = [ROOT]
    connector.config.rss_feeds = []
    connector.config.wayback_fallback = False
    connector.config.domain = DOMAIN
    connector.is_article_url.return_value = True
    connector.explain_match.return_value = Match("accept", "name")
    strategy = SitemapRSSStrategy(connector, MagicMock(), cache)
    strategy._fetch = site.fetch  # type: ignore[method-assign]
    return strategy


async def test_two_concurrent_tickers_make_one_crawl(db: str) -> None:
    site = standard_site()
    cache = make(db, site, Clock())
    first, second = strategy_for(cache, site), strategy_for(cache, site)
    one, two = await asyncio.gather(
        first.discover("Acme", "ACME", RANGE), second.discover("Beta", "BETA", RANGE)
    )
    assert site.crawls() == 1
    assert sorted(u.url for u in one) == sorted(u.url for u in two) == sorted([A1, A2])
    assert cache.crawls == 1


async def test_two_api_requests_for_two_tickers_make_one_crawl(db: str) -> None:
    site = standard_site()
    client = MagicMock()
    connector = SimpleNamespace(
        config=SimpleNamespace(sitemap_roots=[ROOT], domain=DOMAIN), is_article_url=lambda _u: True
    )
    # the same cache for every request of the process: `begin` of two requests at once, then a
    # ticker each
    caches = [shared_cache(connector, client, db) for _ in range(2)]  # type: ignore[arg-type]
    assert caches[0] is caches[1]
    cache = cast(SitemapCache, caches[0])
    cache._fetch = site.fetch
    await asyncio.gather(cache.begin(RANGE), cache.begin(RANGE))
    await asyncio.gather(cache.entries(RANGE), cache.entries(RANGE))
    assert site.crawls() == 1


async def test_the_orchestrator_and_an_api_route_share_the_cache_of_the_process(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = standard_site()
    client = MagicMock()
    connector = MagicMock()
    connector.config.sitemap_roots = [ROOT]
    connector.config.rss_feeds = []
    connector.config.wayback_fallback = False
    connector.config.domain = DOMAIN
    connector.is_article_url.return_value = True
    connector.explain_match.return_value = Match("accept", "name")
    connector._client = client
    queue = URLQueue(db)
    orchestrator = DiscoveryOrchestrator(connectors={DOMAIN: connector}, queue=queue, concurrency=1)

    async def fetch_text(_client: object, url: str) -> str:
        return await site.fetch(url)

    monkeypatch.setattr(sc, "fetch_text", fetch_text)

    async def no_ddg(self: object, *_: object) -> list:
        return []

    monkeypatch.setattr(_common.DDGStrategy, "discover", no_ddg)
    monkeypatch.setattr("news_collector.orchestrator.DDGStrategy.discover", no_ddg)

    async def no_api(*_: object) -> list:
        return []

    orchestrator._fetch_via_extra_api = no_api  # type: ignore[method-assign]
    await orchestrator.run([Company(ticker="ACME", name="Acme")], date_range=RANGE)
    api_cache = shared_cache(connector, client, db)
    assert api_cache is orchestrator._caches[DOMAIN]
    errors = (
        await _common._discover_one_ticker(
            Company(ticker="BETA", name="Beta"), connector, client, RANGE, None, api_cache
        )
    )[3]
    assert errors == []
    assert site.crawls() == 1  # the route found the orchestrator's crawl: fresh, covering


async def test_no_cache_argument_keeps_the_live_fetch(db: str) -> None:
    site = standard_site()
    strategy = strategy_for(None, site)
    await strategy.discover("Acme", "ACME", RANGE)
    await strategy.discover("Beta", "BETA", RANGE)
    assert site.crawls() == TWO  # once per ticker, as before
    assert rows(db, "SELECT COUNT(*) FROM sitemap_crawls") == [(0,)]


async def test_the_pair_gets_the_cached_entries_inside_the_range(db: str) -> None:
    site = standard_site()
    cache = make(db, site, Clock())
    strategy = strategy_for(cache, site)
    found = await strategy.discover("Acme", "ACME", DateRange(start=START, end=date(2023, 6, 1)))
    assert sorted(u.url for u in found) == [A2]  # the run's end is applied when the rows are read
    assert strategy.failures == []
    assert rows(db, "SELECT url FROM sitemap_entries ORDER BY url") == [
        (A1,),
        (A2,),
    ]  # in range only


# --- the crawl and the table ---------------------------------------------------------------------


async def test_a_complete_crawl_is_stored_with_last_seen_equal_to_started_at(db: str) -> None:
    site = Site({ROOT: leaf((A1, "2024-05-01"), (A2, None))})
    await make(db, site, Clock()).begin(RANGE)
    [crawl] = rows(
        db,
        "SELECT started_at, finished_at, complete, range_start, signature, entries, failures "
        "FROM sitemap_crawls",
    )
    started, finished, complete, range_start, sig, entries, failures = crawl
    assert (complete, range_start, sig, entries, failures) == (
        1,
        "2022-01-01",
        sc.signature([ROOT]),
        2,
        0,
    )
    assert finished > started
    assert len(started) == len("2026-10-01T12:00:00.000000")  # microseconds, no offset
    assert {r[0] for r in rows(db, "SELECT last_seen FROM sitemap_entries")} == {started}
    assert rows(db, "SELECT first_seen FROM sitemap_entries")[0][0] == started


async def test_an_entry_missing_from_a_complete_crawl_stops_being_current_but_stays(
    db: str,
) -> None:
    site = standard_site()
    clock = Clock()
    cache = make(db, site, clock)
    await cache.begin(RANGE)
    site.pages[ROOT] = leaf((A1, "2024-05-01"), (A3, "2025-01-01"))  # A2 left, A3 is new
    clock.advance(days=1)
    await cache.begin(RANGE, force=True)
    assert (await read(cache))[0] == sorted([A1, A3])
    assert rows(db, "SELECT url FROM sitemap_entries WHERE url = ?", A2) == [(A2,)]
    seen = dict(rows(db, "SELECT url, last_seen FROM sitemap_entries"))
    assert seen[A1] == seen[A3] != seen[A2]
    first = dict(rows(db, "SELECT url, first_seen FROM sitemap_entries"))
    assert first[A1] == seen[A2]  # an upsert keeps `first_seen`


async def test_an_incomplete_crawl_changes_no_last_seen(db: str) -> None:
    site = Site(
        {
            INDEX_ROOT: index(CHILD_A, CHILD_B),
            CHILD_A: leaf((A1, "2024-05-01")),
            CHILD_B: leaf((A2, "2023-01-01")),
        }
    )
    clock = Clock()
    cache = make(db, site, clock, [INDEX_ROOT])
    await cache.begin(RANGE)
    before = rows(db, "SELECT url, lastmod, last_seen FROM sitemap_entries ORDER BY url")
    site.down.add(CHILD_B)
    site.pages[CHILD_A] = leaf((A1, "2025-02-02"), (A3, "2025-02-03"))
    clock.advance(days=DAYS_FRESH + 1)  # stale: a refresh runs and one child fails
    await cache.begin(RANGE)
    assert rows(db, "SELECT url, lastmod, last_seen FROM sitemap_entries ORDER BY url") == before
    latest = rows(
        db, "SELECT complete, failures FROM sitemap_crawls ORDER BY started_at DESC LIMIT 1"
    )
    assert latest == [(0, 1)]


async def test_a_failed_crawl_keeps_the_old_cache_and_a_fresh_one_is_used(db: str) -> None:
    site = standard_site()
    clock = Clock()
    cache = make(db, site, clock)
    await cache.begin(RANGE)
    site.down.add(ROOT)
    clock.advance(hours=1)
    outcome = await cache.begin(RANGE, force=True)  # (a) forced, failed, the cache is fresh
    assert outcome.crawl is not None
    assert await read(cache) == (sorted([A1, A2]), [])
    assert cache.crawls == TWO


async def test_a_failed_refresh_of_an_old_cache_is_used_with_a_warning_of_its_age(
    db: str, caplog: pytest.LogCaptureFixture
) -> None:
    site = standard_site()
    clock = Clock()
    cache = make(db, site, clock)
    await cache.begin(RANGE)
    site.down.add(ROOT)
    clock.advance(days=DAYS_FRESH + 3)
    with caplog.at_level(logging.WARNING):
        await cache.begin(RANGE)  # (b)
    assert await read(cache) == (sorted([A1, A2]), [])
    [warning] = [r.getMessage() for r in caplog.records if "refresh of" in r.getMessage()]
    assert "10.0 days old" in warning


async def test_a_failed_refresh_without_coverage_fails_the_pair_and_checkpoints_nothing(
    db: str,
) -> None:
    site = standard_site()
    clock = Clock()
    cache = make(db, site, clock)
    await cache.begin(DateRange(start=date(2023, 1, 1), end=RANGE.end))  # covers from 2023
    site.down.add(ROOT)
    outcome = await cache.begin(RANGE)  # (c) the run starts in 2022: the refresh fails
    assert outcome.crawl is None
    entries, failures = await cache.entries(RANGE)
    assert entries == []
    assert len(failures) == 1
    assert "does not cover 2022-01-01" in failures[0]
    assert ROOT in failures[0]


async def test_no_cache_and_a_failed_crawl_fails_the_pair(db: str) -> None:
    site = Site({})
    cache = make(db, site, Clock())
    strategy = strategy_for(cache, site)
    assert await strategy.discover("Acme", "ACME", RANGE) == []
    assert len(strategy.failures) == 1


async def test_every_ticker_of_a_failed_domain_carries_the_failure(db: str) -> None:
    site = Site({})
    cache = make(db, site, Clock())
    await cache.begin(RANGE)
    first, second = strategy_for(cache, site), strategy_for(cache, site)
    await asyncio.gather(first.discover("A", "A", RANGE), second.discover("B", "B", RANGE))
    assert len(first.failures) == len(second.failures) == 1
    assert site.crawls() == 1  # one crawl, a failure reported for each pair


async def test_one_failed_child_among_good_ones_keeps_the_old_cache(db: str) -> None:
    site = Site(
        {
            INDEX_ROOT: index(CHILD_A, CHILD_B),
            CHILD_A: leaf((A1, "2024-05-01")),
            CHILD_B: leaf((A2, "2023-01-01")),
        }
    )
    clock = Clock()
    cache = make(db, site, clock, [INDEX_ROOT])
    await cache.begin(RANGE)
    site.down.add(CHILD_B)
    clock.advance(hours=1)
    await cache.begin(RANGE, force=True)
    assert (await read(cache))[0] == sorted([A1, A2])  # the old crawl, not the half of the new one
    assert rows(db, "SELECT COUNT(*) FROM sitemap_crawls WHERE complete = 1") == [(1,)]


async def test_a_child_that_fails_every_time_keeps_the_crawl_incomplete(db: str) -> None:
    site = Site({INDEX_ROOT: index(CHILD_A, CHILD_B), CHILD_A: leaf((A1, "2024-05-01"))})
    clock = Clock()
    cache = make(db, site, clock, [INDEX_ROOT])
    for _ in range(THREE_CRAWLS):
        clock.advance(days=DAYS_FRESH + 1)
        await cache.begin(RANGE)
        assert (await cache.entries(RANGE))[0] == []
    assert rows(db, "SELECT COUNT(*) FROM sitemap_crawls WHERE complete = 1") == [(0,)]
    assert rows(db, "SELECT COUNT(*) FROM sitemap_entries") == [(0,)]  # no exception list


# --- when it refreshes -------------------------------------------------------------------------------


async def test_a_wider_range_refreshes_and_a_narrower_one_does_not(db: str) -> None:
    site = standard_site()
    clock = Clock()
    cache = make(db, site, clock)
    await cache.begin(DateRange(start=date(2023, 1, 1), end=RANGE.end))
    assert site.crawls() == 1
    await cache.begin(DateRange(start=date(2024, 1, 1), end=RANGE.end))  # narrower: covered
    assert site.crawls() == 1
    await cache.begin(RANGE)  # wider: the cache starts in 2023
    assert site.crawls() == TWO
    assert rows(db, "SELECT range_start FROM sitemap_crawls ORDER BY started_at DESC LIMIT 1") == [
        ("2022-01-01",)
    ]


async def test_a_refresh_after_a_narrower_run_keeps_the_wider_start(db: str) -> None:
    site = standard_site()
    clock = Clock()
    cache = make(db, site, clock)
    await cache.begin(RANGE)
    clock.advance(days=DAYS_FRESH + 1)
    await cache.begin(
        DateRange(start=date(2024, 1, 1), end=RANGE.end)
    )  # 7-day refresh, later start
    clock.advance(hours=1)
    await cache.begin(DateRange(start=date(2024, 1, 1), end=RANGE.end), force=True)  # forced
    starts = rows(db, "SELECT range_start FROM sitemap_crawls WHERE complete = 1")
    assert {s[0] for s in starts} == {"2022-01-01"}
    assert (await read(cache))[0] == sorted([A1, A2])


@pytest.mark.parametrize(("days", "refreshes"), [(DAYS_FRESH - 1, False), (DAYS_FRESH + 1, True)])
async def test_the_age_rule_is_seven_days_whatever_the_end(
    db: str, days: int, refreshes: bool
) -> None:
    site = standard_site()
    clock = Clock()
    cache = make(db, site, clock)
    await cache.begin(RANGE)
    clock.advance(days=days)
    await cache.begin(DateRange(start=START, end=date(2026, 10, 1)))  # an end near today
    assert (site.crawls() == TWO) is refreshes


async def test_the_clock_never_refreshes_in_the_middle_of_a_run(db: str) -> None:
    site = standard_site()
    clock = Clock()
    cache = make(db, site, clock)
    await cache.begin(RANGE)
    clock.advance(days=DAYS_FRESH * 3)  # the run goes on for weeks: no begin, no refresh
    await cache.entries(RANGE)
    await cache.entries(RANGE)
    assert site.crawls() == 1


async def test_a_changed_root_or_version_refreshes_and_a_failed_refresh_fails_the_pair(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = Site({ROOT: leaf((A1, "2024-05-01")), INDEX_ROOT: leaf((A3, "2025-01-01"))})
    clock = Clock()
    await make(db, site, clock).begin(RANGE)
    clock.advance(hours=1)
    wider = make(db, site, clock, [ROOT, INDEX_ROOT])  # a root was added
    await wider.begin(RANGE)
    assert (await read(wider))[0] == sorted([A1, A3])
    monkeypatch.setattr(sc, "CACHE_VERSION", sc.CACHE_VERSION + 1)  # a release changed the filter
    clock.advance(hours=1)
    bumped = make(db, site, clock, [ROOT, INDEX_ROOT])
    site.down.add(ROOT)
    await bumped.begin(RANGE)  # the cache is of the old rules and the refresh fails
    entries, failures = await bumped.entries(RANGE)
    assert entries == []
    assert len(failures) == 1


async def test_the_force_flag_crawls_and_two_forced_requests_make_one_crawl(db: str) -> None:
    site = standard_site()
    cache = make(db, site, Clock())
    await cache.begin(RANGE)
    assert site.crawls() == 1
    await cache.begin(RANGE)  # fresh: no crawl
    assert site.crawls() == 1
    await asyncio.gather(cache.begin(RANGE, force=True), cache.begin(RANGE, force=True))
    assert site.crawls() == TWO  # not three


async def test_a_refresh_by_a_second_connection_rebuilds_the_rows_of_the_first_process(
    db: str,
) -> None:
    site = standard_site()
    clock = Clock()
    mine = make(db, site, clock)
    await mine.begin(RANGE)
    assert (await read(mine))[0] == sorted([A1, A2])
    other_site = Site({ROOT: leaf((A1, "2024-05-01"), (A3, "2025-01-01"))})
    clock.advance(hours=1)
    await make(db, other_site, clock).begin(RANGE, force=True)  # another process refreshes
    await mine.begin(RANGE)  # the next run reads the latest crawl's identity and reloads
    assert (await read(mine))[0] == sorted([A1, A3])
    assert site.crawls() == 1


async def test_the_purge_deletes_old_cache_rows_and_keeps_the_latest_complete_crawl(
    db: str,
) -> None:
    site = standard_site()
    clock = Clock()
    cache = make(db, site, clock)
    await cache.begin(RANGE)
    old_start = rows(db, "SELECT started_at FROM sitemap_crawls")[0][0]
    site.pages[ROOT] = leaf((A1, "2024-05-01"), (A3, "2025-01-01"))
    clock.advance(days=DAYS_PURGE + 1)
    await cache.begin(RANGE)  # complete: A2 and OLD were last seen 91 days ago
    assert [r[0] for r in rows(db, "SELECT url FROM sitemap_entries ORDER BY url")] == sorted(
        [A1, A3]
    )
    assert rows(db, "SELECT COUNT(*) FROM sitemap_crawls WHERE started_at = ?", old_start) == [(0,)]
    assert rows(db, "SELECT COUNT(*) FROM sitemap_crawls WHERE complete = 1") == [(1,)]
    # a failing source for months does not purge the only complete crawl or its entries
    site.down.add(ROOT)
    clock.advance(days=DAYS_PURGE * 2)
    await cache.begin(RANGE)
    assert rows(db, "SELECT COUNT(*) FROM sitemap_entries") == [(TWO,)]
    assert rows(db, "SELECT COUNT(*) FROM sitemap_crawls WHERE complete = 1") == [(1,)]


async def test_the_purge_touches_the_cache_tables_only(db: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO discovered_urls (url, domain, company, ticker, source, discovered_at, status) "
        "VALUES (?, ?, 'Acme', 'ACME', 'sitemap', '2026-01-01', 'pending')",
        (A2, DOMAIN),
    )
    conn.commit()
    conn.close()
    site = standard_site()
    clock = Clock()
    cache = make(db, site, clock)
    await cache.begin(RANGE)
    site.pages[ROOT] = leaf((A1, "2024-05-01"))
    clock.advance(days=DAYS_PURGE + 1)
    await cache.begin(RANGE)
    assert rows(db, "SELECT COUNT(*) FROM sitemap_entries WHERE url = ?", A2) == [(0,)]
    assert rows(db, "SELECT COUNT(*) FROM discovered_urls") == [(1,)]


# --- the database and the event loop -----------------------------------------------------------------


async def test_the_cache_opens_its_connections_through_connect(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[dict] = []
    real = sc.connect

    def spy(path: str, **kw: object):
        opened.append(kw)
        return real(path, **kw)

    monkeypatch.setattr(sc, "connect", spy)
    await make(db, standard_site(), Clock()).begin(RANGE)
    assert opened  # the busy timeout of `connect` (30 s) applies to every one of them
    assert any(kw.get("wal") for kw in opened)  # the writer


async def test_a_write_during_the_refresh_upsert_succeeds(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    many = {
        f"https://example.test/a-{i}": (f"https://example.test/a-{i}", "2024-05-01", None)
        for i in range(MANY_ROWS)
    }
    monkeypatch.setattr(SitemapCache, "_rows_from", lambda _self, _entries: many)

    def other_writer() -> None:
        conn = connect(db, wal=True)  # the busy timeout of `connect` waits for the upsert
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO discovered_urls (url, domain, company, ticker, source, "
                    "discovered_at, status) VALUES (?, ?, 'Acme', 'ACME', 'ddg', '2026-01-01', 'pending')",
                    (A3, DOMAIN),
                )
        finally:
            conn.close()

    cache = make(db, standard_site(), Clock())
    await asyncio.gather(cache.begin(RANGE), asyncio.to_thread(other_writer))
    assert rows(db, "SELECT COUNT(*) FROM discovered_urls") == [(1,)]
    assert rows(db, "SELECT COUNT(*) FROM sitemap_entries") == [(MANY_ROWS,)]


async def test_a_pair_on_another_domain_progresses_during_the_refresh(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = make(db, standard_site(), Clock())
    real = SitemapCache._write
    slow = 0.3

    def slow_write(self: SitemapCache, *args: object) -> None:
        time.sleep(slow)  # a long upsert, in the worker thread
        real(self, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(SitemapCache, "_write", slow_write)
    ticks = 0

    async def other_domain() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    pair = asyncio.create_task(other_domain())
    try:
        await cache.begin(RANGE)
    finally:
        pair.cancel()
    assert ticks >= LOOP_TICKS  # the event loop was not blocked for the 0.3 s


async def test_the_log_says_what_was_refreshed_and_how_many_entries(
    db: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        await make(db, standard_site(), Clock()).begin(RANGE)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "sitemap crawl of example.test from 2022-01-01" in text
    assert "2 entries stored, 0 failure(s)" in text
    assert "2 current entries from the crawl of" in text
