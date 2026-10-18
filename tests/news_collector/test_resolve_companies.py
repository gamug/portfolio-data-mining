"""T-067: a ticker's company name is never defaulted to the ticker."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from news_collector import companies as companies_module
from news_collector.api.deps import get_client
from news_collector.api.routers import _common, cnbc, discovery
from news_collector.api.schemas import DiscoverRequest, RunAllRequest
from news_collector.models import Company

_CLIENT = cast(httpx.AsyncClient, object())  # never used: every lookup is faked

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

    monkeypatch.setattr(companies_module, "fetch_sp500_companies", fake)
    return calls


@pytest.fixture
def store_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A file where $UNIVERSE_DB_PATH points, so the existence check passes."""
    path = tmp_path / "universe.db"
    path.write_bytes(b"")
    monkeypatch.setenv("UNIVERSE_DB_PATH", str(path))
    return path


@pytest.fixture
def universe(monkeypatch: pytest.MonkeyPatch, store_file: Path) -> list[date]:
    asked: list[date] = []

    def fake(as_of: date) -> list[dict]:
        asked.append(as_of)
        return [
            {"Symbol": "XRX", "Security": "Xerox Holdings"},
            {"Symbol": "KLAC", "Security": "x"},
        ]

    monkeypatch.setattr(companies_module, "query_as_of", fake)
    return asked


