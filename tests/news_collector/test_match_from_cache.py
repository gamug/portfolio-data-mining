"""T-071: per-ticker matching from the sitemap cache, with the token index, storing no reject.

The proof that the faster path changes no class (guardrail 2 of Work item 15) is the equality of
the `accept`/`review` pairs with the full scan, here on a fixture corpus; the real-corpus
equality is `scripts/sitemap_preselect_check.py check` (its tests are in `tests/scripts`).
"""

from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sitemap_helpers import DOMAIN, Clock, Site, fake_connector, leaf, request, standard_site
from tqdm import tqdm

from data_mining.matching import Match
from data_mining.token_index import TokenIndex
from news_collector import main as collector_main
from news_collector import sitemap_cache as sc
from news_collector.api.deps import get_client
from news_collector.api.routers import _common, discovery
from news_collector.api.schemas import RunAllResponse
from news_collector.connectors.cnbc import CNBCConnector
from news_collector.models import (
    Company,
    DateRange,
    DiscoveredURL,
    DiscoveryStats,
    PartialStats,
    SitemapEntry,
)
from news_collector.orchestrator import DiscoveryOrchestrator
from news_collector.sitemap_cache import Crawl, SitemapCache, SitemapRun
from news_collector.storage.queue import URLQueue
from news_collector.strategies.sitemap import SitemapRSSStrategy
from news_collector.utils.url import normalize_url

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "sitemap_preselect_check", _SCRIPTS / "sitemap_preselect_check.py"
)
assert _spec is not None
assert _spec.loader is not None
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

START = date(2022, 1, 1)
RANGE = DateRange(start=START, end=date(2026, 8, 10))
ROOT = "https://www.cnbc.com/sitemapAll.xml"
STRESS = 578_000  # a stress size; the real in-range size is 109,493 (T-068)
COMPANIES = [
    ("KLAC", "KLA Corporation"),
    ("AAPL", "Apple Inc."),
    ("BAC", "Bank of America Corporation"),
    ("BRK.B", "Berkshire Hathaway"),
    ("ON", "ON Semiconductor"),
    ("A", "Agilent Technologies"),
    ("IT", "Gartner"),
    ("NWS", "News Corp (Class B)"),
    ("T", "AT&T Inc."),
    ("PEP", "PepsiCo"),
    ("MMM", "3M"),
]
APPLE = "https://www.cnbc.com/2024/05/01/apple-earnings-beat.html"
PLAIN = "https://www.cnbc.com/2024/05/02/stock-picks-for-the-month.html"


def connector() -> CNBCConnector:
    conn = CNBCConnector(MagicMock())
    conn.config.sitemap_roots = [ROOT]
    conn.config.rss_feeds = []
    conn.config.wayback_fallback = False
    return conn


def strategy(cache: SitemapRun | None, conn: CNBCConnector | None = None) -> SitemapRSSStrategy:
    return SitemapRSSStrategy(conn or connector(), cast(httpx.AsyncClient, MagicMock()), cache)


def cached_run(entries: list[SitemapEntry]) -> SitemapRun:
    """A run over `entries` as the cache holds them: normalized, the first entry of a URL wins, in
    URL order (`CURRENT_ENTRIES_SQL` orders by url)."""
    unique: dict[str, SitemapEntry] = {}
    for e in entries:
        url = normalize_url(e.url)
        unique.setdefault(url, SitemapEntry(url=url, lastmod=e.lastmod, title=e.title))
    rows = tuple(unique[u] for u in sorted(unique))
    return SitemapRun(
        "cnbc.com", Crawl("2026-10-20T10:00:00.000000", START, "sig", len(rows)), (), rows
    )


def key(p: DiscoveredURL) -> tuple:
    return (p.url, p.match_class, p.match_reason, p.pub_date, p.source, p.title)


def full_scan(run: SitemapRun, company: str, ticker: str, rng: DateRange) -> list[DiscoveredURL]:
    """Today's path: every in-range cached entry classed, every class kept."""
    entries, _ = run.entries(rng)
    return strategy(None).classify_entries(entries, company, ticker)


