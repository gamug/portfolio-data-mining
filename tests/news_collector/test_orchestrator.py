"""Tests for DiscoveryOrchestrator's checkpoint/resume behavior."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from news_collector.models import Company, DateRange, PartialStats
from news_collector.orchestrator import DiscoveryOrchestrator
from news_collector.storage.queue import URLQueue

_NO_ROOTS = SimpleNamespace(
    config=SimpleNamespace(sitemap_roots=[])
)  # a connector with no sitemap (T-070a: no cache)


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[URLQueue]:
    q = URLQueue(str(tmp_path / "resume_test.db"))
    q.initialize()
    try:
        yield q
    finally:
        q.close()


def _orchestrator(queue: URLQueue) -> DiscoveryOrchestrator:
    # _run_one is monkeypatched in each test below, so the connector dict only
    # needs to supply the domain keys the orchestrator iterates over — no real
    # connector instance or network access is required.
    connectors = {"cnbc.com": _NO_ROOTS, "nasdaq.com": _NO_ROOTS}
    return DiscoveryOrchestrator(connectors=connectors, queue=queue, concurrency=5)  # type: ignore[arg-type]


async def test_resume_skips_already_completed_pairs(queue: URLQueue) -> None:
    date_range = DateRange(start=date(2024, 1, 1), end=date(2024, 1, 31))
    queue.mark_pair_completed(
        "AAPL", "cnbc.com", date_range.start, date_range.end, inserted_count=3
    )

    orch = _orchestrator(queue)
    seen: list[tuple[str, str]] = []

    async def fake_run_one(company: Company, domain: str, date_range: DateRange) -> PartialStats:
        seen.append((company.ticker, domain))
        return PartialStats(company=company.name, domain=domain, inserted_count=1)

    orch._run_one = fake_run_one  # type: ignore[method-assign]

    companies = [Company(ticker="AAPL", name="Apple"), Company(ticker="MSFT", name="Microsoft")]
    stats = await orch.run(
        companies, domains=["cnbc.com", "nasdaq.com"], date_range=date_range, resume=True
    )

    assert ("AAPL", "cnbc.com") not in seen
    assert set(seen) == {("AAPL", "nasdaq.com"), ("MSFT", "cnbc.com"), ("MSFT", "nasdaq.com")}
    assert stats.skipped_pairs == 1


async def test_without_resume_flag_reruns_everything(queue: URLQueue) -> None:
    date_range = DateRange(start=date(2024, 1, 1), end=date(2024, 1, 31))
    queue.mark_pair_completed(
        "AAPL", "cnbc.com", date_range.start, date_range.end, inserted_count=3
    )

    orch = _orchestrator(queue)
    seen: list[tuple[str, str]] = []

    async def fake_run_one(company: Company, domain: str, date_range: DateRange) -> PartialStats:
        seen.append((company.ticker, domain))
        return PartialStats(company=company.name, domain=domain, inserted_count=1)

    orch._run_one = fake_run_one  # type: ignore[method-assign]

    companies = [Company(ticker="AAPL", name="Apple")]
    stats = await orch.run(companies, domains=["cnbc.com"], date_range=date_range, resume=False)

    assert ("AAPL", "cnbc.com") in seen
    assert stats.skipped_pairs == 0


async def test_resume_does_not_skip_a_different_date_range(queue: URLQueue) -> None:
    completed_range = DateRange(start=date(2024, 1, 1), end=date(2024, 1, 31))
    queue.mark_pair_completed("AAPL", "cnbc.com", completed_range.start, completed_range.end)

    orch = _orchestrator(queue)
    seen: list[tuple[str, str]] = []

    async def fake_run_one(company: Company, domain: str, date_range: DateRange) -> PartialStats:
        seen.append((company.ticker, domain))
        return PartialStats(company=company.name, domain=domain, inserted_count=1)

    orch._run_one = fake_run_one  # type: ignore[method-assign]

    new_range = DateRange(start=date(2024, 2, 1), end=date(2024, 2, 29))
    companies = [Company(ticker="AAPL", name="Apple")]
    stats = await orch.run(companies, domains=["cnbc.com"], date_range=new_range, resume=True)

    assert ("AAPL", "cnbc.com") in seen  # different range => not a match, must run again
    assert stats.skipped_pairs == 0


async def test_run_one_counts_rejected_rows_and_dedupe_keeps_the_higher_class(
    queue: URLQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime  # noqa: PLC0415

    from news_collector.models import DiscoveredURL  # noqa: PLC0415
    from news_collector.strategies.ddg import DDGStrategy  # noqa: PLC0415
    from news_collector.strategies.sitemap import SitemapRSSStrategy  # noqa: PLC0415

    def row(url: str, cls: str, source: str, query: str | None = None) -> DiscoveredURL:
        return DiscoveredURL(
            url=url,
            domain="cnbc.com",
            company="Apple",
            ticker="AAPL",
            source=source,  # type: ignore[arg-type]
            discovered_at=datetime(2026, 8, 10),
            match_class=cls,  # type: ignore[arg-type]
            match_reason=cls,
            query=query,
        )

    async def ddg(self, company, ticker, date_range):  # type: ignore[no-untyped-def]
        return [
            row("https://cnbc.com/a", "reject", "ddg", "q1"),
            row("https://cnbc.com/b", "reject", "ddg", "q1"),
        ]

    async def sitemap(self, company, ticker, date_range):  # type: ignore[no-untyped-def]
        return [row("https://cnbc.com/a", "accept", "rss")]

    monkeypatch.setattr(DDGStrategy, "discover", ddg)
    monkeypatch.setattr(SitemapRSSStrategy, "discover", sitemap)

    class _Conn:
        _client = None
        config = _NO_ROOTS.config

    orch = DiscoveryOrchestrator(connectors={"cnbc.com": _Conn()}, queue=queue, concurrency=1)  # type: ignore[arg-type]
    partial = await orch._run_one(
        Company(ticker="AAPL", name="Apple"),
        "cnbc.com",
        DateRange(start=date(2024, 1, 1), end=date(2024, 1, 31)),
    )

    assert (partial.inserted_count, partial.rejected_count, partial.duplicate_count) == (2, 1, 1)
    stored, _total = queue.query(limit=10)
    assert {u.url: (u.match_class, u.status, u.query) for u in stored} == {
        "https://cnbc.com/a": ("accept", "pending", "q1"),
        "https://cnbc.com/b": ("reject", "skipped", "q1"),
    }

    # the same pair found again inserts nothing, so it reports no new rejected row
    again = await orch._run_one(
        Company(ticker="AAPL", name="Apple"),
        "cnbc.com",
        DateRange(start=date(2024, 1, 1), end=date(2024, 1, 31)),
    )
    assert (again.inserted_count, again.rejected_count) == (0, 0)
