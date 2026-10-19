"""The per-domain sitemap cache in one process (T-070a)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sitemap_helpers import (
    A1,
    A2,
    A3,
    CHILD_A,
    CHILD_B,
    CNBC_PLAIN,
    CNBC_UNDATED,
    CNBC_URL,
    DAYS_FRESH,
    DAYS_PURGE,
    DOMAIN,
    FIVE,
    INDEX_ROOT,
    LOOP_TICKS,
    MANY_ROWS,
    ODD_TITLE,
    RANGE,
    RETRY_MINUTES,
    ROOT,
    START,
    T0,
    THREE_CRAWLS,
    TITLE,
    TWO,
    Clock,
    Site,
    cnbc_strategy,
    index,
    leaf,
    make,
    read,
    rows,
    seen,
    standard_site,
    strategy_for,
)

import news_collector.sitemap_cache as sc
from data_mining.db import connect
from news_collector.config import sitemap_retry_after
from news_collector.connectors.cnbc import CNBCConnector
from news_collector.models import DateRange
from news_collector.sitemap_cache import SitemapCache, SitemapRun
from news_collector.utils.url import is_valid_http_url, normalize_url

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


async def test_two_concurrent_tickers_make_one_crawl(db: str) -> None:
    site = standard_site()
    cache = make(db, site, Clock())
    run = await cache.begin(RANGE)
    first, second = strategy_for(run, site), strategy_for(run, site)
    one, two = await asyncio.gather(
        first.discover("Acme", "ACME", RANGE), second.discover("Beta", "BETA", RANGE)
    )
    assert site.crawls() == 1
    assert sorted(u.url for u in one) == sorted(u.url for u in two) == sorted([A1, A2])
    assert cache.crawls == 1


async def test_the_pair_gets_the_cached_entries_inside_the_range(db: str) -> None:
    site = standard_site()
    cache = make(db, site, Clock())
    strategy = strategy_for(await cache.begin(RANGE), site)
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
    run = await cache.begin(RANGE, force=True)
    assert read(run)[0] == sorted([A1, A3])
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
    run = await cache.begin(RANGE, force=True)  # (a) forced, failed, the cache is fresh
    assert run.crawl is not None
    assert read(run) == (sorted([A1, A2]), [])
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
        run = await cache.begin(RANGE)  # (b)
    assert read(run) == (sorted([A1, A2]), [])
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
    run = await cache.begin(RANGE)  # (c) the run starts in 2022: the refresh fails
    assert run.crawl is None
    entries, failures = run.entries(RANGE)
    assert entries == []
    assert len(failures) == 1
    assert "does not cover 2022-01-01" in failures[0]
    assert ROOT in failures[0]


async def test_no_cache_and_a_failed_crawl_fails_the_pair(db: str) -> None:
    site = Site({})
    cache = make(db, site, Clock())
    strategy = strategy_for(await cache.begin(RANGE), site)
    assert await strategy.discover("Acme", "ACME", RANGE) == []
    assert len(strategy.failures) == 1


async def test_every_ticker_of_a_failed_domain_carries_the_failure(db: str) -> None:
    site = Site({})
    cache = make(db, site, Clock())
    run = await cache.begin(RANGE)
    first, second = strategy_for(run, site), strategy_for(run, site)
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
    run = await cache.begin(RANGE, force=True)
    assert read(run)[0] == sorted([A1, A2])  # the old crawl, not the half of the new one
    assert rows(db, "SELECT COUNT(*) FROM sitemap_crawls WHERE complete = 1") == [(1,)]


async def test_a_child_that_fails_every_time_keeps_the_crawl_incomplete(db: str) -> None:
    site = Site({INDEX_ROOT: index(CHILD_A, CHILD_B), CHILD_A: leaf((A1, "2024-05-01"))})
    clock = Clock()
    cache = make(db, site, clock, [INDEX_ROOT])
    for _ in range(THREE_CRAWLS):
        clock.advance(days=DAYS_FRESH + 1)
        run = await cache.begin(RANGE)
        assert run.entries(RANGE)[0] == []
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
    run = await cache.begin(DateRange(start=date(2024, 1, 1), end=RANGE.end), force=True)  # forced
    starts = rows(db, "SELECT range_start FROM sitemap_crawls WHERE complete = 1")
    assert {s[0] for s in starts} == {"2022-01-01"}
    assert read(run, DateRange(start=date(2024, 1, 1), end=RANGE.end))[0] == [A1]  # 2024 only
    assert read(await cache.begin(RANGE))[0] == sorted([A1, A2])  # the wide start was kept


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
    run = await cache.begin(RANGE)
    clock.advance(days=DAYS_FRESH * 3)  # the run goes on for weeks: no begin, no refresh
    run.entries(RANGE)
    run.entries(RANGE)
    assert site.crawls() == 1


async def test_a_changed_root_or_version_refreshes_and_a_failed_refresh_fails_the_pair(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = Site({ROOT: leaf((A1, "2024-05-01")), INDEX_ROOT: leaf((A3, "2025-01-01"))})
    clock = Clock()
    await make(db, site, clock).begin(RANGE)
    clock.advance(hours=1)
    wider = make(db, site, clock, [ROOT, INDEX_ROOT])  # a root was added
    assert read(await wider.begin(RANGE))[0] == sorted([A1, A3])
    monkeypatch.setattr(sc, "CACHE_VERSION", sc.CACHE_VERSION + 1)  # a release changed the filter
    clock.advance(hours=1)
    bumped = make(db, site, clock, [ROOT, INDEX_ROOT])
    site.down.add(ROOT)
    run = await bumped.begin(RANGE)  # the cache is of the old rules and the refresh fails
    entries, failures = run.entries(RANGE)
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
    assert read(await mine.begin(RANGE))[0] == sorted([A1, A2])
    other_site = Site({ROOT: leaf((A1, "2024-05-01"), (A3, "2025-01-01"))})
    clock.advance(hours=1)
    await make(db, other_site, clock).begin(RANGE, force=True)  # another process refreshes
    run = await mine.begin(RANGE)  # the next run reads the latest crawl's identity and reloads
    assert read(run)[0] == sorted([A1, A3])
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
    assert ticks >= LOOP_TICKS  # the loop ran during the 0.3 s; the bound is far below 30 ticks


async def test_the_log_says_what_was_refreshed_and_how_many_entries(
    db: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        await make(db, standard_site(), Clock()).begin(RANGE)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "sitemap crawl of example.test from 2022-01-01" in text
    assert "2 entries stored, 0 failure(s)" in text
    assert "2 current entries from the crawl of" in text


# --- a database error, the run's own decision, the flag, resume ------------------------------------


async def test_a_database_error_fails_the_pairs_and_does_not_raise(tmp_path: Path) -> None:
    bare = tmp_path / "bare.db"
    sqlite3.connect(bare).execute("CREATE TABLE t (x)").connection.commit()  # no cache tables
    run = await make(str(bare), standard_site(), Clock()).begin(RANGE)
    assert run.crawl is None
    entries, failures = run.entries(RANGE)
    assert entries == []
    assert len(failures) == 1
    assert "no such table: sitemap_crawls" in failures[0]


async def test_a_run_that_cannot_use_the_cache_does_not_change_what_another_run_reads(
    db: str,
) -> None:
    site = standard_site()
    clock = Clock()
    cache = make(db, site, clock)
    good = await cache.begin(RANGE)  # run A: a cache from 2022
    site.down.add(ROOT)
    earlier = DateRange(start=date(2020, 1, 1), end=RANGE.end)
    bad = await cache.begin(earlier)  # run B: starts before the cache and the refresh fails
    assert bad.crawl is None
    assert read(good) == (sorted([A1, A2]), [])  # A is untouched by B
    assert read(bad, earlier)[1]  # and B reports its failure


async def test_the_rows_are_parsed_once_per_crawl_and_shared(db: str) -> None:
    cache = make(db, standard_site(), Clock())
    first, second = await cache.begin(RANGE), await cache.begin(RANGE)
    assert first.rows is second.rows  # one parse per crawl, not one per run or per ticker
    entries, _ = first.entries(RANGE)
    assert all(e is r for e, r in zip(entries, first.rows, strict=False))  # no new objects


async def test_a_cache_reused_in_a_second_event_loop_still_works(db: str) -> None:
    site = standard_site()
    cache = make(db, site, Clock())

    async def contended() -> list[SitemapRun]:
        return list(
            await asyncio.gather(cache.begin(RANGE, force=True), cache.begin(RANGE, force=True))
        )

    for _ in range(THREE_CRAWLS):  # the lock binds to the loop that first waits on it
        runs = await asyncio.to_thread(asyncio.run, contended())
        assert all(r.crawl is not None and not r.failures for r in runs)
    assert site.crawls() == THREE_CRAWLS  # one crawl per loop: two forced requests, one crawl


async def test_a_failed_refresh_of_a_fresh_cache_is_a_warning_of_the_run(db: str) -> None:
    site = standard_site()
    clock = Clock()
    cache = make(db, site, clock)
    await cache.begin(RANGE)
    site.down.add(ROOT)
    clock.advance(hours=1)
    run = await cache.begin(RANGE, force=True)
    assert run.failures == ()
    [warning] = run.warnings
    assert "sitemap refresh of example.test failed" in warning
    assert "0.0 days old" in warning


async def test_the_title_evidence_is_kept_through_the_cache(db: str) -> None:
    site = Site(
        {
            ROOT: leaf(
                (CNBC_URL, "2024-05-01", TITLE),  # only the title names the company
                (CNBC_PLAIN, "2024-05-02"),  # nothing names it
                (CNBC_UNDATED, None, TITLE),
            )
        }
    )
    connector = CNBCConnector(MagicMock())
    cache = SitemapCache(DOMAIN, [ROOT], db, site.fetch, connector.is_article_url, Clock())
    run = await cache.begin(RANGE)
    cached = await cnbc_strategy(site, run).discover("Apple Inc.", "AAPL", RANGE)
    live = await cnbc_strategy(site, None).discover("Apple Inc.", "AAPL", RANGE)
    assert seen(cached) == seen(live)  # the same pairs, classes, reasons, titles and dates
    by_url = {p.url: p for p in cached}
    assert by_url[CNBC_URL].match_class == "accept"  # by the title, the slug has no name
    assert by_url[CNBC_URL].title == TITLE
    assert by_url[CNBC_PLAIN].match_class == "reject"
    assert rows(db, "SELECT title FROM sitemap_entries WHERE url = ?", CNBC_URL) == [(TITLE,)]


async def test_an_undated_entry_survives_the_end_filter_and_the_round_trip(db: str) -> None:
    site = Site({ROOT: leaf((A1, "2024-05-01"), (A2, None), (A3, "2022-03-01"))})
    run = await make(db, site, Clock()).begin(RANGE)
    early = DateRange(start=START, end=date(2023, 1, 1))
    assert read(run, early)[0] == sorted([A2, A3])  # A2 has no lastmod: kept whatever the end
    assert read(run)[0] == sorted([A1, A2, A3])
    assert rows(db, "SELECT lastmod FROM sitemap_entries WHERE url = ?", A2) == [(None,)]
    fresh = await make(db, site, Clock(T0 + timedelta(minutes=1))).begin(
        RANGE
    )  # read back from the table
    assert read(fresh, early)[0] == sorted([A2, A3])


async def test_a_title_with_markup_and_non_ascii_comes_back_unchanged(db: str) -> None:
    site = Site({ROOT: leaf((A1, "2024-05-01", ODD_TITLE))})
    await make(db, site, Clock()).begin(RANGE)
    assert rows(db, "SELECT title FROM sitemap_entries")[0][0] == ODD_TITLE
    other = await make(db, site, Clock(T0 + timedelta(minutes=1))).begin(RANGE)  # a new process
    assert [e.title for e in other.entries(RANGE)[0]] == [ODD_TITLE]


# --- an outage: back-off, the purge of crawl rows, one error per domain ------------------------------


async def test_during_an_outage_runs_do_not_retry_the_crawl_until_the_back_off_ends(
    db: str,
) -> None:
    site = Site({})  # nothing answers
    clock = Clock()
    cache = make(db, site, clock)
    for _ in range(FIVE):
        run = await cache.begin(RANGE)
        assert run.crawl is None  # case c, every time
        assert run.failures
    assert cache.crawls == 1  # one attempt, not five
    await cache.begin(RANGE, force=True)  # a forced call tries again
    assert cache.crawls == TWO
    clock.advance(minutes=RETRY_MINUTES + 1)
    await cache.begin(RANGE)  # the back-off is over
    assert cache.crawls == THREE_CRAWLS
    site.pages[ROOT] = leaf((A1, "2024-05-01"))
    await cache.begin(RANGE)  # still backing off after the third failure
    assert cache.crawls == THREE_CRAWLS
    clock.advance(minutes=RETRY_MINUTES + 1)
    run = await cache.begin(RANGE)  # the site is back: a crawl, and the back-off is cleared
    assert cache.crawls == THREE_CRAWLS + 1
    assert read(run)[0] == [A1]
    await cache.begin(RANGE, force=True)
    assert cache.crawls == THREE_CRAWLS + TWO


async def test_a_failed_refresh_purges_crawl_rows_older_than_90_days_but_not_the_latest_complete(
    db: str,
) -> None:
    site = standard_site()
    clock = Clock()
    cache = make(db, site, clock)
    await cache.begin(RANGE)  # a complete crawl, the only one
    site.down.add(ROOT)
    for days in (10, DAYS_PURGE + 10):  # two failed attempts, 10 and 110 days later
        clock.advance(days=days)
        await cache.begin(RANGE, force=True)
    rows_left = rows(db, "SELECT complete FROM sitemap_crawls ORDER BY started_at")
    # the failed attempt of day 10 is older than 90 days at day 110 and is gone; the complete
    # crawl of day 0 is older still but it is the latest complete one, so it stays
    assert rows_left == [(1,), (0,)]
    assert rows(db, "SELECT COUNT(*) FROM sitemap_entries") == [(TWO,)]  # its entries stay too


# --- the back-off is a setting ---------------------------------------------------------------------


def test_the_back_off_defaults_to_five_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SITEMAP_RETRY_MINUTES", raising=False)
    assert sitemap_retry_after() == timedelta(minutes=RETRY_MINUTES)


def test_the_back_off_follows_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SITEMAP_RETRY_MINUTES", "0.5")
    assert sitemap_retry_after() == timedelta(seconds=30)
    monkeypatch.setenv("SITEMAP_RETRY_MINUTES", "0")
    assert sitemap_retry_after() == timedelta(0)


@pytest.mark.parametrize("bad", ["soon", "-1", "nan", "inf", "-inf", "1e12", "5 min"])
def test_a_bad_back_off_value_is_ignored_with_a_warning(
    bad: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SITEMAP_RETRY_MINUTES", bad)
    with caplog.at_level(logging.WARNING):
        got = sitemap_retry_after()
    assert got == timedelta(minutes=RETRY_MINUTES)
    assert "SITEMAP_RETRY_MINUTES" in caplog.text


async def test_the_cache_reads_the_setting_and_zero_turns_the_back_off_off(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SITEMAP_RETRY_MINUTES", "0")
    site = Site({})
    cache = make(db, site, Clock())
    for _ in range(FIVE):
        await cache.begin(RANGE)
    assert cache.crawls == FIVE  # every run retries