def corpus() -> list[SitemapEntry]:
    words = ["stock", "market", "shares", "jump", "fed", "rates", "oil", "gold", "tech", "bank"]
    raw = [
        SitemapEntry(
            url=f"https://www.cnbc.com/2024/05/01/{words[i % 10]}-{words[(i * 3) % 10]}-{i}.html",
            lastmod=date(2024, 5, 1),
        )
        for i in range(200)
    ]
    fixture, _ = script.title_fixture()
    return script.synthetic_corpus(raw, COMPANIES, n=2500, seed=5) + fixture


# --- equality with the full scan ---------------------------------------------------------------


async def test_the_cached_path_gives_the_full_scans_accept_and_review_pairs() -> None:
    run = cached_run(corpus())
    reasons: set[str] = set()
    for ticker, name in COMPANIES:
        got = await strategy(run).discover(name, ticker, RANGE)
        want = [p for p in full_scan(run, name, ticker, RANGE) if p.match_class != "reject"]
        assert [key(p) for p in got] == [key(p) for p in want], ticker
        assert all(p.match_class in ("accept", "review") for p in got)
        reasons.update(p.match_reason for p in got if p.match_reason)
    assert len(reasons) >= 6  # not vacuous: the corpus makes most of the matcher's signals


async def test_the_rejects_not_stored_are_the_entries_in_range_minus_the_pairs() -> None:
    run = cached_run(corpus())
    for ticker, name in COMPANIES:
        strat = strategy(run)
        got = await strat.discover(name, ticker, RANGE)
        entries, _ = run.entries(RANGE)
        assert strat.rejected_not_stored == len(entries) - len(got), ticker
        every = full_scan(run, name, ticker, RANGE)
        assert strat.rejected_not_stored == sum(p.match_class == "reject" for p in every)


async def test_a_run_ending_in_the_past_equals_the_full_scan_with_that_end() -> None:
    entries = [
        SitemapEntry(url=APPLE, lastmod=date(2023, 3, 1)),  # inside
        SitemapEntry(
            url="https://www.cnbc.com/2025/05/01/apple-sales-rise.html", lastmod=date(2025, 5, 1)
        ),  # after the end
        SitemapEntry(url="https://www.cnbc.com/2024/05/03/apple-undated.html", lastmod=None),
        SitemapEntry(url=PLAIN, lastmod=date(2023, 3, 2)),
    ]
    run = cached_run(entries)
    early = DateRange(start=START, end=date(2023, 12, 31))
    strat = strategy(run)
    got = await strat.discover("Apple Inc.", "AAPL", early)
    want = [p for p in full_scan(run, "Apple Inc.", "AAPL", early) if p.match_class != "reject"]
    assert [key(p) for p in got] == [key(p) for p in want]
    assert {p.url for p in got} == {APPLE, "https://www.cnbc.com/2024/05/03/apple-undated.html"}
    assert strat.rejected_not_stored == 1  # PLAIN; the entry after the end is not read at all


async def test_the_pair_gets_the_cached_lastmod_and_a_url_listed_twice_keeps_the_first(
    db: str,
) -> None:
    site = Site({ROOT: leaf((APPLE, "2024-05-01"), (APPLE, "2024-05-09"), (PLAIN, "2024-05-02"))})
    conn = connector()
    cache = SitemapCache("cnbc.com", [ROOT], db, site.fetch, conn.is_article_url, Clock())
    run = await cache.begin(RANGE)
    cached = await strategy(run, conn).discover("Apple Inc.", "AAPL", RANGE)
    live = strategy(None, conn)
    live._fetch = site.fetch  # type: ignore[method-assign]
    old = await live.discover("Apple Inc.", "AAPL", RANGE)
    assert [p.pub_date for p in cached] == [date(2024, 5, 1)]  # the first entry's lastmod
    assert [key(p) for p in cached] == [key(p) for p in old if p.match_class != "reject"]


# --- a live entry of a URL the cache holds does not replace it ---------------------------------


