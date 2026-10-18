"""A request or command without a database path follows `$DATABASE_URL` (T-065)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from data_mining.db import DEFAULT_DB_PATH, resolve_db_path
from news_collector import main as collector_main
from news_collector.api.routers import discovery
from news_collector.models import DiscoveredURL
from news_collector.storage.queue import URLQueue


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


@pytest.mark.parametrize("bad", [None, ""])
def test_urlqueue_refuses_no_path(bad: str | None, tmp_path: Path) -> None:
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
