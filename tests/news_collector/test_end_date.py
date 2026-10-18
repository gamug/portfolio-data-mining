"""The end date is an input that defaults to today (UTC), not an environment variable (T-077)."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import logging
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import dotenv
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps import news_collector_api
from data_mining import env
from news_collector import config
from news_collector import main as collector_main
from news_collector.api.deps import get_client
from news_collector.api.routers import _common, discovery
from news_collector.models import Company, DateRange, DiscoveredURL, DiscoveryStats
from news_collector.orchestrator import DiscoveryOrchestrator
from news_collector.storage.queue import URLQueue
from news_collector.strategies.ddg import DDGStrategy
from news_collector.strategies.sitemap import SitemapRSSStrategy

FROZEN = date(2026, 10, 18)


class _Now:
    """The UTC instant the code under test sees; a test moves it between two calls."""

    at = datetime(2026, 10, 18, 23, 59, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> type[_Now]:
    _Now.at = datetime(2026, 10, 18, 23, 59, tzinfo=UTC)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return _Now.at

    monkeypatch.setattr(config, "datetime", _Clock)
    return _Now


@pytest.fixture
def today(clock: type[_Now]) -> date:
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


# --- round 1 of the manager review (PR #85) ---------------------------------------------

BEFORE_MIDNIGHT = datetime(2026, 10, 18, 23, 59, tzinfo=UTC)
AFTER_MIDNIGHT = BEFORE_MIDNIGHT + timedelta(minutes=2)  # 00:01 UTC on the 19th


def test_two_days_give_two_dates_from_one_process(clock: type[_Now]) -> None:
    clock.at = BEFORE_MIDNIGHT
    first = config.build_date_range(date(2022, 1, 1), None).end
    clock.at = AFTER_MIDNIGHT
    second = config.build_date_range(date(2022, 1, 1), None).end
    assert (first, second) == (date(2026, 10, 18), date(2026, 10, 19))


def test_one_api_app_takes_the_day_of_each_request(
    clock: type[_Now], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    extra = _patch_run_all(monkeypatch, tmp_path)
    api = _app()
    body = {
        "tickers": ["AAPL"],
        "company_names": ["Apple Inc."],
        "start_date": "2026-01-01",
        **extra,
    }
    clock.at = BEFORE_MIDNIGHT
    first = api.post("/discovery/run-all", json=body).json()["end_date"]
    clock.at = AFTER_MIDNIGHT
    second = api.post("/discovery/run-all", json=body).json()["end_date"]
    assert (first, second) == ("2026-10-18", "2026-10-19")


def test_one_cli_parser_takes_the_day_the_command_runs(
    clock: type[_Now], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_cli(monkeypatch)
    args = collector_main.build_parser().parse_args(
        ["--db", str(tmp_path / "q.db"), "--log-dir", "", "discover", "--tickers", "AAPL"]
    )
    clock.at = BEFORE_MIDNIGHT
    assert asyncio.run(collector_main.cmd_discover(args)) == 0
    clock.at = AFTER_MIDNIGHT
    assert asyncio.run(collector_main.cmd_discover(args)) == 0
    assert [r.end for r in _Recording.ranges] == [date(2026, 10, 18), date(2026, 10, 19)]


def test_only_a_future_start_date_is_a_422_on_run_all(
    today: date, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = {
        "tickers": ["AAPL"],
        "company_names": ["Apple Inc."],
        "start_date": "2099-01-01",
        **_patch_run_all(monkeypatch, tmp_path),
    }
    response = _app().post("/discovery/run-all", json=body)
    assert response.status_code == 422
    assert "2099-01-01" in response.json()["detail"]
    assert _Recording.ranges == []


async def test_only_a_future_start_date_is_a_422_on_a_domain_run(today: date) -> None:
    req = _common.DiscoverRequest(
        tickers=["AAPL"], company_names=["Apple Inc."], start_date=date(2099, 1, 1)
    )
    with pytest.raises(HTTPException) as exc:
        await _common.run_domain_discovery(req, object(), object(), "cnbc.com")  # type: ignore[arg-type]
    assert exc.value.status_code == 422
    assert "2099-01-01" in exc.value.detail


def test_cli_with_only_a_future_start_date_exits_1(
    today: date, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_cli(monkeypatch)
    assert _discover(tmp_path, "--start-date", "2099-01-01") == 1
    assert _Recording.ranges == []


def test_a_bad_start_date_in_the_environment_is_not_an_invalid_range(
    today: date, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The server's configuration is a 500 as before, not a 422 that blames the caller."""
    monkeypatch.setenv("DISCOVERY_START_DATE", "garbage")
    with pytest.raises(ValueError, match="garbage") as exc:
        config.build_date_range(None, None)
    assert not isinstance(exc.value, config.InvalidRangeError)

    body = {
        "tickers": ["AAPL"],
        "company_names": ["Apple Inc."],
        **_patch_run_all(monkeypatch, tmp_path),
    }
    app = FastAPI()
    app.include_router(discovery.router)
    app.dependency_overrides[get_client] = lambda: None
    response = TestClient(app, raise_server_exceptions=False).post("/discovery/run-all", json=body)
    assert response.status_code == 500


