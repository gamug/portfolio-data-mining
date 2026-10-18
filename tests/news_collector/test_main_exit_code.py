"""`news_collector.main.cmd_discover` reports a run that recorded errors to the shell (T-064)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

import pytest

from news_collector import main as collector_main
from news_collector.companies import CompanyLookupError
from news_collector.models import Company, DiscoveryStats


class _FakeOrchestrator:
    errors: ClassVar[list[str]] = []

    def __init__(self, **_: object) -> None: ...

    async def run(self, **_: object) -> DiscoveryStats:
        stats = DiscoveryStats()
        stats.errors = list(self.errors)
        return stats


class _FakeClient:
    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


async def _fake_lookup(tickers: list[str], client: object, start: object) -> list[Company]:
    return [Company(ticker=t, name=f"{t} Inc.") for t in tickers]


@pytest.mark.parametrize(
    ("errors", "code"), [([], 0), (["AAPL/cnbc.com: too many SQL variables"], 1)]
)
def test_cmd_discover_returns_nonzero_when_the_run_recorded_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, errors: list[str], code: int
) -> None:
    _FakeOrchestrator.errors = errors
    monkeypatch.setattr(collector_main, "DiscoveryOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(collector_main, "build_client", _FakeClient)
    monkeypatch.setattr(collector_main, "build_connectors", lambda client: [])
    monkeypatch.setattr(collector_main, "lookup_companies", _fake_lookup)
    args = collector_main.build_parser().parse_args(
        ["--db", str(tmp_path / "q.db"), "discover", "--tickers", "AAPL"]
    )

    assert asyncio.run(collector_main.cmd_discover(args)) == code


class _CapturingOrchestrator(_FakeOrchestrator):
    companies: ClassVar[list[Company]] = []

    async def run(self, **kwargs: object) -> DiscoveryStats:
        type(self).companies = list(kwargs["companies"])  # type: ignore[call-overload]
        return DiscoveryStats()


def _patch_cli(monkeypatch: pytest.MonkeyPatch, lookup: object) -> None:
    monkeypatch.setattr(collector_main, "DiscoveryOrchestrator", _CapturingOrchestrator)
    monkeypatch.setattr(collector_main, "build_client", _FakeClient)
    monkeypatch.setattr(collector_main, "build_connectors", lambda client: [])
    monkeypatch.setattr(collector_main, "lookup_companies", lookup)


def test_cli_tickers_get_their_company_names_not_the_ticker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, object] = {}

    async def lookup(tickers: list[str], client: object, start: object) -> list[Company]:
        seen["start"] = start
        return [Company(ticker="KLAC", name="KLA Corporation")]

    _patch_cli(monkeypatch, lookup)
    args = collector_main.build_parser().parse_args(
        [
            "--db",
            str(tmp_path / "q.db"),
            "discover",
            "--tickers",
            "klac",
            "--start-date",
            "2023-05-06",
        ]
    )

    assert asyncio.run(collector_main.cmd_discover(args)) == 0
    assert [(c.ticker, c.name) for c in _CapturingOrchestrator.companies] == [
        ("KLAC", "KLA Corporation")
    ]
    assert str(seen["start"]) == "2023-05-06"


def test_cli_stops_with_status_1_when_a_ticker_has_no_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def lookup(tickers: list[str], client: object, start: object) -> list[Company]:
        raise CompanyLookupError("No company name for: ZZZ", 422)

    _patch_cli(monkeypatch, lookup)
    _CapturingOrchestrator.companies = []
    args = collector_main.build_parser().parse_args(
        ["--db", str(tmp_path / "q.db"), "discover", "--tickers", "ZZZ"]
    )

    assert asyncio.run(collector_main.cmd_discover(args)) == 1
    assert _CapturingOrchestrator.companies == []  # nothing was discovered under a made-up name
