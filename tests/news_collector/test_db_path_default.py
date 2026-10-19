"""A request or command without a database path follows `$DATABASE_URL` (T-065)."""

from __future__ import annotations

import importlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from data_mining.db import DEFAULT_DB_PATH, resolve_db_path
from news_collector import main as collector_main
from news_collector.api import deps
from news_collector.api.deps import get_client
from news_collector.api.routers import _common, discovery
from news_collector.api.schemas import DiscoverRequest
from news_collector.models import DiscoveredURL, DiscoveryStats
from news_collector.storage.queue import URLQueue

_NO_ROOTS = SimpleNamespace(
    config=SimpleNamespace(sitemap_roots=[])
)  # a connector with no sitemap (T-070a: no cache)


@pytest.fixture
def real_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A database with 2 rows, named by DATABASE_URL, run from a directory with no data/."""
    path = tmp_path / "real.db"
    queue = URLQueue(path)
    queue.initialize()
    queue.enqueue_batch(
        [
            DiscoveredURL(
                url=f"https://cnbc.com/a-{i}",
                domain="cnbc.com",
                company="Apple Inc.",
                ticker="AAPL",
                source="sitemap",
                discovered_at=datetime(2026, 8, 10),
                match_class="accept",
                match_reason="name",
            )
            for i in range(2)
        ]
    )
    queue.close()
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("DATABASE_URL", str(path))
    return path


@pytest.mark.parametrize(
    ("value", "env", "expected"),
    [
        ("/x.db", "/env.db", "/x.db"),
        (None, "/env.db", "/env.db"),
        ("", "/env.db", "/env.db"),
        (None, "", DEFAULT_DB_PATH),  # `.env.example` ships `DATABASE_URL=`
        (None, "  ", DEFAULT_DB_PATH),
        ("  ", "/env.db", "/env.db"),
        (None, None, DEFAULT_DB_PATH),
    ],
)
def test_resolve_db_path(
    monkeypatch: pytest.MonkeyPatch, value: str | None, env: str | None, expected: str
) -> None:
    if env is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", env)
    assert resolve_db_path(value) == expected


@pytest.mark.parametrize("bad", [None, "", "  ", Path("")])
def test_urlqueue_refuses_no_path(bad: str | Path | None, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="database path"):
        URLQueue(bad)  # type: ignore[arg-type]
    assert not (tmp_path / "None").exists()


def test_stats_without_db_path_reports_the_real_counts(real_db: Path) -> None:
    app = FastAPI()
    app.include_router(discovery.router)
    client = TestClient(app)

    assert client.get("/discovery/stats").json()["total"] == 2
    assert client.get("/discovery/query").json()["total"] == 2
    assert client.get("/discovery/pending").status_code == 200
    # an empty value is unset, not a path
    assert client.get("/discovery/stats", params={"db_path": ""}).json()["total"] == 2
    # nothing was created in the working directory
    assert list(Path.cwd().iterdir()) == []


def test_cli_stats_without_db_follows_database_url(
    real_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = collector_main.build_parser().parse_args(["stats"])
    assert args.db is None
    collector_main.cmd_stats(args)
    assert json.loads(capsys.readouterr().out)["total"] == 2
    assert list(Path.cwd().iterdir()) == []


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the path every `URLQueue` is built with, in each module that builds one."""
    paths: list[str] = []

    class _Recording(URLQueue):
        def __init__(self, db_path: str | Path) -> None:
            super().__init__(db_path)
            paths.append(self._db_path)

    for mod in (discovery, _common, deps, collector_main):
        monkeypatch.setattr(mod, "URLQueue", _Recording)
    return paths


def test_run_all_without_db_path_opens_database_url(
    real_db: Path, opened: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeOrchestrator:
        def __init__(self, **_: object) -> None: ...

        async def run(self, **_: object) -> DiscoveryStats:
            return DiscoveryStats()

    monkeypatch.setattr(discovery, "DiscoveryOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(discovery, "build_connectors", lambda _client: {})
    app = FastAPI()
    app.include_router(discovery.router)
    app.dependency_overrides[get_client] = lambda: None
    body = {"tickers": ["AAPL"], "company_names": ["Apple Inc."]}
    assert TestClient(app).post("/discovery/run-all", json=body).status_code == 200
    assert opened == [str(real_db)]
    assert list(Path.cwd().iterdir()) == []


async def test_run_domain_discovery_without_db_path_opens_database_url(
    real_db: Path, opened: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def nothing(*_: object, **__: object) -> tuple[list, list, list, list]:
        return [], [], [], []

    monkeypatch.setattr(_common, "_discover_one_ticker", nothing)
    req = DiscoverRequest(tickers=["AAPL"], company_names=["Apple Inc."])
    await _common.run_domain_discovery(req, _NO_ROOTS, object(), "cnbc.com")  # type: ignore[arg-type]
    assert opened == [str(real_db)]
    assert list(Path.cwd().iterdir()) == []


def test_get_queue_without_a_path_opens_database_url(real_db: Path, opened: list[str]) -> None:
    deps.get_queue().close()
    deps.get_queue("").close()
    assert opened == [str(real_db), str(real_db)]


def test_cli_export_without_db_follows_database_url(
    real_db: Path, opened: list[str], tmp_path: Path
) -> None:
    out = tmp_path / "pending.csv"
    args = collector_main.build_parser().parse_args(["export", "--output", str(out)])
    collector_main.cmd_export(args)
    assert opened == [str(real_db)]
    assert out.exists()


@pytest.mark.parametrize("module", ["apps.news_crawler_api", "cli.news_crawler_cli"])
@pytest.mark.parametrize(
    ("env", "expected"), [("", DEFAULT_DB_PATH), ("/some/real.db", "/some/real.db")]
)
def test_crawler_default_db_follows_the_resolver(
    monkeypatch: pytest.MonkeyPatch, module: str, env: str, expected: str
) -> None:
    mod = importlib.import_module(module)
    try:
        with monkeypatch.context() as m:
            m.setenv("DATABASE_URL", env)
            importlib.reload(mod)
            assert expected == mod.DEFAULT_DB
    finally:
        importlib.reload(mod)
