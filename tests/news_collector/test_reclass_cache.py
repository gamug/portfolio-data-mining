"""Re-class the sitemap cache with the current matcher, with no network (T-072)."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sitemap_helpers import RANGE, ROOT, START, Clock, Site, cnbc_strategy, leaf, seen

from data_mining.matching import Match
from news_collector import main as collector_main
from news_collector import reclass_cache
from news_collector.api.deps import get_client
from news_collector.api.routers import discovery
from news_collector.api.schemas import MAX_RECLASS_TICKERS, ReclassRequest
from news_collector.connectors.cnbc import CNBCConnector
from news_collector.models import Company, DateRange
from news_collector.reclass_cache import ReclassReport, reclass_from_cache
from news_collector.sitemap_cache import SitemapCache
from news_collector.storage.queue import URLQueue

DOMAIN = "cnbc.com"
APPLE = Company(ticker="AAPL", name="Apple Inc.")
TITLED = "https://www.cnbc.com/2024/05/01/stock-picks-for-the-week.html"  # only the title names it
PLAIN = "https://www.cnbc.com/2024/05/02/stock-picks-for-the-month.html"  # nothing names it
LEFT = "https://www.cnbc.com/2024/05/03/stock-picks-that-left.html"  # leaves the sitemap
TITLE = "Apple Inc. reports record earnings"
TWO = 2


def connector() -> CNBCConnector:
    conn = CNBCConnector(MagicMock())
    conn.config.sitemap_roots = [ROOT]
    conn.config.rss_feeds = []
    conn.config.wayback_fallback = False
    return conn


async def crawl(db: str, site: Site, clock: Clock, conn: CNBCConnector) -> None:
    cache = SitemapCache(DOMAIN, [ROOT], db, site.fetch, conn.is_article_url, clock)
    await cache.begin(RANGE, force=True)


def stored(
    db: str,
    sql: str = "SELECT url, match_class, status FROM discovered_urls ORDER BY url",
    *params: object,
) -> list:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def cnbc_connectors(_client: object) -> dict[str, CNBCConnector]:
    return {DOMAIN: connector()}


def digest(db: str) -> str:
    return hashlib.sha256(Path(db).read_bytes()).hexdigest()


@pytest.fixture
def site() -> Site:
    return Site(
        {
            ROOT: leaf(
                (TITLED, "2024-05-01", TITLE),
                (PLAIN, "2024-05-02"),
                (LEFT, "2024-05-03", TITLE),
            )
        }
    )


async def run(db: str, conn: CNBCConnector, *, apply: bool, companies=(APPLE,), **kw):  # type: ignore[no-untyped-def]
    return await reclass_from_cache(db, {DOMAIN: conn}, list(companies), RANGE, apply=apply, **kw)


# --- what it does ------------------------------------------------------------------------------------


async def test_a_dry_run_reads_the_cache_and_writes_nothing(db: str, site: Site) -> None:
    conn = connector()
    await crawl(db, site, Clock(), conn)
    before = digest(db)
    fetched = dict(site.fetches)
    report = await run(db, conn, apply=False)
    assert digest(db) == before  # not one byte of the file changed
    assert site.fetches == fetched  # and nothing was fetched
    assert report.applied is False
    [result] = report.results
    assert (result.entries, result.new, result.promotable) == (3, 2, 0)  # TITLED and LEFT
    assert result.classes["accept"] == TWO
    assert result.classes["reject"] == 1  # PLAIN: counted, never stored
    assert stored(db) == []


async def test_apply_stores_the_accept_and_review_pairs_and_never_a_reject(
    db: str, site: Site
) -> None:
    conn = connector()
    await crawl(db, site, Clock(), conn)
    report = await run(db, conn, apply=True)
    assert report.total("inserted") == TWO
    assert sorted((u, c) for u, c, _s in stored(db)) == sorted(
        [(LEFT, "accept"), (TITLED, "accept")]
    )
    assert stored(db, "SELECT COUNT(*) FROM discovered_urls WHERE match_class = 'reject'") == [(0,)]
    assert stored(
        db, "SELECT pub_date, title, source FROM discovered_urls WHERE url = ?", TITLED
    ) == [
        ("2024-05-01", TITLE, "sitemap")  # the cached lastmod and title, as discovery stores them
    ]


async def test_a_second_run_inserts_and_promotes_nothing(db: str, site: Site) -> None:
    conn = connector()
    await crawl(db, site, Clock(), conn)
    await run(db, conn, apply=True)
    again = await run(db, conn, apply=True)
    assert (again.total("inserted"), again.total("promoted")) == (0, 0)
    dry = await run(db, conn, apply=False)
    assert (dry.total("new"), dry.total("promotable")) == (0, 0)


async def test_a_matcher_change_moves_a_pair_from_nothing_to_review(db: str, site: Site) -> None:
    conn = connector()
    await crawl(db, site, Clock(), conn)
    mode = {"class": "reject"}
    real = conn.explain_match

    def matcher(url: str, title: str, company: str, ticker: str) -> Match:
        return Match(mode["class"], "rule") if url == PLAIN else real(url, title, company, ticker)  # type: ignore[arg-type]

    conn.explain_match = matcher  # type: ignore[method-assign]
    await run(db, conn, apply=True)
    assert PLAIN not in [u for u, _c, _s in stored(db)]  # rejected: not stored
    mode["class"] = "review"  # the matcher changed
    report = await run(db, conn, apply=True)
    assert report.total("inserted") == 1
    assert (PLAIN, "review", "pending") in stored(db)
    mode["class"] = "accept"  # and again: a stored row is promoted, never demoted
    promoted = await run(db, conn, apply=True)
    assert (promoted.total("inserted"), promoted.total("promoted")) == (0, 1)
    mode["class"] = "reject"  # a worse class later changes nothing
    worse = await run(db, conn, apply=True)
    assert (worse.total("inserted"), worse.total("promoted")) == (0, 0)
    assert (PLAIN, "accept", "pending") in stored(db)


async def test_a_stored_reject_is_promoted_and_goes_back_to_pending(db: str, site: Site) -> None:
    conn = connector()
    await crawl(db, site, Clock(), conn)
    URLQueue(db).initialize()  # the tables, as the services made them
    raw = sqlite3.connect(db)
    raw.execute(
        "INSERT INTO discovered_urls (url, domain, company, ticker, source, discovered_at, status, match_class) "
        "VALUES (?, ?, 'Apple Inc.', 'AAPL', 'sitemap', '2026-01-01', 'skipped', 'reject')",
        (TITLED, DOMAIN),
    )
    raw.commit()
    raw.close()
    report = await run(db, conn, apply=True)
    assert (report.total("inserted"), report.total("promoted"), report.total("lifted")) == (1, 1, 1)
    assert (TITLED, "accept", "pending") in stored(db)


async def test_only_the_current_entries_are_re_classed_and_a_stored_row_is_never_touched(
    db: str, site: Site
) -> None:
    conn = connector()
    clock = Clock()
    await crawl(db, site, clock, conn)
    site.pages[ROOT] = leaf((TITLED, "2024-05-01", TITLE), (PLAIN, "2024-05-02"))  # LEFT is gone
    clock.advance(days=1)
    await crawl(db, site, clock, conn)  # a complete crawl: LEFT is expired, still in the table
    assert stored(db, "SELECT COUNT(*) FROM sitemap_entries") == [(3,)]
    raw = sqlite3.connect(db)  # a row stored earlier for the expired URL
    raw.execute(
        "INSERT INTO discovered_urls (url, domain, company, ticker, source, discovered_at, status, match_class) "
        "VALUES (?, ?, 'Apple Inc.', 'AAPL', 'sitemap', '2026-01-01', 'pending', 'accept')",
        (LEFT, DOMAIN),
    )
    raw.commit()
    raw.close()
    report = await run(db, conn, apply=True)
    assert report.results[0].entries == TWO  # LEFT is not read
    assert sorted(u for u, _c, _s in stored(db)) == sorted(
        [LEFT, TITLED]
    )  # TITLED inserted; LEFT untouched
    assert (LEFT, "accept", "pending") in stored(db)


async def test_it_uses_an_old_cache_and_never_crawls(db: str, site: Site) -> None:
    conn = connector()
    await crawl(db, site, Clock(), conn)  # the crawl of 2026-10-01: far older than 7 days
    fetched = dict(site.fetches)
    report = await run(db, conn, apply=True)
    assert report.errors == []
    assert report.total("inserted") == TWO
    assert site.fetches == fetched


async def test_a_domain_with_no_complete_crawl_is_an_error_and_nothing_is_written(db: str) -> None:
    conn = connector()
    report = await run(db, conn, apply=True)
    assert report.results == []
    assert len(report.errors) == 1
    assert "does not cover" in report.errors[0]
    assert stored(db) == []


async def test_a_dry_run_on_a_missing_path_creates_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "typo.db"
    for apply in (False, True):
        report = await run(str(missing), connector(), apply=apply)
        assert report.results == []
        assert "no such database" in report.errors[0]
        assert not missing.exists()


async def test_a_file_without_the_queue_tables_is_an_error_line_not_a_crash(
    db: str, site: Site
) -> None:
    conn = connector()
    await crawl(db, site, Clock(), conn)
    raw = sqlite3.connect(db)
    raw.execute("DROP TABLE discovered_urls")
    raw.commit()
    raw.close()
    report = await run(db, conn, apply=False)
    assert report.results == []
    assert "database error" in report.errors[0]
    assert "AAPL" in report.errors[0]


async def test_classify_entries_gives_the_pairs_discover_builds_from_the_cache(
    db: str, site: Site
) -> None:
    conn = connector()
    cache = SitemapCache(DOMAIN, [ROOT], db, site.fetch, conn.is_article_url, Clock())
    sitemap_run = await cache.begin(RANGE, force=True)
    strategy = cnbc_strategy(site, sitemap_run)
    entries, _ = sitemap_run.entries(RANGE)
    discovered = await strategy.discover(APPLE.name, APPLE.ticker, RANGE)
    classified = strategy.classify_entries(entries, APPLE.name, APPLE.ticker)
    assert seen(discovered) == seen(classified)  # every class, `reject` included (until T-071)
    assert {p.match_class for p in classified} == {"accept", "reject"}


async def test_a_range_wider_than_the_cache_is_an_error(db: str, site: Site) -> None:
    conn = connector()
    cache = SitemapCache(DOMAIN, [ROOT], db, site.fetch, conn.is_article_url, Clock())
    await cache.begin(DateRange(start=START.replace(year=2023), end=RANGE.end))
    report = await run(db, conn, apply=True)  # RANGE starts in 2022
    assert report.results == []
    assert "does not cover 2022-01-01" in report.errors[0]


async def test_a_domain_without_roots_is_an_error_line(db: str, site: Site) -> None:
    conn = connector()
    plain = connector()
    plain.config.sitemap_roots = []
    await crawl(db, site, Clock(), conn)
    report = await reclass_from_cache(
        db,
        {DOMAIN: conn, "plain.test": plain},
        [APPLE],
        RANGE,
        domains=[DOMAIN, "plain.test"],
        apply=False,
    )
    assert report.errors == ["plain.test: no sitemap cache for this domain"]
    assert len(report.results) == 1


async def test_the_progress_callback_sees_every_pair(db: str, site: Site) -> None:
    conn = connector()
    await crawl(db, site, Clock(), conn)
    seen: list[str] = []
    await run(
        db,
        conn,
        apply=False,
        companies=(APPLE, Company(ticker="MSFT", name="Microsoft")),
        progress=lambda r: seen.append(r.ticker),
    )
    assert seen == ["AAPL", "MSFT"]


# --- the command and the route ---------------------------------------------------------------------


def test_the_cli_is_a_dry_run_by_default_and_apply_reaches_the_re_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[bool] = []

    async def fake(
        db_path, connectors, companies, date_range, *, domains=None, apply=False, progress=None
    ):  # type: ignore[no-untyped-def]
        seen.append(apply)
        return ReclassReport(applied=apply)

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    async def lookup(tickers: list[str], client: object, start: object) -> list[Company]:
        return [Company(ticker=t, name=f"{t} Inc.") for t in tickers]

    monkeypatch.setattr(collector_main, "reclass_from_cache", fake)
    monkeypatch.setattr(collector_main, "build_client", FakeClient)
    monkeypatch.setattr(collector_main, "build_connectors", cnbc_connectors)
    monkeypatch.setattr(collector_main, "lookup_companies", lookup)
    base = ["--db", str(tmp_path / "q.db"), "--log-dir", "", "reclass-sitemap", "--tickers", "AAPL"]
    for extra in ([], ["--apply"]):
        args = collector_main.build_parser().parse_args(base + extra)
        assert asyncio.run(collector_main.cmd_reclass_sitemap(args)) == 0
    assert seen == [False, True]
    out = capsys.readouterr().out
    assert "dry run (nothing written; --apply writes)" in out
    assert "APPLIED" in out


def test_the_cli_exits_1_when_a_domain_has_no_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake(*_a: object, **_k: object):  # type: ignore[no-untyped-def]
        return ReclassReport(
            applied=False, errors=["cnbc.com: sitemap cache of cnbc.com does not cover x"]
        )

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    async def lookup(tickers: list[str], client: object, start: object) -> list[Company]:
        return [Company(ticker=t, name=f"{t} Inc.") for t in tickers]

    monkeypatch.setattr(collector_main, "reclass_from_cache", fake)
    monkeypatch.setattr(collector_main, "build_client", FakeClient)
    monkeypatch.setattr(collector_main, "build_connectors", cnbc_connectors)
    monkeypatch.setattr(collector_main, "lookup_companies", lookup)
    argv = ["--db", str(tmp_path / "q.db"), "--log-dir", "", "reclass-sitemap", "--tickers", "AAPL"]
    args = collector_main.build_parser().parse_args(argv)
    assert asyncio.run(collector_main.cmd_reclass_sitemap(args)) == 1
    assert "does not cover" in capsys.readouterr().out


async def test_the_route_is_a_dry_run_by_default_and_apply_writes(
    db: str, site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = connector()
    await crawl(db, site, Clock(), conn)

    async def companies(_req: object, _client: object) -> list[Company]:
        return [APPLE]

    monkeypatch.setattr(discovery, "resolve_companies", companies)
    monkeypatch.setattr(discovery, "build_connectors", lambda client: {DOMAIN: conn})
    app = FastAPI()
    app.include_router(discovery.router)
    app.dependency_overrides[get_client] = object
    body = {
        "tickers": ["AAPL"],
        "start_date": "2022-01-01",
        "end_date": "2026-08-10",
        "db_path": db,
    }
    http = TestClient(app)

    dry = http.post("/discovery/reclass-sitemap", json=body).json()
    assert dry["applied"] is False
    assert (dry["would_insert"], dry["inserted"], dry["reject"]) == (TWO, 0, 1)
    assert stored(db) == []

    done = http.post("/discovery/reclass-sitemap", json={**body, "apply": True}).json()
    assert (done["applied"], done["inserted"], done["would_insert"]) == (True, TWO, 0)
    assert done["tickers"][0]["ticker"] == "AAPL"
    assert done["lifted"] == 0 == done["tickers"][0]["lifted"]
    assert len(stored(db)) == TWO
    again = http.post("/discovery/reclass-sitemap", json={**body, "apply": True}).json()
    assert (again["inserted"], again["promoted"]) == (0, 0)


def test_the_route_reports_a_domain_without_a_cache_as_an_error(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def companies(_req: object, _client: object) -> list[Company]:
        return [APPLE]

    monkeypatch.setattr(discovery, "resolve_companies", companies)
    monkeypatch.setattr(discovery, "build_connectors", lambda client: {DOMAIN: connector()})
    app = FastAPI()
    app.include_router(discovery.router)
    app.dependency_overrides[get_client] = object
    res = TestClient(app).post(
        "/discovery/reclass-sitemap",
        json={
            "tickers": ["AAPL"],
            "start_date": "2022-01-01",
            "end_date": "2026-08-10",
            "db_path": db,
        },
    )
    assert res.status_code == 200
    assert "does not cover" in res.json()["errors"][0]
    assert cast(list, res.json()["tickers"]) == []


def test_the_route_needs_tickers_and_caps_them(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.include_router(discovery.router)
    app.dependency_overrides[get_client] = object
    http = TestClient(app)
    none = http.post("/discovery/reclass-sitemap", json={"db_path": db})
    assert none.status_code == 422
    many = [f"T{i}" for i in range(MAX_RECLASS_TICKERS + 1)]
    over = http.post("/discovery/reclass-sitemap", json={"tickers": many, "db_path": db})
    assert over.status_code == 422


def test_the_request_model_carries_the_contract() -> None:
    schema = ReclassRequest.model_json_schema()
    assert "tickers" in schema["required"]
    tickers = schema["properties"]["tickers"]
    assert (tickers["minItems"], tickers["maxItems"]) == (1, MAX_RECLASS_TICKERS)
    assert "S&P 500 is the CLI" in tickers["description"]
    assert "entire S&P 500, fetched" not in tickers["description"]
    assert ReclassRequest(
        tickers=["AAPL"] * MAX_RECLASS_TICKERS
    ).tickers  # as sent: duplicates count


async def test_a_database_error_in_a_later_batch_keeps_what_was_written(
    db: str, site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = connector()
    await crawl(db, site, Clock(), conn)
    monkeypatch.setattr(reclass_cache, "_WRITE_BATCH", 1)
    real = URLQueue.enqueue_batch
    calls = []

    def flaky(self: URLQueue, batch: list) -> int:
        calls.append(1)
        if len(calls) == TWO:
            raise sqlite3.OperationalError("database is locked")
        return real(self, batch)

    monkeypatch.setattr(URLQueue, "enqueue_batch", flaky)
    report = await run(db, conn, apply=True)
    assert len(report.errors) == 1
    assert "database is locked" in report.errors[0]
    assert "1 inserted" in report.errors[0]
    assert [r.inserted for r in report.results] == [1]
    assert report.total("inserted") == len(stored(db)) == 1


async def test_it_logs_one_line_per_ticker(
    db: str, site: Site, caplog: pytest.LogCaptureFixture
) -> None:
    conn = connector()
    await crawl(db, site, Clock(), conn)
    with caplog.at_level("INFO", logger="news_collector.reclass_cache"):
        await run(db, conn, apply=False)
    lines = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("re-class cnbc.com AAPL")
    ]
    assert len(lines) == 1
    assert "new 2" in lines[0]


class _Bar:
    last: _Bar

    def __init__(self, total: int, **_k: object) -> None:
        self.total, self.n = total, 0
        _Bar.last = self

    def update(self, k: int) -> None:
        self.n += k

    def close(self) -> None:
        self.closed_at = (self.n, self.total)


def test_the_bar_ends_where_it_got_when_a_domain_has_no_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake(*_a: object, **_k: object) -> ReclassReport:
        return ReclassReport(applied=False, errors=["cnbc.com: no cache"])

    monkeypatch.setattr(collector_main, "reclass_from_cache", fake)
    monkeypatch.setattr(collector_main, "build_client", _FakeClient)
    monkeypatch.setattr(collector_main, "build_connectors", cnbc_connectors)
    monkeypatch.setattr(collector_main, "lookup_companies", _lookup)
    monkeypatch.setattr(collector_main, "tqdm", _Bar)
    argv = ["--db", str(tmp_path / "q.db"), "--log-dir", "", "reclass-sitemap", "--tickers", "AAPL"]
    args = collector_main.build_parser().parse_args(argv)
    assert asyncio.run(collector_main.cmd_reclass_sitemap(args)) == 1
    assert _Bar.last.closed_at == (0, 0)


def test_the_cli_on_a_missing_database_exits_1_and_creates_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(collector_main, "build_client", _FakeClient)
    monkeypatch.setattr(collector_main, "build_connectors", cnbc_connectors)
    monkeypatch.setattr(collector_main, "lookup_companies", _lookup)
    missing = tmp_path / "typo.db"
    argv = ["--db", str(missing), "--log-dir", "", "reclass-sitemap", "--tickers", "AAPL"]
    args = collector_main.build_parser().parse_args(argv)
    assert asyncio.run(collector_main.cmd_reclass_sitemap(args)) == 1
    assert "no such database" in capsys.readouterr().out
    assert not missing.exists()


class _FakeClient:
    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


async def _lookup(tickers: list[str], client: object, start: object) -> list[Company]:
    return [Company(ticker=t, name=f"{t} Inc.") for t in tickers]
