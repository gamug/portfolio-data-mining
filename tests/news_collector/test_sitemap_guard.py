"""The guard between processes on one database (T-070b): one crawl per domain, a failed winner, a
crashed crawl, a takeover. Two `SitemapCache` objects on one file are two processes: each has its
own lock, its own rows and its own connections."""

from __future__ import annotations

import asyncio
import multiprocessing
import sqlite3
from datetime import timedelta

import pytest
from sitemap_helpers import (
    A1,
    A2,
    DOMAIN,
    RANGE,
    ROOT,
    START,
    Clock,
    Site,
    child_crawl,
    read,
    rows,
    standard_site,
)

import news_collector.sitemap_cache as sc
from news_collector.config import sitemap_stale_after
from news_collector.models import DateRange
from news_collector.sitemap_cache import SitemapCache

POLL = 0.01
STALE = timedelta(minutes=30)
TWO = 2
THREE = 3
WAIT_LIMIT = 5.0  # seconds: a waiter that never gives up fails the test instead of hanging it


def process(db: str, site: Site, clock: Clock, **kw: object) -> SitemapCache:
    """A cache as another process would make it: its own lock and rows, the same file."""
    return SitemapCache(
        DOMAIN,
        [ROOT],
        db,
        site.fetch,
        lambda _u: True,
        clock,
        stale_after=STALE,
        poll=POLL,
        **kw,  # type: ignore[arg-type]
    )


def open_crawl(db: str, started_at: str, range_start: str = "2022-01-01") -> None:
    """A crawl row with no `finished_at`: a process that is crawling (or crashed while doing so)."""
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO sitemap_crawls VALUES (?, ?, NULL, 0, ?, ?, NULL, NULL)",
        (DOMAIN, started_at, range_start, sc.signature([ROOT])),
    )
    conn.commit()
    conn.close()


def finish_crawl(
    db: str, started_at: str, *, complete: bool, entries: list[str] | None = None
) -> None:
    """What a winner's write does: its entries and its closed row, in one transaction."""
    conn = sqlite3.connect(db)
    for url in entries or []:
        conn.execute(
            "INSERT INTO sitemap_entries VALUES (?, ?, '2024-05-01', NULL, ?, ?)",
            (DOMAIN, url, started_at, started_at),
        )
    conn.execute(
        "UPDATE sitemap_crawls SET finished_at = ?, complete = ?, entries = ?, failures = ? "
        "WHERE domain = ? AND started_at = ?",
        (
            sc.stamp(sc.utc_now()),
            int(complete),
            len(entries or []),
            0 if complete else 1,
            DOMAIN,
            started_at,
        ),
    )
    conn.commit()
    conn.close()


# --- one crawl, two processes -------------------------------------------------------------------------


async def test_two_processes_make_one_crawl(db: str) -> None:
    site = standard_site()
    site.delay = 0.05  # the crawl takes long enough for the other process to arrive
    clock = Clock()
    first, second = process(db, site, clock), process(db, site, clock)
    run_a, run_b = await asyncio.gather(first.begin(RANGE), second.begin(RANGE))
    assert site.crawls() == 1  # one fetch of the root in all
    assert first.crawls + second.crawls == 1  # one of them crawled, the other waited
    assert read(run_a) == read(run_b) == (sorted([A1, A2]), [])
    assert rows(db, "SELECT COUNT(*), SUM(complete) FROM sitemap_crawls") == [(1, 1)]


async def test_two_forced_processes_make_one_crawl(db: str) -> None:
    site = standard_site()
    site.delay = 0.05
    clock = Clock()
    seed = process(db, site, clock)
    await seed.begin(RANGE)  # a fresh crawl
    first, second = process(db, site, clock), process(db, site, clock)
    await asyncio.gather(first.begin(RANGE, force=True), second.begin(RANGE, force=True))
    assert site.crawls() == TWO  # the seed, and one forced crawl for both
    assert rows(db, "SELECT COUNT(*) FROM sitemap_crawls WHERE complete = 1") == [(TWO,)]


