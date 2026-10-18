"""The start-up check of the shared database (T-066a)."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps import gateway, news_collector_api, news_crawler_api
from data_mining import startup_check
from data_mining.startup_check import (
    StartupCheckError,
    apply_allow_new_db_flag,
    check_startup,
    inspect_database,
)
from news_collector.models import DiscoveredURL
from news_collector.storage.queue import URLQueue


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    startup_check._reported.clear()
    monkeypatch.delenv("ALLOW_NEW_DB", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    yield
    startup_check._reported.clear()


def _schema_only(path: Path) -> Path:
    queue = URLQueue(path)
    queue.initialize()
    queue.close()
    return path


def _populated(path: Path) -> Path:
    queue = URLQueue(path)
    queue.initialize()
    queue.enqueue_batch(
        [
            DiscoveredURL(
                url="https://cnbc.com/a-1",
                domain="cnbc.com",
                company="Apple Inc.",
                ticker="AAPL",
                source="sitemap",
                discovered_at=datetime(2026, 8, 10),
                match_class="accept",
                match_reason="name",
            )
        ]
    )
    queue.close()
    return path


def _env_file(tmp_path: Path, value: str | None) -> Path:
    path = tmp_path / ".env"
    path.write_text("" if value is None else f"DATABASE_URL={value}\n")
    return path


def test_log_line_names_source_path_and_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db = _populated(tmp_path / "real.db")
    monkeypatch.setenv("DATABASE_URL", str(db))
    with caplog.at_level(logging.INFO, logger="data_mining.startup"):
        check_startup(creates_db=False, dotenv_path=_env_file(tmp_path, str(db)))
    line = caplog.records[0].getMessage()
    assert "source=.env" in line
    assert str(db.resolve()) in line
    assert "discovered_urls=1 rows" in line
    assert f"{db.stat().st_size} bytes" in line


@pytest.mark.parametrize(
    ("inherited", "in_env_file", "source"),
    [(None, None, "default"), ("/a.db", None, "inherited"), ("/a.db", "/a.db", ".env")],
)
def test_source_of_the_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inherited: str | None,
    in_env_file: str | None,
    source: str,
) -> None:
    if inherited:
        monkeypatch.setenv("DATABASE_URL", inherited)
    report = inspect_database(_env_file(tmp_path, in_env_file))
    assert report.source == source
    assert report.warnings == []


def test_inherited_value_that_differs_from_env_file_is_warned_and_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DATABASE_URL", "../data/urls.db")
    with caplog.at_level(logging.INFO, logger="data_mining.startup"):
        report = inspect_database(_env_file(tmp_path, "/real/urls.db"))
    assert report.source == "inherited"
    assert report.path.name == "urls.db"
    assert report.path.parent.name == "data"  # the inherited value won
    (warning,) = report.warnings
    assert "DATABASE_URL" in warning
    assert "'../data/urls.db'" in warning
    assert "'/real/urls.db'" in warning


def test_empty_inherited_value_hiding_env_file_is_warned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    report = inspect_database(_env_file(tmp_path, "/real/urls.db"))
    assert report.source == "default"
    assert any("empty" in w and "'/real/urls.db'" in w for w in report.warnings)


def test_warning_is_logged_once_per_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db = _populated(tmp_path / "real.db")
    monkeypatch.setenv("DATABASE_URL", str(db))
    env_file = _env_file(tmp_path, "/other.db")
    with caplog.at_level(logging.INFO, logger="data_mining.startup"):
        check_startup(creates_db=False, dotenv_path=env_file)
        check_startup(creates_db=False, dotenv_path=env_file)
    assert len(caplog.records) == 2  # the line and the warning, not four


def test_strict_refuses_a_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", str(tmp_path / "nowhere.db"))
    with pytest.raises(StartupCheckError, match="does not exist"):
        check_startup(creates_db=False, dotenv_path=_env_file(tmp_path, None))


def test_strict_refuses_a_schema_only_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The case that happened: `data/urls.db` is a schema with no rows and served `total: 0`."""
    db = _schema_only(tmp_path / "urls.db")
    assert db.stat().st_size > 0  # not a zero-byte file
    monkeypatch.setenv("DATABASE_URL", str(db))
    with pytest.raises(StartupCheckError, match="no rows"):
        check_startup(creates_db=False, dotenv_path=_env_file(tmp_path, None))


def test_strict_refuses_a_file_without_the_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "other.db"
    sqlite3.connect(db).execute("CREATE TABLE t (x)").connection.close()
    monkeypatch.setenv("DATABASE_URL", str(db))
    with pytest.raises(StartupCheckError, match="no discovered_urls table"):
        check_startup(creates_db=False, dotenv_path=_env_file(tmp_path, None))


def test_strict_accepts_a_populated_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _populated(tmp_path / "real.db")
    monkeypatch.setenv("DATABASE_URL", str(db))
    report = check_startup(creates_db=False, dotenv_path=_env_file(tmp_path, None))
    assert report.rows == 1


@pytest.mark.parametrize("how", ["env", "flag"])
def test_allow_new_db_lets_an_empty_database_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", str(_schema_only(tmp_path / "urls.db")))
    if how == "env":
        monkeypatch.setenv("ALLOW_NEW_DB", "1")
    else:
        apply_allow_new_db_flag(["gateway.py", "--allow-new-db"])
    try:
        check_startup(creates_db=False, dotenv_path=_env_file(tmp_path, None))
    finally:
        monkeypatch.delenv("ALLOW_NEW_DB", raising=False)


def test_a_file_that_is_not_a_database_is_refused_by_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not sqlite" * 100)
    monkeypatch.setenv("DATABASE_URL", str(bad))
    for creates in (False, True):
        with pytest.raises(StartupCheckError, match="cannot be read"):
            check_startup(creates_db=creates, dotenv_path=_env_file(tmp_path, None))


def test_collector_refuses_a_missing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", str(tmp_path / "no-dir" / "urls.db"))
    with pytest.raises(StartupCheckError, match="directory"):
        check_startup(creates_db=True, dotenv_path=_env_file(tmp_path, None))


def test_collector_starts_on_a_new_database_with_a_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DATABASE_URL", str(tmp_path / "new.db"))
    with caplog.at_level(logging.INFO, logger="data_mining.startup"):
        check_startup(creates_db=True, dotenv_path=_env_file(tmp_path, None))
    assert any("will be created" in r.getMessage() for r in caplog.records)
    assert not (tmp_path / "new.db").exists()  # the check reads, it does not create


def test_crawler_app_refuses_to_start_on_an_empty_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", str(_schema_only(tmp_path / "urls.db")))
    with pytest.raises(StartupCheckError), TestClient(news_crawler_api.app):
        pass
    monkeypatch.setenv("DATABASE_URL", str(_populated(tmp_path / "real.db")))
    startup_check._reported.clear()
    with TestClient(news_crawler_api.app):
        pass


def test_gateway_refuses_to_start_on_an_empty_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", str(_schema_only(tmp_path / "urls.db")))
    with pytest.raises(StartupCheckError), TestClient(gateway.app):
        pass


def test_collector_app_starts_on_a_new_database_and_refuses_a_missing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", str(tmp_path / "new.db"))
    with TestClient(news_collector_api.app):
        pass
    monkeypatch.setenv("DATABASE_URL", str(tmp_path / "no-dir" / "urls.db"))
    with pytest.raises(StartupCheckError, match="directory"), TestClient(news_collector_api.app):
        pass
