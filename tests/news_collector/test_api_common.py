"""Tests for the shared per-domain discovery helper in api/routers/_common.py."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from news_collector.api.routers import _common
from news_collector.api.schemas import DiscoverRequest, RunAllRequest
from news_collector.models import Company, DateRange, DiscoveredURL


@pytest.fixture(autouse=True)
def _restore_discover_one_ticker():
    """Each test patches _discover_one_ticker; restore the real one afterward."""
    original = _common._discover_one_ticker
    yield
    _common._discover_one_ticker = original


async def test_run_domain_discovery_respects_concurrency_limit(tmp_path, monkeypatch) -> None:
    in_flight = 0
    max_in_flight = 0

    async def fake_discover_one_ticker(company, connector, client, date_range, extra_fetch=None):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return [], [], [], []

    monkeypatch.setattr(_common, "_discover_one_ticker", fake_discover_one_ticker)

    req = DiscoverRequest(
        tickers=[f"T{i}" for i in range(10)],
        db_path=str(tmp_path / "conc.db"),
        concurrency=3,
    )
    responses = await _common.run_domain_discovery(
        req, connector=object(), client=object(), domain="cnbc.com"
    )

    assert len(responses) == 10
    assert max_in_flight <= 3


async def test_run_domain_discovery_default_concurrency_is_five(tmp_path, monkeypatch) -> None:
    max_in_flight = 0
    in_flight = 0

    async def fake_discover_one_ticker(company, connector, client, date_range, extra_fetch=None):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return [], [], [], []

    monkeypatch.setattr(_common, "_discover_one_ticker", fake_discover_one_ticker)

    req = DiscoverRequest(tickers=[f"T{i}" for i in range(10)], db_path=str(tmp_path / "conc2.db"))
    await _common.run_domain_discovery(req, connector=object(), client=object(), domain="cnbc.com")

    assert max_in_flight <= 5


# ---------------------------------------------------------------------------
# resolve_companies: explicit tickers vs. Wikipedia S&P 500 fallback
# ---------------------------------------------------------------------------


async def test_resolve_companies_uses_explicit_tickers_when_given(monkeypatch) -> None:
    async def fail_if_called(client):
        raise AssertionError("fetch_sp500_companies should not be called when tickers is given")

    monkeypatch.setattr(_common, "fetch_sp500_companies", fail_if_called)

    req = DiscoverRequest(tickers=["aapl", "MSFT"], company_names=["Apple Inc.", "Microsoft Corp."])
    companies = await _common.resolve_companies(req, client=object())

    assert companies == [
        Company(ticker="AAPL", name="Apple Inc."),
        Company(ticker="MSFT", name="Microsoft Corp."),
    ]


async def test_resolve_companies_uses_ticker_as_name_when_names_omitted() -> None:
    req = DiscoverRequest(tickers=["aapl"])
    companies = await _common.resolve_companies(req, client=object())
    assert companies == [Company(ticker="AAPL", name="AAPL")]


@pytest.mark.parametrize("req_cls", [DiscoverRequest, RunAllRequest])
async def test_resolve_companies_falls_back_to_wikipedia_when_tickers_omitted(
    monkeypatch, req_cls
) -> None:
    fake_companies = [Company(ticker="MMM", name="3M", sector="Industrials")]

    async def fake_fetch_sp500(client):
        return fake_companies

    monkeypatch.setattr(_common, "fetch_sp500_companies", fake_fetch_sp500)

    req = req_cls()  # tickers omitted entirely
    companies = await _common.resolve_companies(req, client=object())

    assert companies == fake_companies


async def test_resolve_companies_empty_tickers_list_also_falls_back(monkeypatch) -> None:
    fake_companies = [Company(ticker="MMM", name="3M")]

    async def fake_fetch_sp500(client):
        return fake_companies

    monkeypatch.setattr(_common, "fetch_sp500_companies", fake_fetch_sp500)

    req = DiscoverRequest(tickers=[])
    companies = await _common.resolve_companies(req, client=object())

    assert companies == fake_companies


async def test_resolve_companies_translates_wikipedia_failure_to_502(monkeypatch) -> None:
    async def fake_fetch_sp500(client):
        raise ValueError("Wikipedia's page markup may have changed")

    monkeypatch.setattr(_common, "fetch_sp500_companies", fake_fetch_sp500)

    req = DiscoverRequest()
    with pytest.raises(HTTPException) as exc_info:
        await _common.resolve_companies(req, client=object())

    assert exc_info.value.status_code == 502
