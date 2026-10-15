"""The post-fetch match re-evaluation is wired into every fetch path (T-056c)."""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from portfolio_common.db import Database

from apps import news_crawler_api
from cli import news_crawler_cli
from extractor.db import connect, ensure_articles_table, record_fetch
from news_collector.models import DiscoveredURL
from news_collector.storage.queue import URLQueue

_URL = "https://www.cnbc.com/2024/01/15/earnings-beat.html"
_FETCHED_TITLE = "Apple Inc. earnings beat"


def _row(url: str, match_class: str) -> DiscoveredURL:
    return DiscoveredURL(
        url=url,
        domain="cnbc.com",
        company="Apple Inc.",
        ticker="AAPL",
        source="sitemap",
        discovered_at=datetime(2026, 8, 10),
        match_class=match_class,  # type: ignore[arg-type]
        match_reason="weak_ticker" if match_class == "review" else "none",
    )


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "urls.db")
    queue = URLQueue(path)
    queue.initialize()
    queue.enqueue_batch([_row(_URL, "review"), _row("https://www.cnbc.com/rej.html", "reject")])
    return path


def _id(db_path: str, url: str) -> int:
    with sqlite3.connect(db_path) as c:
        return int(c.execute("SELECT id FROM discovered_urls WHERE url = ?", (url,)).fetchone()[0])


def _state(db_path: str, url: str) -> tuple[str, str, int]:
    with sqlite3.connect(db_path) as c:
        cls, status = c.execute(
            "SELECT match_class, status FROM discovered_urls WHERE url = ?", (url,)
        ).fetchone()
        n = c.execute(
            "SELECT COUNT(*) FROM articles WHERE id = (SELECT id FROM discovered_urls WHERE url=?)",
            (url,),
        ).fetchone()[0]
    return cls, status, n


def _article(url_id: int, fetch_status: str = "ok", title: str | None = _FETCHED_TITLE) -> dict:
    return {
        "id": url_id,
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "title": title,
        "body_text": "body",
        "fetch_status": fetch_status,
        "http_status_code": 200,
        "fetched_at": "2026-08-10T00:00:00",
    }


def test_record_fetch_promotes_a_review_row_after_an_ok_fetch(db_path: str) -> None:
    conn = connect(db_path)
    ensure_articles_table(conn)
    record_fetch(conn, _article(_id(db_path, _URL)))
    conn.close()

    assert _state(db_path, _URL) == ("accept", "ok", 1)


def test_record_fetch_does_not_reevaluate_a_failed_fetch(db_path: str) -> None:
    conn = connect(db_path)
    ensure_articles_table(conn)
    record_fetch(conn, _article(_id(db_path, _URL), fetch_status="failed"))
    conn.close()

    assert _state(db_path, _URL)[0] == "review"


@pytest.fixture
def crawler(db_path: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    def _conn() -> Iterator[Database]:
        conn = connect(db_path, check_same_thread=False)
        ensure_articles_table(conn)
        try:
            yield conn
        finally:
            conn.close()

    async def fake_process_one(client, scheduler, row, **kwargs):  # type: ignore[no-untyped-def]
        return _article(row["id"])

    async def fake_gics() -> dict:
        return {}

    monkeypatch.setattr(news_crawler_api, "process_one", fake_process_one)
    news_crawler_api.app.dependency_overrides[news_crawler_api.get_conn] = _conn
    news_crawler_api.app.dependency_overrides[news_crawler_api.get_gics_map] = fake_gics
    yield TestClient(news_crawler_api.app)
    news_crawler_api.app.dependency_overrides.clear()


def test_api_extract_one_reevaluates_the_row(crawler: TestClient, db_path: str) -> None:
    resp = crawler.post(f"/extract/{_id(db_path, _URL)}")

    assert resp.status_code == 200
    assert _state(db_path, _URL) == ("accept", "ok", 1)


def test_api_extract_one_refuses_a_rejected_row(crawler: TestClient, db_path: str) -> None:
    resp = crawler.post(f"/extract/{_id(db_path, 'https://www.cnbc.com/rej.html')}")

    assert resp.status_code == 409
    # no article saved, the row keeps the `skipped AND reject` convention
    assert _state(db_path, "https://www.cnbc.com/rej.html") == ("reject", "skipped", 0)


def test_api_batch_extract_reevaluates_the_rows(crawler: TestClient, db_path: str) -> None:
    resp = crawler.post("/extract/run", params={"limit": 5})

    assert resp.status_code == 200
    assert resp.json()["processed"] == 1  # the rejected row is not selected
    assert _state(db_path, _URL)[0] == "accept"


def test_cli_run_reevaluates_the_rows(db_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_process_one(client, scheduler, row, **kwargs):  # type: ignore[no-untyped-def]
        return _article(row["id"])

    async def fake_gics(client) -> dict:  # type: ignore[no-untyped-def]
        return {}

    monkeypatch.setattr(news_crawler_cli, "process_one", fake_process_one)
    monkeypatch.setattr(news_crawler_cli, "load_gics_map", fake_gics)
    args = argparse.Namespace(
        db=db_path,
        limit=None,
        retry_failed=False,
        default_concurrency=2,
        cnbc_concurrency=4,
    )

    counts = asyncio.run(news_crawler_cli.run(args))

    assert counts == {"ok": 1}
    assert _state(db_path, _URL) == ("accept", "ok", 1)
    assert _state(db_path, "https://www.cnbc.com/rej.html") == ("reject", "skipped", 0)


def test_record_fetch_reevaluates_a_paywalled_page_that_was_read(db_path: str) -> None:
    conn = connect(db_path)
    ensure_articles_table(conn)
    record_fetch(conn, _article(_id(db_path, _URL), fetch_status="paywalled"))
    conn.close()

    assert _state(db_path, _URL) == ("accept", "paywalled", 1)
