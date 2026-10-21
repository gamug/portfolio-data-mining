"""A failed sitemap root or child is an error and the pair is not checkpointed (T-076)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

import news_collector.orchestrator as mod
from data_mining.matching import Match
from news_collector import sitemap_cache
from news_collector.api.routers import _common
from news_collector.connectors.ft import FTConnector
from news_collector.connectors.nasdaq import NasdaqConnector
from news_collector.models import Company, DateRange
from news_collector.orchestrator import DiscoveryOrchestrator
from news_collector.storage.queue import URLQueue
from news_collector.strategies.sitemap import SitemapRSSStrategy, summarize_failures

_RANGE = DateRange(start=date(2022, 1, 1), end=date(2026, 8, 10))
_NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
_ROOT = "https://example.test/sitemap.xml"
_CHILD_OK = "https://example.test/ok.xml"
_CHILD_BAD = "https://example.test/bad.xml"
_INDEX = (
    f"<sitemapindex {_NS}><sitemap><loc>{_CHILD_OK}</loc></sitemap>"
    f"<sitemap><loc>{_CHILD_BAD}</loc></sitemap></sitemapindex>"
)
_LEAF = f"<urlset {_NS}><url><loc>https://example.test/a-1</loc></url></urlset>"


class _Pages:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages

    async def fetch(self, url: str) -> str:
        if url not in self.pages:
            raise httpx.HTTPStatusError("403", request=MagicMock(), response=MagicMock())
        return self.pages[url]


def _strategy(pages: dict[str, str], roots: list[str]) -> SitemapRSSStrategy:
    connector = MagicMock()
    connector.config.sitemap_roots = roots
    connector.config.rss_feeds = []
    connector.config.wayback_fallback = False
    connector.config.domain = "example.test"
    connector.is_article_url.return_value = True
    connector.explain_match.return_value = Match("accept", "name")
    strategy = SitemapRSSStrategy(connector, MagicMock())
    strategy._fetch = _Pages(pages).fetch  # type: ignore[method-assign]
    return strategy


async def test_failed_root_is_listed() -> None:
    strategy = _strategy({}, [_ROOT])
    assert await strategy.discover("Acme", "ACME", _RANGE) == []
    assert len(strategy.failures) == 1
    assert _ROOT in strategy.failures[0]


async def test_failed_child_is_listed_and_the_good_child_is_kept() -> None:
    strategy = _strategy({_ROOT: _INDEX, _CHILD_OK: _LEAF}, [_ROOT])
    urls = await strategy.discover("Acme", "ACME", _RANGE)
    assert [u.url for u in urls] == ["https://example.test/a-1"]
    assert len(strategy.failures) == 1
    assert _CHILD_BAD in strategy.failures[0]


async def test_unparseable_xml_is_listed() -> None:
    strategy = _strategy({_ROOT: "<not-xml"}, [_ROOT])
    await strategy.discover("Acme", "ACME", _RANGE)
    assert len(strategy.failures) == 1


async def test_failures_reset_on_each_discover() -> None:
    strategy = _strategy({}, [_ROOT])
    await strategy.discover("Acme", "ACME", _RANGE)
    await strategy.discover("Acme", "ACME", _RANGE)
    assert len(strategy.failures) == 1


async def test_domain_without_roots_has_no_failures() -> None:
    strategy = _strategy({}, [])
    await strategy.discover("Acme", "ACME", _RANGE)
    assert strategy.failures == []


@pytest.mark.parametrize("connector_cls", [FTConnector, NasdaqConnector])
def test_blocked_sources_have_no_dead_sitemap_roots(connector_cls: type) -> None:
    assert connector_cls(MagicMock()).config.sitemap_roots == []


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[URLQueue]:
    q = URLQueue(str(tmp_path / "t076.db"))
    q.initialize()
    try:
        yield q
    finally:
        q.close()


def _orchestrator(queue: URLQueue) -> DiscoveryOrchestrator:
    connector = MagicMock()
    connector.config.sitemap_roots = [_ROOT]
    connector.config.rss_feeds = []
    connector.config.wayback_fallback = False
    connector.config.domain = "example.test"
    connector.is_article_url.return_value = True
    connector.explain_match.return_value = Match("accept", "name")
    connector._client = MagicMock()
    orch = DiscoveryOrchestrator(connectors={"example.test": connector}, queue=queue, concurrency=1)  # type: ignore[arg-type]

    async def no_api(*_: object) -> list:
        return []

    orch._fetch_via_extra_api = no_api  # type: ignore[method-assign]
    return orch


def _done(queue: URLQueue) -> bool:
    return ("ACME", "example.test") in queue.completed_pairs(
        ["example.test"], _RANGE.start, _RANGE.end
    )


async def _run(orch: DiscoveryOrchestrator, monkeypatch: pytest.MonkeyPatch, pages: dict[str, str]):
    async def no_ddg(self, *_: object) -> list:
        return []

    async def fetch(_client: object, url: str) -> str:
        return await _Pages(pages).fetch(url)

    monkeypatch.setattr(mod.DDGStrategy, "discover", no_ddg)
    # the orchestrator reads the sitemap through its cache (T-070a), which fetches with this
    monkeypatch.setattr(sitemap_cache, "fetch_text", fetch)
    return await orch.run(
        [Company(ticker="ACME", name="Acme")], domains=["example.test"], date_range=_RANGE
    )


async def test_failed_domain_is_an_error_and_a_later_run_retries(
    queue: URLQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(queue)
    stats = await _run(orch, monkeypatch, {})
    assert len(stats.errors) == 1
    assert _ROOT in stats.errors[0]
    assert not _done(queue)

    # the source recovers and the next run is a new process (the CLI; a gateway would wait out the
    # 5-minute back-off of the cache, or be forced): the pair is not skipped and is checkpointed
    sitemap_cache.reset_shared_caches()
    stats = await _run(orch, monkeypatch, {_ROOT: _LEAF})
    assert stats.errors == []
    assert _done(queue)


async def test_failed_child_is_an_error_and_not_checkpointed(
    queue: URLQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = {_ROOT: _INDEX, _CHILD_OK: _LEAF}
    stats = await _run(_orchestrator(queue), monkeypatch, pages)
    assert len(stats.errors) == 1
    assert _CHILD_BAD in stats.errors[0]
    assert not _done(queue)


async def test_strategy_that_raises_is_an_error_and_not_checkpointed(
    queue: URLQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(self: object, *_: object) -> list:
        raise RuntimeError("boom")

    monkeypatch.setattr(mod.SitemapRSSStrategy, "discover", boom)
    stats = await _run(_orchestrator(queue), monkeypatch, {})
    assert len(stats.errors) == 1
    assert "boom" in stats.errors[0]
    assert not _done(queue)


def test_summarize_failures_is_bounded() -> None:
    few = [f"f{i}" for i in range(3)]
    many = [f"f{i}" for i in range(500)]
    assert summarize_failures(few) == "f0; f1; f2"
    assert summarize_failures(many) == "f0; f1; f2; and 497 more"


async def test_many_dead_children_give_one_short_error(
    queue: URLQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    children = [f"https://example.test/c{i}.xml" for i in range(200)]
    index = (
        f"<sitemapindex {_NS}>"
        + "".join(f"<sitemap><loc>{c}</loc></sitemap>" for c in children)
        + "</sitemapindex>"
    )
    stats = await _run(_orchestrator(queue), monkeypatch, {_ROOT: index})
    assert len(stats.errors) == 1
    assert "and 197 more" in stats.errors[0]
    assert len(stats.errors[0]) < 600


async def test_api_route_lists_a_failed_root_in_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_ddg(self: object, *_: object) -> list:
        return []

    async def fetch(self: object, url: str) -> str:
        return await _Pages({}).fetch(url)

    monkeypatch.setattr(_common.DDGStrategy, "discover", no_ddg)
    monkeypatch.setattr(_common.SitemapRSSStrategy, "_fetch", fetch)
    connector = MagicMock()
    connector.config.sitemap_roots = [_ROOT]
    connector.config.rss_feeds = []
    connector.config.wayback_fallback = False
    _, _, _, errors, _ = await _common._discover_one_ticker(
        Company(ticker="ACME", name="Acme"), connector, MagicMock(), _RANGE, None
    )
    assert len(errors) == 1
    assert _ROOT in errors[0]
