"""T-067: a ticker's company name is never defaulted to the ticker."""

from __future__ import annotations

from datetime import date

import httpx
import pytest
from fastapi import HTTPException

from news_collector.api.routers import _common
from news_collector.api.schemas import DiscoverRequest, RunAllRequest
from news_collector.models import Company

_CLIENT = httpx.AsyncClient()

_SP500 = [
    Company(ticker="KLAC", name="KLA Corporation", sector="Information Technology"),
    Company(ticker="AAPL", name="Apple Inc.", sector="Information Technology"),
]


@pytest.fixture
def wikipedia(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    async def fake(client: httpx.AsyncClient) -> list[Company]:
        calls.append(1)
        return _SP500

    monkeypatch.setattr(_common, "fetch_sp500_companies", fake)
    return calls


@pytest.fixture
def universe(monkeypatch: pytest.MonkeyPatch) -> list[date]:
    asked: list[date] = []

    def fake(as_of: date) -> list[dict]:
        asked.append(as_of)
        return [
            {"Symbol": "XRX", "Security": "Xerox Holdings"},
            {"Symbol": "KLAC", "Security": "x"},
        ]

    monkeypatch.setattr(_common, "query_as_of", fake)
    return asked


@pytest.mark.parametrize("req_cls", [DiscoverRequest, RunAllRequest])
async def test_omitted_names_come_from_the_current_list_by_exact_symbol(
    wikipedia: list[int], req_cls: type[DiscoverRequest] | type[RunAllRequest]
) -> None:
    req = req_cls(tickers=["klac", " AAPL "])
    companies = await _common.resolve_companies(req, _CLIENT)
    assert [(c.ticker, c.name) for c in companies] == [
        ("KLAC", "KLA Corporation"),
        ("AAPL", "Apple Inc."),
    ]
    assert len(wikipedia) == 1  # one request for the whole list


async def test_current_tickers_do_not_touch_the_universe_store(
    wikipedia: list[int], universe: list[date]
) -> None:
    await _common.resolve_companies(DiscoverRequest(tickers=["KLAC"]), _CLIENT)
    assert universe == []


async def test_explicit_names_skip_every_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(client: httpx.AsyncClient) -> list[Company]:
        raise AssertionError("no lookup when names are given")

    monkeypatch.setattr(_common, "fetch_sp500_companies", boom)
    req = DiscoverRequest(tickers=["zzz"], company_names=[" Zed Corp "])
    assert await _common.resolve_companies(req, _CLIENT) == [Company(ticker="ZZZ", name="Zed Corp")]


@pytest.mark.parametrize("names", [["A"], ["A", "B", "C"]])
async def test_a_names_list_of_another_length_is_a_422_that_says_positional(
    names: list[str],
) -> None:
    req = DiscoverRequest(tickers=["AAPL", "KLAC"], company_names=names)
    with pytest.raises(HTTPException) as exc:
        await _common.resolve_companies(req, _CLIENT)
    assert exc.value.status_code == 422
    assert "positional" in exc.value.detail


async def test_a_blank_name_is_a_422() -> None:
    req = DiscoverRequest(tickers=["AAPL", "KLAC"], company_names=["Apple", "  "])
    with pytest.raises(HTTPException) as exc:
        await _common.resolve_companies(req, _CLIENT)
    assert exc.value.status_code == 422
    assert "KLAC" in exc.value.detail


async def test_a_leaver_is_found_as_of_the_request_start_date(
    wikipedia: list[int], universe: list[date]
) -> None:
    req = DiscoverRequest(tickers=["XRX", "KLAC"], start_date=date(2023, 1, 1))
    companies = await _common.resolve_companies(req, _CLIENT)
    assert [(c.ticker, c.name) for c in companies] == [
        ("XRX", "Xerox Holdings"),
        ("KLAC", "KLA Corporation"),  # the current list wins for a current ticker
    ]
    assert universe == [date(2023, 1, 1)]


async def test_the_leaver_lookup_defaults_to_the_configured_start_date(
    monkeypatch: pytest.MonkeyPatch, wikipedia: list[int], universe: list[date]
) -> None:
    monkeypatch.setenv("DISCOVERY_START_DATE", "2024-02-03")
    await _common.resolve_companies(DiscoverRequest(tickers=["XRX"]), _CLIENT)
    assert universe == [date(2024, 2, 3)]


async def test_an_unknown_ticker_that_is_a_substring_of_a_name_is_refused(
    wikipedia: list[int], universe: list[date]
) -> None:
    # "KLA" is inside "KLA Corporation" and "XER" inside "Xerox Holdings": exact symbol only.
    for ticker in ("KLA", "XER"):
        with pytest.raises(HTTPException) as exc:
            await _common.resolve_companies(DiscoverRequest(tickers=[ticker]), _CLIENT)
        assert exc.value.status_code == 422
        assert ticker in exc.value.detail
        assert "company_names" in exc.value.detail


async def test_a_start_date_outside_the_coverage_is_a_clear_422(
    monkeypatch: pytest.MonkeyPatch, wikipedia: list[int]
) -> None:
    def raises(as_of: date) -> list[dict]:
        raise ValueError("as_of predates the earliest backfilled date")

    monkeypatch.setattr(_common, "query_as_of", raises)
    with pytest.raises(HTTPException) as exc:
        await _common.resolve_companies(
            DiscoverRequest(tickers=["XRX"], start_date=date(1990, 1, 1)), _CLIENT
        )
    assert exc.value.status_code == 422
    assert "1990-01-01" in exc.value.detail


async def test_a_missing_store_does_not_break_a_current_ticker(
    monkeypatch: pytest.MonkeyPatch, wikipedia: list[int]
) -> None:
    def raises(as_of: date) -> list[dict]:
        raise AssertionError("must not be read for current tickers")

    monkeypatch.setattr(_common, "query_as_of", raises)
    companies = await _common.resolve_companies(DiscoverRequest(tickers=["AAPL"]), _CLIENT)
    assert companies[0].name == "Apple Inc."


async def test_a_missing_store_refuses_only_the_leaver(
    monkeypatch: pytest.MonkeyPatch, wikipedia: list[int]
) -> None:
    def raises(as_of: date) -> list[dict]:
        raise ValueError("no backfill has been run yet")

    monkeypatch.setattr(_common, "query_as_of", raises)
    with pytest.raises(HTTPException) as exc:
        await _common.resolve_companies(DiscoverRequest(tickers=["AAPL", "XRX"]), _CLIENT)
    assert exc.value.status_code == 422
    assert "XRX" in exc.value.detail and "AAPL" not in exc.value.detail


async def test_a_wikipedia_failure_is_a_502(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(client: httpx.AsyncClient) -> list[Company]:
        raise ValueError("markup changed")

    monkeypatch.setattr(_common, "fetch_sp500_companies", fail)
    with pytest.raises(HTTPException) as exc:
        await _common.resolve_companies(DiscoverRequest(tickers=["AAPL"]), _CLIENT)
    assert exc.value.status_code == 502