def _store_raises(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def raises(as_of: date) -> list[dict]:
        raise exc

    monkeypatch.setattr(companies_module, "query_as_of", raises)


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


async def test_explicit_names_skip_every_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(client: httpx.AsyncClient) -> list[Company]:
        raise AssertionError("no lookup when names are given")

    monkeypatch.setattr(companies_module, "fetch_sp500_companies", boom)
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


async def test_a_yahoo_style_symbol_is_not_normalized(
    monkeypatch: pytest.MonkeyPatch, store_file: Path
) -> None:
    async def fake(client: httpx.AsyncClient) -> list[Company]:
        return [Company(ticker="BRK.B", name="Berkshire Hathaway")]

    monkeypatch.setattr(companies_module, "fetch_sp500_companies", fake)
    _store_raises(monkeypatch, ValueError("no data"))
    with pytest.raises(HTTPException) as exc:
        await _common.resolve_companies(DiscoverRequest(tickers=["BRK-B"]), _CLIENT)
    assert exc.value.status_code == 422
    assert "BRK-B" in exc.value.detail


async def test_a_start_date_outside_the_coverage_is_a_clear_422(
    monkeypatch: pytest.MonkeyPatch, wikipedia: list[int], store_file: Path
) -> None:
    _store_raises(monkeypatch, ValueError("as_of predates the earliest backfilled date"))
    with pytest.raises(HTTPException) as exc:
        await _common.resolve_companies(
            DiscoverRequest(tickers=["XRX"], start_date=date(1990, 1, 1)), _CLIENT
        )
    assert exc.value.status_code == 422
    assert "1990-01-01" in exc.value.detail
    assert "predates" in exc.value.detail


async def test_a_missing_store_does_not_break_a_current_ticker(
    monkeypatch: pytest.MonkeyPatch, wikipedia: list[int], tmp_path: Path
) -> None:
    monkeypatch.setenv("UNIVERSE_DB_PATH", str(tmp_path / "nope.db"))
    companies = await _common.resolve_companies(DiscoverRequest(tickers=["AAPL"]), _CLIENT)
    assert companies[0].name == "Apple Inc."


async def test_a_missing_store_file_is_reported_and_not_created(
    monkeypatch: pytest.MonkeyPatch, wikipedia: list[int], tmp_path: Path
) -> None:
    missing = tmp_path / "nope.db"
    monkeypatch.setenv("UNIVERSE_DB_PATH", str(missing))
    with pytest.raises(HTTPException) as exc:
        await _common.resolve_companies(DiscoverRequest(tickers=["AAPL", "XRX"]), _CLIENT)
    assert exc.value.status_code == 422
    assert "XRX" in exc.value.detail and "AAPL" not in exc.value.detail
    assert "does not exist" in exc.value.detail
    assert not missing.exists()  # a read does not write


@pytest.mark.parametrize(
    "error", [PermissionError(13, "Permission denied"), sqlite3.DatabaseError("not a database")]
)
async def test_a_store_that_cannot_be_opened_is_a_422_not_a_500(
    monkeypatch: pytest.MonkeyPatch, wikipedia: list[int], store_file: Path, error: Exception
) -> None:
    _store_raises(monkeypatch, error)
    with pytest.raises(HTTPException) as exc:
        await _common.resolve_companies(DiscoverRequest(tickers=["XRX"]), _CLIENT)
    assert exc.value.status_code == 422
    assert "XRX" in exc.value.detail


async def test_a_real_unreadable_file_is_a_422(
    monkeypatch: pytest.MonkeyPatch, wikipedia: list[int], store_file: Path
) -> None:
    store_file.write_bytes(b"this is not a sqlite file" * 100)
    with pytest.raises(HTTPException) as exc:
        await _common.resolve_companies(DiscoverRequest(tickers=["XRX"]), _CLIENT)
    assert exc.value.status_code == 422


async def test_a_wikipedia_failure_is_a_502(
    monkeypatch: pytest.MonkeyPatch, store_file: Path
) -> None:
    async def fail(client: httpx.AsyncClient) -> list[Company]:
        raise ValueError("markup changed")

    monkeypatch.setattr(companies_module, "fetch_sp500_companies", fail)
    _store_raises(monkeypatch, ValueError("no data"))
    with pytest.raises(HTTPException) as exc:
        await _common.resolve_companies(DiscoverRequest(tickers=["AAPL"]), _CLIENT)
    assert exc.value.status_code == 502
    assert "markup changed" in exc.value.detail


async def test_when_wikipedia_is_down_the_store_still_names_what_it_knows(
    monkeypatch: pytest.MonkeyPatch, universe: list[date]
) -> None:
    async def fail(client: httpx.AsyncClient) -> list[Company]:
        raise ValueError("markup changed")

    monkeypatch.setattr(companies_module, "fetch_sp500_companies", fail)
    companies = await _common.resolve_companies(DiscoverRequest(tickers=["XRX"]), _CLIENT)
    assert [(c.ticker, c.name) for c in companies] == [("XRX", "Xerox Holdings")]


async def test_when_wikipedia_is_down_a_ticker_the_store_lacks_is_a_502_naming_both(
    monkeypatch: pytest.MonkeyPatch, universe: list[date]
) -> None:
    async def fail(client: httpx.AsyncClient) -> list[Company]:
        raise ValueError("markup changed")

    monkeypatch.setattr(companies_module, "fetch_sp500_companies", fail)
    with pytest.raises(HTTPException) as exc:
        await _common.resolve_companies(DiscoverRequest(tickers=["XRX", "AAPL"]), _CLIENT)
    assert exc.value.status_code == 502
    assert "markup changed" in exc.value.detail
    assert "AAPL" in exc.value.detail and "XRX" not in exc.value.detail.split("No company")[1]


# --- through the routes: the status reaches the caller -------------------------------------


def _app() -> TestClient:
    app = FastAPI()
    app.include_router(discovery.router)
    app.include_router(cnbc.router)
    app.dependency_overrides[get_client] = lambda: None
    return TestClient(app)


def test_run_all_returns_422_for_a_mismatched_names_list() -> None:
    body = {"tickers": ["AAPL", "KLAC"], "company_names": ["Apple"]}
    res = _app().post("/discovery/run-all", json=body)
    assert res.status_code == 422
    assert "positional" in res.json()["detail"]


def test_a_per_domain_route_returns_422_for_an_unknown_ticker(
    monkeypatch: pytest.MonkeyPatch, wikipedia: list[int], tmp_path: Path
) -> None:
    monkeypatch.setenv("UNIVERSE_DB_PATH", str(tmp_path / "nope.db"))
    res = _app().post("/discover/cnbc", json={"tickers": ["ZZZ"]})
    assert res.status_code == 422
    assert "ZZZ" in res.json()["detail"]
    assert "company_names" in res.json()["detail"]


async def test_an_empty_names_list_counts_as_omitted(wikipedia: list[int]) -> None:
    req = DiscoverRequest(tickers=["AAPL"], company_names=[])
    assert (await _common.resolve_companies(req, _CLIENT))[0].name == "Apple Inc."


async def test_a_renamed_current_ticker_gets_the_store_name_when_wikipedia_is_down(
    monkeypatch: pytest.MonkeyPatch, store_file: Path
) -> None:
    async def fail(client: httpx.AsyncClient) -> list[Company]:
        raise ValueError("markup changed")

    def renamed(as_of: date) -> list[dict]:
        return [{"Symbol": "KLAC", "Security": "KLA-Tencor"}]

    monkeypatch.setattr(companies_module, "fetch_sp500_companies", fail)
    monkeypatch.setattr(companies_module, "query_as_of", renamed)
    companies = await _common.resolve_companies(DiscoverRequest(tickers=["KLAC"]), _CLIENT)
    assert companies[0].name == "KLA-Tencor"  # the name as of start_date, as documented