async def test_a_bad_start_date_in_the_environment_is_not_a_422_on_a_domain_run(
    today: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISCOVERY_START_DATE", "garbage")
    req = _common.DiscoverRequest(tickers=["AAPL"], company_names=["Apple Inc."])
    with pytest.raises(ValueError, match="garbage"):
        await _common.run_domain_discovery(req, object(), object(), "cnbc.com")  # type: ignore[arg-type]


# --- resume and duplicates (decision 2) -------------------------------------------------


@pytest.fixture
def one_url_orchestrator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> DiscoveryOrchestrator:
    """An orchestrator whose one pair always finds the same URL: only the queue is real."""

    async def ddg(self: object, company: object, ticker: str, date_range: DateRange) -> list:
        return [
            DiscoveredURL(
                url="https://cnbc.com/a",
                domain="cnbc.com",
                company="Apple",
                ticker="AAPL",
                source="ddg",
                discovered_at=datetime(2026, 10, 18),
                match_class="accept",
                match_reason="name",
            )
        ]

    async def sitemap(self: object, company: object, ticker: str, date_range: DateRange) -> list:
        return []

    monkeypatch.setattr(DDGStrategy, "discover", ddg)
    monkeypatch.setattr(SitemapRSSStrategy, "discover", sitemap)

    class _Conn:
        _client = None

    queue = URLQueue(tmp_path / "resume.db")
    return DiscoveryOrchestrator(connectors={"cnbc.com": _Conn()}, queue=queue, concurrency=1)  # type: ignore[arg-type]


APPLE = Company(ticker="AAPL", name="Apple")


async def test_a_second_run_over_the_same_range_with_resume_off_inserts_nothing(
    one_url_orchestrator: DiscoveryOrchestrator,
) -> None:
    rng = DateRange(start=date(2026, 1, 1), end=date(2026, 10, 18))
    first = await one_url_orchestrator.run([APPLE], ["cnbc.com"], rng, resume=False)
    second = await one_url_orchestrator.run([APPLE], ["cnbc.com"], rng, resume=False)
    assert (first.total_inserted, second.total_inserted) == (1, 0)
    assert second.skipped_pairs == 0  # it did run again, and UNIQUE(url, ticker) kept one row


async def test_a_new_end_date_with_resume_on_runs_the_pair_again_without_duplicates(
    one_url_orchestrator: DiscoveryOrchestrator,
) -> None:
    start = date(2026, 1, 1)
    first = await one_url_orchestrator.run(
        [APPLE], ["cnbc.com"], DateRange(start=start, end=date(2026, 10, 18)), resume=True
    )
    same_range = await one_url_orchestrator.run(
        [APPLE], ["cnbc.com"], DateRange(start=start, end=date(2026, 10, 18)), resume=True
    )
    next_day = await one_url_orchestrator.run(
        [APPLE], ["cnbc.com"], DateRange(start=start, end=date(2026, 10, 19)), resume=True
    )
    assert first.skipped_pairs == 0
    assert same_range.skipped_pairs == 1  # same exact range: skipped
    assert (next_day.skipped_pairs, next_day.total_discovered) == (0, 1)  # new end: ran again
    assert next_day.total_inserted == 0  # the cost is time and network, not rows


# --- config reads no .env; a retired name is never applied ------------------------------


def test_config_does_not_import_dotenv() -> None:
    tree = ast.parse(Path(config.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for alias in n.names
    } | {
        n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert "dotenv" not in imported


def test_importing_config_reads_no_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: calls.append((a, k)) or True)
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *a, **k: calls.append((a, k)) or {})
    # A second copy under another name: reloading `config` itself would re-run its body in the
    # real module and replace `InvalidRangeError`, which the call sites hold by name.
    spec = importlib.util.spec_from_file_location("config_probe", config.__file__)
    assert spec is not None
    assert spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    assert probe.InvalidRangeError is not config.InvalidRangeError  # it is a separate copy
    assert calls == []


def test_a_retired_name_is_not_applied_over_the_system_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DISCOVERY_END_DATE", "2025-01-01")  # the system value
    path = tmp_path / ".env"
    path.write_text("DISCOVERY_END_DATE=2026-08-10\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="data_mining.env"):
        env.load_env(path)
    assert os.environ["DISCOVERY_END_DATE"] == "2025-01-01"  # the file's value did not win
    [line] = [r.getMessage() for r in caplog.records]
    assert "DISCOVERY_END_DATE" in line
    assert "2025-01-01" not in line
    assert "2026-08-10" not in line


# --- the collector API's own trace ------------------------------------------------------


async def test_the_collector_api_puts_the_retired_notice_in_its_file_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The lifespan calls `log_again()` once the file handler exists (T-066b, T-077)."""
    trace = tmp_path / "api.log"
    startup_log = logging.getLogger("data_mining.startup")
    handler = logging.FileHandler(trace)
    previous_level = startup_log.level
    startup_log.addHandler(handler)
    startup_log.setLevel(logging.INFO)

    monkeypatch.setenv("DISCOVERY_END_DATE", "2026-08-10")
    env.load_env(tmp_path / "missing.env")  # what the app module did when it was imported
    monkeypatch.setattr(news_collector_api, "check_startup", lambda **_: None)
    monkeypatch.setattr(news_collector_api, "setup_logging", lambda **_: trace)
    monkeypatch.setattr(news_collector_api, "build_client", _FakeClient)
    try:
        async with news_collector_api.lifespan(FastAPI()):
            pass
    finally:
        startup_log.removeHandler(handler)
        startup_log.setLevel(previous_level)
        handler.close()
    text = trace.read_text(encoding="utf-8")
    assert text.count("retired variable: DISCOVERY_END_DATE") == 1
    assert "2026-08-10" not in text
