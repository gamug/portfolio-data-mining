"""The matching class on the two APIs that read `discovered_urls` (T-056b)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from portfolio_common.db import Database

from apps import news_crawler_api
from data_mining.db import connect
from data_mining.schema import apply_schema, run_migrations
from news_collector.api.routers import discovery
from news_collector.models import DiscoveredURL
from news_collector.storage.queue import URLQueue


def _row(url: str, match_class: str, reason: str | None) -> DiscoveredURL:
    return DiscoveredURL(
        url=url,
        domain="cnbc.com",
        company="Apple Inc.",
        ticker="AAPL",
        source="sitemap",
        discovered_at=datetime(2026, 8, 10),
        match_class=match_class,  # type: ignore[arg-type]
        match_reason=reason,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "urls.db")
    queue = URLQueue(path)
    queue.initialize()
    queue.enqueue_batch(
        [
            _row("https://cnbc.com/acc", "accept", "name"),
            _row("https://cnbc.com/rev", "review", "weak_ticker"),
            _row("https://cnbc.com/rej", "reject", "none"),
        ]
    )
    queue.close()
    return path


def test_collector_query_exposes_class_and_filters_on_it(db_path: str) -> None:
    app = FastAPI()
    app.include_router(discovery.router)
    client = TestClient(app)

    everything = client.get("/discovery/query", params={"db_path": db_path}).json()
    classes = {r["url"]: (r["match_class"], r["match_reason"]) for r in everything["results"]}
    assert classes["https://cnbc.com/rej"] == ("reject", "none")
    assert classes["https://cnbc.com/acc"] == ("accept", "name")

    only = client.get("/discovery/query", params={"db_path": db_path, "match_class": "reject"})
    assert [r["url"] for r in only.json()["results"]] == ["https://cnbc.com/rej"]
    assert (
        client.get(
            "/discovery/query", params={"db_path": db_path, "match_class": "bogus"}
        ).status_code
        == 422
    )


def test_collector_pending_leaves_out_rejected_rows(db_path: str) -> None:
    app = FastAPI()
    app.include_router(discovery.router)
    got = TestClient(app).get("/discovery/pending", params={"db_path": db_path}).json()
    assert {r["url"] for r in got} == {"https://cnbc.com/acc", "https://cnbc.com/rev"}


def test_collector_stats_split_skipped_by_match_class(db_path: str) -> None:
    app = FastAPI()
    app.include_router(discovery.router)
    got = TestClient(app).get("/discovery/stats", params={"db_path": db_path}).json()
    assert got["by_status"] == {"pending": 2, "skipped": 1}
    assert got["by_match_class"] == {"accept": 1, "review": 1, "reject": 1}


def test_status_filter_description_says_skipped_includes_rejected() -> None:
    app = FastAPI()
    app.include_router(discovery.router)
    params = app.openapi()["paths"]["/discovery/query"]["get"]["parameters"]
    status = next(p for p in params if p["name"] == "status")
    assert "matcher-rejected" in status["description"]


@pytest.fixture
def crawler(db_path: str) -> Iterator[TestClient]:
    def _conn() -> Iterator[Database]:
        conn = connect(db_path, check_same_thread=False)
        apply_schema(conn)
        run_migrations(conn)
        try:
            yield conn
        finally:
            conn.close()

    news_crawler_api.app.dependency_overrides[news_crawler_api.get_conn] = _conn
    yield TestClient(news_crawler_api.app)
    news_crawler_api.app.dependency_overrides.clear()


def test_crawler_discovered_exposes_class_and_filters_on_it(crawler: TestClient) -> None:
    rows = crawler.get("/discovered", params={"limit": 10}).json()
    assert {r["match_class"] for r in rows} == {"accept", "review", "reject"}

    rejected = crawler.get("/discovered", params={"match_class": "reject"}).json()
    assert [(r["url"], r["status"], r["match_reason"]) for r in rejected] == [
        ("https://cnbc.com/rej", "skipped", "none")
    ]
    one = crawler.get(f"/discovered/{rejected[0]['id']}").json()
    assert one["match_class"] == "reject"
    assert crawler.get("/discovered", params={"match_class": "bogus"}).status_code == 422


def test_crawler_reset_refuses_a_rejected_pair_and_resets_others(crawler: TestClient) -> None:
    rows = {r["url"]: r["id"] for r in crawler.get("/discovered").json()}

    refused = crawler.post(f"/discovered/{rows['https://cnbc.com/rej']}/reset")
    assert refused.status_code == 409
    assert "matcher-rejected" in refused.json()["detail"]
    assert crawler.get(f"/discovered/{rows['https://cnbc.com/rej']}").json()["status"] == "skipped"

    ok = crawler.post(f"/discovered/{rows['https://cnbc.com/rev']}/reset")
    assert ok.status_code == 200
    assert ok.json() == {"id": rows["https://cnbc.com/rev"], "status": "pending"}


def test_crawler_status_leaves_out_rejected_rows_and_reports_the_classes(
    crawler: TestClient,
) -> None:
    got = crawler.get("/status").json()
    assert got["counts"] == {"pending": 2}
    assert got["total"] == 2
    assert got["by_match_class"] == {"accept": 1, "review": 1, "reject": 1}