async def test_a_waiter_that_needs_a_wider_range_crawls_after_the_winner(db: str) -> None:
    site = standard_site()
    site.delay = 0.05
    clock = Clock()
    narrow = DateRange(start=START.replace(year=2023), end=RANGE.end)
    first, second = process(db, site, clock), process(db, site, clock)
    run_a, run_b = await asyncio.gather(first.begin(narrow), second.begin(RANGE))
    # whoever won, the 2022 run needs a cache from 2022: a second crawl when the first was narrower
    assert read(run_b, RANGE)[1] == []
    assert rows(db, "SELECT MIN(range_start) FROM sitemap_crawls WHERE complete = 1") == [
        ("2022-01-01",)
    ]
    assert run_a.crawl is not None


# --- a failed winner ------------------------------------------------------------------------------------


async def test_a_failed_winner_means_the_waiter_crawls_nothing(db: str) -> None:
    site = Site({})  # the source is down
    site.delay = 0.05
    clock = Clock()
    first, second = process(db, site, clock), process(db, site, clock)
    run_a, run_b = await asyncio.gather(first.begin(RANGE), second.begin(RANGE))
    assert site.crawls() == 1  # one outage, one crawl, not one per process
    assert first.crawls + second.crawls == 1
    assert run_a.crawl is None
    assert run_b.crawl is None  # both apply the failed-refresh cases: no cache, the pairs fail
    messages = run_a.failures + run_b.failures
    assert any("by another process" in m for m in messages)  # the waiter says whose crawl failed
    assert rows(db, "SELECT complete, failures FROM sitemap_crawls") == [(0, 1)]


async def test_a_waiter_uses_the_old_cache_when_the_winner_fails(db: str) -> None:
    site = standard_site()
    clock = Clock()
    await process(db, site, clock).begin(RANGE)
    site.down.add(ROOT)
    site.delay = 0.05
    clock.advance(days=sc.FRESH_DAYS + 1)  # stale: both refresh
    first, second = process(db, site, clock), process(db, site, clock)
    run_a, run_b = await asyncio.gather(first.begin(RANGE), second.begin(RANGE))
    assert site.crawls() == TWO  # the first crawl, and one failed attempt for both
    assert read(run_a)[0] == read(run_b)[0] == sorted([A1, A2])  # case b: the old crawl, warned
    assert run_a.warnings and run_b.warnings


# --- a crashed crawl and a takeover --------------------------------------------------------------------


async def test_a_crashed_crawl_older_than_the_timeout_lets_a_new_crawl_start(db: str) -> None:
    site = standard_site()
    clock = Clock()
    crashed = sc.stamp(clock.now - STALE - timedelta(minutes=1))
    open_crawl(db, crashed)  # a process died 31 minutes ago, mid-crawl
    run = await asyncio.wait_for(process(db, site, clock).begin(RANGE), WAIT_LIMIT)  # not forever
    assert site.crawls() == 1  # the dead row did not block it
    assert read(run) == (sorted([A1, A2]), [])
    assert rows(db, "SELECT COUNT(*) FROM sitemap_crawls WHERE finished_at IS NULL") == [
        (1,)
    ]  # still there, ignored


async def test_a_waiter_takes_over_when_the_winner_goes_stale(db: str) -> None:
    site = standard_site()
    clock = Clock()
    winner = sc.stamp(clock.now - timedelta(minutes=29))  # alive, 1 minute from the timeout
    open_crawl(db, winner)

    async def time_passes() -> None:
        await asyncio.sleep(POLL * 5)
        clock.advance(minutes=2)  # the winner never finishes: its row is now stale

    run, _ = await asyncio.gather(
        asyncio.wait_for(process(db, site, clock).begin(RANGE), WAIT_LIMIT), time_passes()
    )
    assert site.crawls() == 1  # the waiter took over and crawled
    assert read(run) == (sorted([A1, A2]), [])


async def test_a_waiter_reads_the_winners_complete_crawl(db: str) -> None:
    site = standard_site()
    clock = Clock()
    winner = sc.stamp(clock.now)
    open_crawl(db, winner)

    async def winner_finishes() -> None:
        await asyncio.sleep(POLL * 5)
        finish_crawl(db, winner, complete=True, entries=[A1, A2])

    run, _ = await asyncio.gather(process(db, site, clock).begin(RANGE), winner_finishes())
    assert site.crawls() == 0  # it crawled nothing: it read what the other process wrote
    assert read(run) == (sorted([A1, A2]), [])


