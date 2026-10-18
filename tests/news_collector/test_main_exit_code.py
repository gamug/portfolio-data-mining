"""`news_collector.main.cmd_discover` reports a run that recorded errors to the shell (T-064)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

import pytest

from news_collector import main as collector_main
from news_collector.models import DiscoveryStats


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
    args = collector_main.build_parser().parse_args(
        ["--db", str(tmp_path / "q.db"), "discover", "--tickers", "AAPL"]
    )

    assert asyncio.run(collector_main.cmd_discover(args)) == code