def with_rss(strat: SitemapRSSStrategy, *entries: SitemapEntry) -> SitemapRSSStrategy:
    strat._connector.config.rss_feeds = ["https://rss.test/feed"]

    async def parse(_url: str) -> list[SitemapEntry]:
        for e in entries:
            e._from_rss = True  # type: ignore[attr-defined]
        return list(entries)

    strat._parse_rss = parse  # type: ignore[method-assign]
    return strat


async def test_a_url_in_the_cache_and_in_an_rss_entry_keeps_the_cached_one() -> None:
    """The cached entry comes first and the first entry of a URL wins, as in the old path: the RSS
    copy (whose title would make it `accept`) is not read, and the cached reject is not stored."""
    cached = SitemapEntry(url=PLAIN, lastmod=date(2024, 5, 2))
    rss = SitemapEntry(url=PLAIN, lastmod=date(2024, 5, 2), title="Apple Inc. reports earnings")
    only_rss = SitemapEntry(
        url="https://www.cnbc.com/2024/06/01/apple-buys-startup.html", lastmod=date(2024, 6, 1)
    )
    run = cached_run([cached, SitemapEntry(url=APPLE, lastmod=date(2024, 5, 1))])
    new = await with_rss(strategy(run), rss, only_rss).discover("Apple Inc.", "AAPL", RANGE)
    # the old path: the same entries in one list, the cached ones first
    old = with_rss(strategy(None), rss, only_rss)
    all_entries = [*run.rows, rss, only_rss]
    old_pairs = old._dedupe_and_filter(all_entries, "Apple Inc.", "AAPL")
    assert [key(p) for p in new] == [key(p) for p in old_pairs if p.match_class != "reject"]
    assert PLAIN not in {p.url for p in new}  # not promoted by the RSS title
    assert {p.url: p.source for p in new}[only_rss.url] == "rss"  # an RSS-only URL still comes


async def test_a_cached_entry_outside_the_run_does_not_block_the_live_one() -> None:
    cached = SitemapEntry(url=PLAIN, lastmod=date(2025, 5, 2))  # after this run's end
    rss = SitemapEntry(url=PLAIN, lastmod=date(2023, 5, 2), title="Apple Inc. reports earnings")
    run = cached_run([cached])
    early = DateRange(start=START, end=date(2024, 12, 31))
    new = await with_rss(strategy(run), rss).discover("Apple Inc.", "AAPL", early)
    # old path: the cached entry is dropped by the date filter, so the RSS entry is the first
    old_pairs = with_rss(strategy(None), rss)._dedupe_and_filter([rss], "Apple Inc.", "AAPL")
    assert [key(p) for p in new] == [key(p) for p in old_pairs]
    assert [p.match_class for p in new] == ["accept"]


async def test_a_live_reject_is_still_stored_and_only_the_cached_ones_are_not() -> None:
    rss = SitemapEntry(
        url="https://www.cnbc.com/2024/06/02/market-wrap.html", lastmod=date(2024, 6, 2)
    )
    run = cached_run([SitemapEntry(url=PLAIN, lastmod=date(2024, 5, 2))])
    strat = with_rss(strategy(run), rss)
    got = await strat.discover("Apple Inc.", "AAPL", RANGE)
    assert [(p.url, p.match_class, p.source) for p in got] == [(rss.url, "reject", "rss")]
    assert strat.rejected_not_stored == 1  # the cached PLAIN only


# --- no reject row reaches the queue --------------------------------------------------------------


async def test_a_batch_of_a_578000_entry_cache_writes_no_reject_row(tmp_path: Path) -> None:
    rows = [
        SitemapEntry(
            url=f"https://www.cnbc.com/2024/05/01/story-{i}.html", lastmod=date(2024, 5, 1)
        )
        for i in range(STRESS)
    ]
    keep = [
        SitemapEntry(
            url=f"https://www.cnbc.com/2024/05/01/apple-news-{i}.html", lastmod=date(2024, 5, 1)
        )
        for i in range(3)
    ]
    run = cached_run([*rows, *keep])
    strat = strategy(run)
    pairs = await strat.discover("Apple Inc.", "AAPL", RANGE)
    assert len(pairs) == len(keep)
    assert strat.rejected_not_stored == STRESS
    queue = URLQueue(str(tmp_path / "q.db"))
    queue.initialize()
    assert queue.enqueue_batch(pairs) == len(keep)
    queue.close()
    conn = sqlite3.connect(tmp_path / "q.db")
    assert conn.execute("SELECT COUNT(*) FROM discovered_urls").fetchone()[0] == len(keep)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM discovered_urls WHERE match_class = 'reject'"
        ).fetchone()[0]
        == 0
    )
    conn.close()


