"""The sitemap cache in the orchestrator, the CLI and the API routes (T-070a): the flag, resume, the warnings, one error per domain."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
from sitemap_helpers import (
    DOMAIN,
    RANGE,
    ROOT,
    TWO,
    Site,
    fake_connector,
    request,
    rows,
    standard_site,
    strategy_for,
)

from news_collector import main as collector_main
from news_collector.api.routers import _common
from news_collector.api.schemas import RunAllRequest
from news_collector.models import Company, DiscoveryStats
from news_collector.orchestrator import DiscoveryOrchestrator
from news_collector.sitemap_cache import SitemapCache, shared_cache
from news_collector.storage.queue import URLQueue
from news_collector.strategies.ddg import DDGStrategy


async def test_two_api_requests_for_two_tickers_make_one_crawl(db: str, offline: Site) -> None:
    connector, client = fake_connector(), MagicMock()
    first, second = await asyncio.gather(
        _common.run_domain_discovery(request(db, "AAA"), connector, client, DOMAIN),
        _common.run_domain_discovery(request(db, "BBB"), connector, client, DOMAIN),
    )
    assert offline.crawls() == 1
    assert [r.errors for r in first + second] == [[], []]
    assert first[0].sitemap_count == second[0].sitemap_count == TWO  # both read the same rows


async def test_the_orchestrator_and_an_api_route_share_the_cache_of_the_process(
    db: str, offline: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = MagicMock()
    connector = fake_connector(client)
    orchestrator = DiscoveryOrchestrator(
        connectors={DOMAIN: connector}, queue=URLQueue(db), concurrency=1
    )
    monkeypatch.setattr(DDGStrategy, "discover", _common.DDGStrategy.discover)  # one patched class

    async def no_api(*_: object) -> list:
        return []

    orchestrator._fetch_via_extra_api = no_api  # type: ignore[method-assign]
    await orchestrator.run([Company(ticker="ACME", name="Acme")], date_range=RANGE)
    assert offline.crawls() == 1
    [route] = await _common.run_domain_discovery(request(db, "BBB"), connector, client, DOMAIN)
    assert route.errors == []
    assert offline.crawls() == 1  # the route found the orchestrator's crawl: fresh, covering
    assert shared_cache(connector, client, db) is shared_cache(connector, client, db)


async def test_no_cache_argument_keeps_the_live_fetch(db: str) -> None:
    site = standard_site()
    strategy = strategy_for(None, site)
    await strategy.discover("Acme", "ACME", RANGE)
    await strategy.discover("Beta", "BETA", RANGE)
    assert site.crawls() == TWO  # once per ticker, as before
    assert rows(db, "SELECT COUNT(*) FROM sitemap_crawls") == [(0,)]


async def test_a_cache_that_cannot_open_fails_its_domain_and_the_others_run(
    db: str, offline: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    sitemap_domain = fake_connector()
    plain = fake_connector()
    plain.config.sitemap_roots = []
    plain.config.domain = "plain.test"
    orchestrator = DiscoveryOrchestrator(
        connectors={DOMAIN: sitemap_domain, "plain.test": plain}, queue=URLQueue(db), concurrency=1
    )

    def broken(_self: SitemapCache) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(SitemapCache, "_latest_complete", broken)

    async def no_api(*_: object) -> list:
        return []

    orchestrator._fetch_via_extra_api = no_api  # type: ignore[method-assign]
    stats = await orchestrator.run([Company(ticker="ACME", name="Acme")], date_range=RANGE)
    assert any("database is locked" in e for e in stats.errors)  # the sitemap domain's pair
    assert not any("plain.test" in e for e in stats.errors)  # the other domain ran
    reader = URLQueue(db)
    reader.initialize()
    done = reader.completed_pairs(["plain.test", DOMAIN], RANGE.start, RANGE.end)
    assert ("ACME", "plain.test") in done
    assert ("ACME", DOMAIN) not in done


async def test_a_domain_whose_pairs_are_all_skipped_by_resume_is_not_crawled(
    db: str, offline: Site
) -> None:
    connector = fake_connector()
    queue = URLQueue(db)
    queue.initialize()
    queue.mark_pair_completed("ACME", DOMAIN, RANGE.start, RANGE.end)
    orchestrator = DiscoveryOrchestrator(connectors={DOMAIN: connector}, queue=queue, concurrency=1)
    await orchestrator.run([Company(ticker="ACME", name="Acme")], date_range=RANGE, resume=True)
    assert offline.crawls() == 0
    assert orchestrator._caches == {}


async def test_a_domain_without_sitemap_roots_makes_no_cache(db: str, offline: Site) -> None:
    connector = fake_connector()
    connector.config.sitemap_roots = []
    assert shared_cache(connector, MagicMock(), db) is None


async def test_the_run_all_flag_reaches_the_cache(db: str, offline: Site) -> None:
    connector = fake_connector()
    orchestrator = DiscoveryOrchestrator(
        connectors={DOMAIN: connector}, queue=URLQueue(db), concurrency=1
    )

    async def no_api(*_: object) -> list:
        return []

    orchestrator._fetch_via_extra_api = no_api  # type: ignore[method-assign]
    ana = [Company(ticker="ACME", name="Acme")]
    await orchestrator.run(ana, date_range=RANGE, resume=False)
    await orchestrator.run(ana, date_range=RANGE, resume=False)  # fresh: no crawl
    assert offline.crawls() == 1
    await orchestrator.run(ana, date_range=RANGE, resume=False, refresh_sitemaps=True)
    assert offline.crawls() == TWO


async def test_the_per_domain_flag_forces_a_crawl_and_defaults_to_off(
    db: str, offline: Site
) -> None:
    connector, client = fake_connector(), MagicMock()
    assert request(db, "AAA").refresh_sitemaps is False
    await _common.run_domain_discovery(request(db, "AAA"), connector, client, DOMAIN)
    await _common.run_domain_discovery(request(db, "BBB"), connector, client, DOMAIN)
    assert offline.crawls() == 1
    await _common.run_domain_discovery(
        request(db, "CCC", refresh_sitemaps=True), connector, client, DOMAIN
    )
    assert offline.crawls() == TWO


def test_the_run_all_request_has_the_flag_off_by_default() -> None:
    assert RunAllRequest().refresh_sitemaps is False
    assert RunAllRequest(refresh_sitemaps=True).refresh_sitemaps is True


def test_the_cli_flag_is_off_by_default_and_reaches_the_orchestrator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[bool] = []

    class Recording:
        def __init__(self, **_: object) -> None:
            pass

        async def run(self, **kw: object) -> DiscoveryStats:
            seen.append(cast(bool, kw["refresh_sitemaps"]))
            return DiscoveryStats()

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    async def lookup(tickers: list[str], client: object, start: object) -> list[Company]:
        return [Company(ticker=t, name=f"{t} Inc.") for t in tickers]

    monkeypatch.setattr(collector_main, "DiscoveryOrchestrator", Recording)
    monkeypatch.setattr(collector_main, "build_client", FakeClient)
    monkeypatch.setattr(collector_main, "build_connectors", lambda client: [])
    monkeypatch.setattr(collector_main, "lookup_companies", lookup)
    base = ["--db", str(tmp_path / "q.db"), "--log-dir", "", "discover", "--tickers", "AAPL"]
    for extra in ([], ["--refresh-sitemaps"]):
        args = collector_main.build_parser().parse_args(base + extra)
        asyncio.run(collector_main.cmd_discover(args))
    assert seen == [False, True]


async def test_the_warning_reaches_the_orchestrator_stats_and_the_api_response(
    db: str, offline: Site
) -> None:
    connector, client = fake_connector(), MagicMock()
    orchestrator = DiscoveryOrchestrator(
        connectors={DOMAIN: connector}, queue=URLQueue(db), concurrency=1
    )

    async def no_api(*_: object) -> list:
        return []

    orchestrator._fetch_via_extra_api = no_api  # type: ignore[method-assign]
    ana = [Company(ticker="ACME", name="Acme")]
    clean = await orchestrator.run(ana, date_range=RANGE, resume=False)
    assert clean.warnings == []
    offline.down.add(ROOT)
    warned = await orchestrator.run(ana, date_range=RANGE, resume=False, refresh_sitemaps=True)
    assert len(warned.warnings) == 1
    assert warned.errors == []  # not an error: the pair was checkpointed
    [response] = await _common.run_domain_discovery(
        request(db, "BBB", refresh_sitemaps=True), connector, client, DOMAIN
    )
    assert len(response.warnings) == 1
    assert response.errors == []


def test_the_cli_summary_prints_the_warnings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Recording:
        def __init__(self, **_: object) -> None:
            pass

        async def run(self, **_: object) -> DiscoveryStats:
            return DiscoveryStats(warnings=["sitemap refresh of cnbc.com failed (x); old crawl"])

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    async def lookup(tickers: list[str], client: object, start: object) -> list[Company]:
        return [Company(ticker=t, name=f"{t} Inc.") for t in tickers]

    monkeypatch.setattr(collector_main, "DiscoveryOrchestrator", Recording)
    monkeypatch.setattr(collector_main, "build_client", FakeClient)
    monkeypatch.setattr(collector_main, "build_connectors", lambda client: [])
    monkeypatch.setattr(collector_main, "lookup_companies", lookup)
    argv = ["--db", str(tmp_path / "q.db"), "--log-dir", "", "discover", "--tickers", "AAPL"]
    code = asyncio.run(collector_main.cmd_discover(collector_main.build_parser().parse_args(argv)))
    out = capsys.readouterr().out
    assert code == 0  # a warning is not a failure of the run
    assert "Warnings         : 1" in out
    assert "old crawl" in out


async def test_an_unusable_domain_is_one_error_in_the_run_and_every_pair_is_not_checkpointed(
    db: str, offline: Site
) -> None:
    offline.pages.clear()  # the sitemap is down
    connector = fake_connector()
    queue = URLQueue(db)
    orchestrator = DiscoveryOrchestrator(connectors={DOMAIN: connector}, queue=queue, concurrency=1)

    async def no_api(*_: object) -> list:
        return []

    orchestrator._fetch_via_extra_api = no_api  # type: ignore[method-assign]
    companies = [Company(ticker=t, name=f"{t} Inc.") for t in ("AAA", "BBB", "CCC")]
    stats = await orchestrator.run(companies, date_range=RANGE, resume=False)
    assert len(stats.errors) == 1  # one line for the domain, not three
    assert stats.errors[0].startswith(f"{DOMAIN}: ")
    assert stats.errors[0].endswith("(3 pairs not checkpointed)")  # how much was left undone
    reader = URLQueue(db)
    reader.initialize()
    assert reader.completed_pairs([DOMAIN], RANGE.start, RANGE.end) == set()  # none checkpointed
