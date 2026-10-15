"""The strategies keep `review` and `reject` pairs with their class (T-056c)."""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import MagicMock

import pytest

from news_collector.connectors.cnbc import CNBCConnector
from news_collector.models import DateRange, SitemapEntry
from news_collector.strategies.ddg import DDGStrategy
from news_collector.strategies.sitemap import SitemapRSSStrategy

_ACCEPT = "https://www.cnbc.com/2024/01/15/apple-inc-earnings-beat.html"
_REVIEW = "https://www.cnbc.com/2024/01/15/aapl-earnings.html"
_REJECT = "https://www.cnbc.com/2024/01/15/oil-prices-rise.html"
_RANGE = DateRange(start=date(2024, 1, 1), end=date(2024, 1, 31))


@pytest.fixture
def connector() -> CNBCConnector:
    return CNBCConnector(client=MagicMock())


def test_sitemap_keeps_every_class_with_its_reason(connector: CNBCConnector) -> None:
    strategy = SitemapRSSStrategy(connector, MagicMock())
    entries = [SitemapEntry(url=u) for u in (_ACCEPT, _REVIEW, _REJECT)]

    rows = strategy._dedupe_and_filter(entries, "Apple Inc.", "AAPL")

    assert {r.url: r.match_class for r in rows} == {
        _ACCEPT: "accept",
        _REVIEW: "review",
        _REJECT: "reject",
    }
    assert all(r.match_reason for r in rows)
    assert all(r.query is None for r in rows)


def test_sitemap_uses_an_rss_title_as_evidence(connector: CNBCConnector) -> None:
    strategy = SitemapRSSStrategy(connector, MagicMock())

    (row,) = strategy._dedupe_and_filter(
        [SitemapEntry(url=_REJECT, title="Apple Inc. surges")], "Apple Inc.", "AAPL"
    )

    assert row.match_class == "accept"


def test_sitemap_still_drops_non_article_urls(connector: CNBCConnector) -> None:
    strategy = SitemapRSSStrategy(connector, MagicMock())

    rows = strategy._dedupe_and_filter(
        [SitemapEntry(url="https://www.cnbc.com/technology/")], "Apple Inc.", "AAPL"
    )

    assert rows == []


def test_ddg_keeps_every_class_with_the_query(
    connector: CNBCConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    strategy = DDGStrategy(connector, asyncio.Semaphore(1))
    monkeypatch.setattr("news_collector.strategies.ddg._DDG_SLEEP_MIN", 0)
    monkeypatch.setattr("news_collector.strategies.ddg._DDG_SLEEP_JITTER", 0)
    monkeypatch.setattr(connector, "build_ddg_queries", lambda *a, **k: ["q one"])

    async def fake_search(query: str, max_results: int) -> list[dict]:
        return [
            {"href": _ACCEPT, "title": "Apple Inc. beats"},
            {"href": _REVIEW, "title": ""},
            {"href": _REJECT, "title": "Oil prices rise"},
        ]

    monkeypatch.setattr(strategy, "_ddg_search", fake_search)

    rows = asyncio.run(strategy.discover("Apple Inc.", "AAPL", _RANGE))

    assert {r.url: r.match_class for r in rows} == {
        _ACCEPT: "accept",
        _REVIEW: "review",
        _REJECT: "reject",
    }
    assert {r.query for r in rows} == {"q one"}
    assert all(r.match_reason for r in rows)