# --- the run, the index and the failure cases -----------------------------------------------------


def test_a_range_before_the_crawl_and_a_missing_crawl_are_failures_and_build_no_index() -> None:
    run = cached_run([SitemapEntry(url=APPLE, lastmod=date(2024, 5, 1))])
    before = DateRange(start=date(2021, 1, 1), end=RANGE.end)
    found, in_range, failures = run.select("Apple Inc.", "AAPL", before)
    assert (found, in_range) == ([], 0)
    assert "starts at 2022-01-01" in failures[0]
    assert "index" not in run._memo
    none = SitemapRun("cnbc.com", None, ("sitemap cache of cnbc.com unusable",))
    assert none.select("Apple Inc.", "AAPL", RANGE) == (
        [],
        0,
        ["sitemap cache of cnbc.com unusable"],
    )


def test_a_run_without_an_index_builds_it_once_and_a_given_one_is_used() -> None:
    run = cached_run([SitemapEntry(url=APPLE, lastmod=date(2024, 5, 1))])
    run.select("Apple Inc.", "AAPL", RANGE)
    first = run._memo["index"]
    run.select("Apple Inc.", "AAPL", RANGE)
    assert run._memo["index"] is first  # once
    rows = run.rows
    given = sc.build_index(rows)
    run2 = SitemapRun("cnbc.com", run.crawl, (), rows, index=given)
    run2.select("Apple Inc.", "AAPL", RANGE)
    assert "index" not in run2._memo
    assert given.size == len(rows)


async def test_the_cache_builds_the_index_once_per_crawl_and_every_run_shares_it(db: str) -> None:
    site = standard_site()
    cache = SitemapCache(
        "example.test", [site_root(site)], db, site.fetch, lambda _u: True, Clock()
    )
    first = await cache.begin(RANGE)
    second = await cache.begin(RANGE)
    assert first.index is not None
    assert first.index is second.index  # one index per crawl
    assert first.index.size == len(first.rows)


def site_root(site: Site) -> str:
    return next(iter(site.pages))


def test_the_index_lives_in_src_and_the_script_uses_that_class() -> None:
    assert script.TokenIndex is TokenIndex
    assert TokenIndex.__module__ == "data_mining.token_index"


async def test_holds_says_whether_the_cache_has_a_url_in_range() -> None:
    run = cached_run([SitemapEntry(url=PLAIN, lastmod=date(2024, 5, 2)), SitemapEntry(url=APPLE)])
    early = DateRange(start=START, end=date(2023, 1, 1))
    assert run.holds(PLAIN, RANGE) is True
    assert run.holds(PLAIN, early) is False  # in the cache, outside this run
    assert run.holds(APPLE, early) is True  # undated: kept whatever the end
    assert run.holds("https://www.cnbc.com/2099/x.html", RANGE) is False


async def test_the_strategy_without_a_cache_still_crawls_and_stores_every_class() -> None:
    """`cache=None` is today's live fetch: nothing changes for it (guardrail 4)."""
    site = Site({ROOT: leaf((APPLE, "2024-05-01"), (PLAIN, "2024-05-02"))})
    live = strategy(None)
    live._fetch = site.fetch  # type: ignore[method-assign]
    pairs = await live.discover("Apple Inc.", "AAPL", RANGE)
    assert sorted(p.match_class for p in pairs) == ["accept", "reject"]
    assert live.rejected_not_stored == 0


# --- the numbers: the run, the CLI and both API responses -----------------------------------------


