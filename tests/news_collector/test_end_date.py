"""The end date is an input that defaults to today (UTC), not an environment variable (T-077)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from data_mining import env
from news_collector import config
from news_collector import main as collector_main
from news_collector.api.deps import get_client
from news_collector.api.routers import _common, discovery
from news_collector.models import Company, DateRange, DiscoveryStats

FROZEN = date(2026, 10, 18)


@pytest.fixture
def today(monkeypatch: pytest.MonkeyPatch) -> date:
    class _Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return datetime(2026, 10, 18, 23, 59, tzinfo=UTC)

    monkeypatch.setattr(config, "datetime", _Clock)
    return FROZEN


# --- build_date_range -------------------------------------------------------------------


def test_the_default_end_is_utc_today_and_ignores_discovery_end_date(
    today: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISCOVERY_END_DATE", "2026-08-10")
    assert config.today_utc() == FROZEN
    assert config.build_date_range(date(2022, 1, 1), None).end == FROZEN


def test_the_default_start_still_follows_its_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCOVERY_START_DATE", "2024-02-03")
    assert config.build_date_range(None, date(2025, 1, 1)) == DateRange(
        start=date(2024, 2, 3), end=date(2025, 1, 1)
    )


def test_start_after_end_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="must be <="):
        config.build_date_range(date(2026, 2, 1), date(2026, 1, 1))


def test_start_equal_to_end_is_one_day() -> None:
    d = date(2026, 1, 1)
    assert config.build_date_range(d, d) == DateRange(start=d, end=d)


def test_a_future_end_is_allowed_without_a_clamp(today: date) -> None:
    future = date(2030, 1, 1)
    assert config.build_date_range(date(2022, 1, 1), future).end == future


# --- API --------------------------------------------------------------------------------


def _app() -> TestClient:
    app = FastAPI()
    app.include_router(discovery.router)
    app.dependency_overrides[get_client] = lambda: None
    return TestClient(app)


class _Recording:
    ranges: ClassVar[list[DateRange]] = []

    def __init__(self, **_: object) -> None: ...

    async def run(self, **kwargs: object) -> DiscoveryStats:
        type(self).ranges.append(kwargs["date_range"])  # type: ignore[arg-type]
        return DiscoveryStats()


def _patch_run_all(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    _Recording.ranges = []
    monkeypatch.setattr(discovery, "DiscoveryOrchestrator", _Recording)
    monkeypatch.setattr(discovery, "build_connectors", lambda _client: {})
    return {"db_path": str(tmp_path / "q.db")}


def test_run_all_defaults_the_end_to_today_and_echoes_the_range(
    today: date, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISCOVERY_END_DATE", "2026-08-10")
    body = {
        "tickers": ["AAPL"],
        "company_names": ["Apple Inc."],
        "start_date": "2026-01-01",
        **_patch_run_all(monkeypatch, tmp_path),
    }
    response = _app().post("/discovery/run-all", json=body)
    assert response.status_code == 200
    assert response.json()["start_date"] == "2026-01-01"
    assert response.json()["end_date"] == "2026-10-18"
    assert _Recording.ranges == [DateRange(start=date(2026, 1, 1), end=FROZEN)]


def test_run_all_takes_the_end_date_it_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = {
        "tickers": ["AAPL"],
        "company_names": ["Apple Inc."],
        "start_date": "2026-01-01",
        "end_date": "2026-03-31",
        **_patch_run_all(monkeypatch, tmp_path),
    }
    response = _app().post("/discovery/run-all", json=body)
    assert response.json()["end_date"] == "2026-03-31"


def test_run_all_with_start_after_end_is_a_422_that_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = {
        "tickers": ["AAPL"],
        "company_names": ["Apple Inc."],
        "start_date": "2026-05-01",
        "end_date": "2026-03-31",
        **_patch_run_all(monkeypatch, tmp_path),
    }
    response = _app().post("/discovery/run-all", json=body)
    assert response.status_code == 422
    assert "2026-05-01" in response.json()["detail"]
    assert _Recording.ranges == []


async def _nothing(*_: object, **__: object) -> tuple[list, list, list, list]:
    return [], [], [], []


async def test_a_domain_run_echoes_the_range_it_used(
    today: date, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_common, "_discover_one_ticker", _nothing)
    req = _common.DiscoverRequest(
        tickers=["AAPL"],
        company_names=["Apple Inc."],
        start_date=date(2026, 1, 1),
        db_path=str(tmp_path / "q.db"),
    )
    [out] = await _common.run_domain_discovery(req, object(), object(), "cnbc.com")  # type: ignore[arg-type]
    assert (out.start_date, out.end_date) == (date(2026, 1, 1), FROZEN)


async def test_a_failed_ticker_still_echoes_the_range(
    today: date, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def boom(*_: object, **__: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(_common, "_discover_one_ticker", boom)
    req = _common.DiscoverRequest(
        tickers=["AAPL"],
        company_names=["Apple Inc."],
        start_date=date(2026, 1, 1),
        db_path=str(tmp_path / "q.db"),
    )
    [out] = await _common.run_domain_discovery(req, object(), object(), "cnbc.com")  # type: ignore[arg-type]
    assert out.errors == ["boom"]
    assert (out.start_date, out.end_date) == (date(2026, 1, 1), FROZEN)


async def test_a_domain_run_with_start_after_end_is_a_422() -> None:
    req = _common.DiscoverRequest(
        tickers=["AAPL"],
        company_names=["Apple Inc."],
        start_date=date(2026, 5, 1),
        end_date=date(2026, 3, 31),
    )
    with pytest.raises(HTTPException) as exc:
        await _common.run_domain_discovery(req, object(), object(), "cnbc.com")  # type: ignore[arg-type]
    assert exc.value.status_code == 422
    assert "2026-05-01" in exc.value.detail


# --- CLI --------------------------------------------------------------------------------


class _FakeClient:
    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def _patch_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    _Recording.ranges = []

    async def lookup(tickers: list[str], client: object, start: object) -> list[Company]:
        return [Company(ticker=t, name=f"{t} Inc.") for t in tickers]

    monkeypatch.setattr(collector_main, "DiscoveryOrchestrator", _Recording)
    monkeypatch.setattr(collector_main, "build_client", _FakeClient)
    monkeypatch.setattr(collector_main, "build_connectors", lambda client: [])
    monkeypatch.setattr(collector_main, "lookup_companies", lookup)


def _discover(tmp_path: Path, *extra: str) -> int:
    args = collector_main.build_parser().parse_args(
        ["--db", str(tmp_path / "q.db"), "--log-dir", "", "discover", "--tickers", "AAPL", *extra]
    )
    return asyncio.run(collector_main.cmd_discover(args))


def test_cli_end_date_defaults_to_today_at_the_start_of_the_command(
    today: date, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DISCOVERY_END_DATE", "2026-08-10")
    _patch_cli(monkeypatch)
    parser = collector_main.build_parser()  # built before the day is known to the command
    args = parser.parse_args(["discover", "--start-date", "2026-01-01"])
    assert args.end_date is None
    assert _discover(tmp_path, "--start-date", "2026-01-01") == 0
    assert _Recording.ranges == [DateRange(start=date(2026, 1, 1), end=FROZEN)]
    assert "Date range       : 2026-01-01 to 2026-10-18" in capsys.readouterr().out


def test_cli_takes_the_end_date_it_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_cli(monkeypatch)
    assert _discover(tmp_path, "--start-date", "2026-01-01", "--end-date", "2026-03-31") == 0
    assert _Recording.ranges[0].end == date(2026, 3, 31)
    assert "2026-01-01 to 2026-03-31" in capsys.readouterr().out


def test_cli_start_after_end_exits_1_with_one_line_and_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_cli(monkeypatch)
    code = _discover(tmp_path, "--start-date", "2026-05-01", "--end-date", "2026-03-31")
    assert code == 1
    assert _Recording.ranges == []
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.strip()]
    assert len(lines) == 1  # the message, no stack
    assert "start 2026-05-01 must be <= end 2026-03-31" in lines[0]


@pytest.mark.parametrize("flag", ["--end-date", "--start-date"])
def test_cli_malformed_date_is_a_usage_error(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        collector_main.build_parser().parse_args(["discover", flag, "10/18/2026"])
    assert exc.value.code == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_cli_discover_replays_the_retired_notice_into_its_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DISCOVERY_END_DATE", "2026-08-10")
    env.load_env(tmp_path / "missing.env")  # what the entry point did at import
    _patch_cli(monkeypatch)
    args = collector_main.build_parser().parse_args(
        ["--db", str(tmp_path / "q.db"), "--log-dir", "", "discover", "--tickers", "AAPL"]
    )
    assert asyncio.run(collector_main.cmd_discover(args)) == 0
    err = capsys.readouterr().err
    assert err.count("retired variable: DISCOVERY_END_DATE") == 1
    assert "2026-08-10" not in err