async def test_a_cancelled_crawl_closes_its_row_as_failed(db: str) -> None:
    site = standard_site()
    site.delay = 5.0  # a crawl that is still running when its task is cancelled
    clock = Clock()
    task = asyncio.create_task(process(db, site, clock).begin(RANGE))
    await asyncio.sleep(POLL * 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rows(db, "SELECT COUNT(*) FROM sitemap_crawls WHERE finished_at IS NULL") == [(0,)]
    assert rows(db, "SELECT complete, failures FROM sitemap_crawls") == [
        (0, 1)
    ]  # not left for the timeout


async def test_a_crawl_that_raises_closes_its_row_as_failed(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    cache = process(db, standard_site(), Clock())
    real = SitemapCache._write
    calls = []

    def write_once_then_ok(self: SitemapCache, *args: object) -> None:
        calls.append(args)
        if len(calls) == 1:
            boom()
        real(self, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(SitemapCache, "_write", write_once_then_ok)
    run = await cache.begin(RANGE)
    assert run.crawl is None
    assert rows(db, "SELECT complete, failures FROM sitemap_crawls") == [(0, 1)]  # closed, not open


# --- the query, the schema, the setting ----------------------------------------------------------------


def test_the_in_progress_lookup_uses_the_partial_index(db: str) -> None:
    conn = sqlite3.connect(db)
    plan = " ".join(
        r[3]
        for r in conn.execute(
            "EXPLAIN QUERY PLAN " + sc.OPEN_CRAWL_SQL, (DOMAIN, "2026-10-19T09:30:00.000000")
        )
    )
    conn.close()
    assert "ix_sitemap_crawls_open" in plan
    assert "SCAN" not in plan


async def test_the_claim_inserts_the_row_in_one_step_and_a_second_claim_sees_it(db: str) -> None:
    cache = process(db, Site({}), Clock())
    first = cache._claim(START)
    second = cache._claim(START)
    assert first.started_at is not None
    assert second.started_at is None
    assert second.other == first.started_at  # the second process is told whose crawl to wait for
    assert rows(db, "SELECT COUNT(*) FROM sitemap_crawls WHERE finished_at IS NULL") == [(1,)]


def test_the_stale_timeout_defaults_to_thirty_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SITEMAP_STALE_MINUTES", raising=False)
    assert sitemap_stale_after() == STALE


def test_the_stale_timeout_follows_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SITEMAP_STALE_MINUTES", "90")
    assert sitemap_stale_after() == timedelta(minutes=90)


@pytest.mark.parametrize("bad", ["soon", "0", "-5", "nan", "inf", "1e12", "5 min"])
def test_a_bad_stale_timeout_is_ignored_with_a_warning(
    bad: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SITEMAP_STALE_MINUTES", bad)
    assert sitemap_stale_after() == STALE  # zero would make every crawl stale: the guard off
    assert "SITEMAP_STALE_MINUTES" in caplog.text


# --- really two processes ----------------------------------------------------------------------------


def test_two_operating_system_processes_make_one_crawl(db: str) -> None:
    ctx = multiprocessing.get_context("spawn")  # two fresh interpreters, nothing shared
    results = ctx.Queue()
    procs = [ctx.Process(target=child_crawl, args=(db, results)) for _ in range(TWO)]
    for proc in procs:
        proc.start()
    outputs = [results.get(timeout=60) for _ in procs]
    for proc in procs:
        proc.join(timeout=60)
    assert all(proc.exitcode == 0 for proc in procs)
    assert sorted(fetches for fetches, _, _ in outputs) == [
        0,
        1,
    ]  # one crawled, one fetched nothing
    assert [(entries, failures) for _, entries, failures in outputs] == [
        (1, 0),
        (1, 0),
    ]  # both read it
    assert rows(db, "SELECT COUNT(*), SUM(complete) FROM sitemap_crawls") == [(1, 1)]