def rejecting_connector() -> MagicMock:
    """A connector whose matcher accepts only the URLs ending `a-1` and rejects the rest."""
    conn = fake_connector()
    conn.explain_match.side_effect = lambda url, _title, _company, _ticker: (
        Match("accept", "name") if url.endswith("a-1") else Match("reject", "none")
    )
    return conn


async def no_api(*_: object) -> list:
    return []


@pytest.mark.usefixtures("every_entry_a_candidate")
async def test_the_run_stores_no_sitemap_reject_and_reports_how_many_it_did_not(
    db: str, offline: Site
) -> None:
    orchestrator = DiscoveryOrchestrator(
        connectors={DOMAIN: rejecting_connector()}, queue=URLQueue(db), concurrency=2
    )
    orchestrator._fetch_via_extra_api = no_api  # type: ignore[method-assign]
    done: list[PartialStats] = []

    stats = await orchestrator.run(
        [Company(ticker="AAA", name="AAA Inc."), Company(ticker="BBB", name="BBB Inc.")],
        date_range=RANGE,
        progress=done.append,
    )

    # two cached entries in range per ticker: one accepted (a-1), one reject (a-2; the old one is
    # out of range) -> per ticker: 1 pair stored, 1 counted
    assert {p.rejected_not_stored for p in done} == {1}
    assert stats.total_rejected_not_stored == 2
    assert stats.total_rejected == 0  # the sitemap rejects are not among the stored ones
    assert stats.total_inserted == 2
    assert sqlite3.connect(db).execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE match_class = 'reject'"
    ).fetchone() == (0,)
    assert len(done) == 2  # the progress callback: once per (ticker, domain)


@pytest.mark.usefixtures("every_entry_a_candidate")
async def test_a_progress_callback_that_raises_never_stops_the_run(db: str, offline: Site) -> None:
    orchestrator = DiscoveryOrchestrator(
        connectors={DOMAIN: rejecting_connector()}, queue=URLQueue(db), concurrency=1
    )
    orchestrator._fetch_via_extra_api = no_api  # type: ignore[method-assign]

    def boom(_partial: PartialStats) -> None:
        raise RuntimeError("the bar broke")

    stats = await orchestrator.run(
        [Company(ticker="AAA", name="AAA Inc.")], date_range=RANGE, progress=boom
    )
    assert stats.errors == []
    assert stats.total_inserted == 1


@pytest.mark.usefixtures("every_entry_a_candidate")
async def test_the_domain_response_carries_the_count_it_did_not_store(
    db: str, offline: Site
) -> None:
    """`DiscoverResponse` of the per-domain routes (/cnbc/, /ft/, ...)."""
    [response] = await _common.run_domain_discovery(
        request(db, "AAA"), rejecting_connector(), MagicMock(), DOMAIN
    )
    assert response.errors == []
    assert response.inserted == 1
    assert response.rejected_not_stored == 1
    assert [d.match_class for d in response.discovered] == ["accept"]


class _Orchestrator:
    def __init__(self, **_: object) -> None: ...

    async def run(self, **_: object) -> DiscoveryStats:
        return DiscoveryStats(total_inserted=3, total_rejected_not_stored=7)


