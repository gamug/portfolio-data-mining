"""Tests for DiscoveryOrchestrator's checkpoint/resume behavior."""

from __future__ import annotations

from datetime import date

import pytest

from news_collector.models import Company, DateRange, PartialStats
from news_collector.orchestrator import DiscoveryOrchestrator
from news_collector.storage.queue import URLQueue


@pytest.fixture
def queue(tmp_path) -> URLQueue:
    q = URLQueue(str(tmp_path / "resume_test.db"))
    q.initialize()
    return q


def _orchestrator(queue: URLQueue) -> DiscoveryOrchestrator:
    # _run_one is monkeypatched in each test below, so the connector dict only
    # needs to supply the domain keys the orchestrator iterates over — no real
    # connector instance or network access is required.
    connectors = {"cnbc.com": object(), "nasdaq.com": object()}
    return DiscoveryOrchestrator(connectors=connectors, queue=queue, concurrency=5)  # type: ignore[arg-type]


async def test_resume_skips_already_completed_pairs(queue: URLQueue) -> None:
    date_range = DateRange(start=date(2024, 1, 1), end=date(2024, 1, 31))
    queue.mark_pair_completed("AAPL", "cnbc.com", date_range.start, date_range.end, inserted_count=3)

    orch = _orchestrator(queue)
    seen: list[tuple[str, str]] = []

    async def fake_run_one(company: Company, domain: str, dr: DateRange) -> PartialStats:
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
    queue.mark_pair_completed("AAPL", "cnbc.com", date_range.start, date_range.end, inserted_count=3)

    orch = _orchestrator(queue)
    seen: list[tuple[str, str]] = []

    async def fake_run_one(company: Company, domain: str, dr: DateRange) -> PartialStats:
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

    async def fake_run_one(company: Company, domain: str, dr: DateRange) -> PartialStats:
        seen.append((company.ticker, domain))
        return PartialStats(company=company.name, domain=domain, inserted_count=1)

    orch._run_one = fake_run_one  # type: ignore[method-assign]

    new_range = DateRange(start=date(2024, 2, 1), end=date(2024, 2, 29))
    companies = [Company(ticker="AAPL", name="Apple")]
    stats = await orch.run(companies, domains=["cnbc.com"], date_range=new_range, resume=True)

    assert ("AAPL", "cnbc.com") in seen  # different range => not a match, must run again
    assert stats.skipped_pairs == 0