def test_the_run_all_response_carries_the_count_it_did_not_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`RunAllResponse` of POST /discovery/run-all, and its default for a run with none."""
    app = FastAPI()
    app.include_router(discovery.router)
    app.dependency_overrides[get_client] = lambda: None
    monkeypatch.setattr(discovery, "DiscoveryOrchestrator", _Orchestrator)
    monkeypatch.setattr(discovery, "build_connectors", lambda _client: {})
    body = {
        "tickers": ["AAPL"],
        "company_names": ["Apple Inc."],
        "start_date": "2026-01-01",
        "db_path": str(tmp_path / "q.db"),
    }
    response = TestClient(app).post("/discovery/run-all", json=body)
    assert response.status_code == 200
    assert response.json()["total_rejected_not_stored"] == 7
    assert response.json()["total_rejected"] == 0
    without = {k: v for k, v in response.json().items() if k != "total_rejected_not_stored"}
    assert RunAllResponse(**without).total_rejected_not_stored == 0


class _CliOrchestrator:
    def __init__(self, **_: object) -> None: ...

    async def run(self, **kwargs: object) -> DiscoveryStats:
        progress = kwargs["progress"]
        kwargs["on_start"](2)  # type: ignore[operator]
        for _ in range(2):  # two pairs finish
            progress(PartialStats(company="x", domain="d"))  # type: ignore[operator]
        return DiscoveryStats(total_inserted=2, total_rejected_not_stored=5)


class _Client:
    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def test_the_cli_has_a_bar_that_does_not_break_a_non_tty_run_and_prints_the_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bars: list[tqdm] = []

    updates: list[int] = []

    class Recording(tqdm):  # type: ignore[misc]
        def __init__(self, *a: object, **kw: object) -> None:
            super().__init__(*a, **kw)
            bars.append(self)

        def update(self, n: float | None = 1) -> bool | None:
            updates.append(int(n or 0))
            return super().update(n)

    async def lookup(tickers: list[str], _c: object, _s: object) -> list[Company]:
        return [Company(ticker=t, name=f"{t} Inc.") for t in tickers]

    monkeypatch.setattr(collector_main, "tqdm", Recording)
    monkeypatch.setattr(collector_main, "DiscoveryOrchestrator", _CliOrchestrator)
    monkeypatch.setattr(collector_main, "build_client", _Client)
    monkeypatch.setattr(collector_main, "build_connectors", lambda _client: {"cnbc.com": object()})
    monkeypatch.setattr(collector_main, "lookup_companies", lookup)
    args = collector_main.build_parser().parse_args(
        ["--db", str(tmp_path / "q.db"), "discover", "--tickers", "AAPL", "MSFT"]
    )

    assert asyncio.run(collector_main.cmd_discover(args)) == 0

    captured = capsys.readouterr()
    assert "Sitemap rejects not stored: 5 (read from the cache)" in captured.out
    [bar] = bars
    assert bar.disable is True  # not a terminal (`disable=None`): nothing is written into a log
    assert updates == [1, 1]  # one tick per finished pair, and the run went on
    assert bar.total == 2  # the total the orchestrator reported, not companies x domains
    assert "%|" not in captured.err  # no bar drawn in a pipe


@pytest.mark.usefixtures("every_entry_a_candidate")
async def test_on_start_reports_only_the_pairs_that_will_run_when_resume_skips_some(
    db: str, offline: Site
) -> None:
    queue = URLQueue(db)
    queue.initialize()
    queue.mark_pair_completed("AAA", DOMAIN, RANGE.start, RANGE.end)  # a prior run did this one
    orchestrator = DiscoveryOrchestrator(
        connectors={DOMAIN: rejecting_connector()}, queue=queue, concurrency=1
    )
    orchestrator._fetch_via_extra_api = no_api  # type: ignore[method-assign]
    totals: list[int] = []
    ticks: list[PartialStats] = []
    companies = [Company(ticker=t, name=f"{t} Inc.") for t in ("AAA", "BBB")]
    await orchestrator.run(
        companies,
        date_range=RANGE,
        resume=True,
        progress=ticks.append,
        on_start=totals.append,
    )
    assert totals == [1]  # two pairs in the grid, one skipped
    assert len(ticks) == 1  # the bar ends at its total


@pytest.mark.usefixtures("every_entry_a_candidate")
async def test_an_on_start_callback_that_raises_never_stops_the_run(db: str, offline: Site) -> None:
    orchestrator = DiscoveryOrchestrator(
        connectors={DOMAIN: rejecting_connector()}, queue=URLQueue(db), concurrency=1
    )
    orchestrator._fetch_via_extra_api = no_api  # type: ignore[method-assign]

    def boom(_total: int) -> None:
        raise RuntimeError("the bar broke")

    stats = await orchestrator.run(
        [Company(ticker="AAA", name="AAA Inc.")], date_range=RANGE, on_start=boom
    )
    assert stats.errors == []
    assert stats.total_inserted == 1
