"""`load_env()`: `.env` wins over the inherited environment (T-066b)."""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from data_mining import env, startup_check
from data_mining.env import LOGGED_VALUES, load_env
from data_mining.startup_check import (
    allow_new_db,
    apply_allow_new_db_flag,
    check_startup,
    inspect_database,
    log_again,
)

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh_reports() -> Iterator[None]:
    startup_check._reports.clear()
    yield
    startup_check._reports.clear()


def _unset(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Absent now, and removed again at teardown even if the test fails after `load_env()`."""
    for name in names:
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text)
    return path


def test_env_file_value_wins_over_inherited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "../data/urls.db")
    assert load_env(_write(tmp_path, "DATABASE_URL=/real/urls.db\n")) == ["DATABASE_URL"]
    assert os.environ["DATABASE_URL"] == "/real/urls.db"


def test_every_variable_in_the_file_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FINNHUB_API_KEY", "old")
    _unset(monkeypatch, "NEW_ONE")
    load_env(_write(tmp_path, "FINNHUB_API_KEY=new\nNEW_ONE=1\n"))
    assert os.environ["FINNHUB_API_KEY"] == "new"
    assert os.environ["NEW_ONE"] == "1"


@pytest.mark.parametrize(
    "line", ["DATABASE_URL=\n", "DATABASE_URL=   \n", "DATABASE_URL\n", "OTHER=x\n"]
)
def test_blank_or_absent_value_leaves_the_inherited_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", "/system.db")
    _unset(monkeypatch, "OTHER")
    assert "DATABASE_URL" not in load_env(_write(tmp_path, line))
    assert os.environ["DATABASE_URL"] == "/system.db"


def test_missing_file_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "/system.db")
    assert load_env(tmp_path / "nope.env") == []
    assert os.environ["DATABASE_URL"] == "/system.db"


def test_root_is_the_repo_not_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert env._repo_root() == REPO


def test_no_pyproject_above_means_no_file_and_one_notice(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(env, "root_env_file", lambda: None)
    monkeypatch.setenv("DATABASE_URL", "/system.db")
    with caplog.at_level(logging.WARNING, logger="data_mining.env"):
        assert load_env() == []
    assert os.environ["DATABASE_URL"] == "/system.db"
    assert len(caplog.records) == 1


def test_notice_prints_values_only_for_the_allow_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DATABASE_URL", "../data/urls.db")
    monkeypatch.setenv("HTTPS_PROXY", "http://old-user:old-secret@proxy:1")
    path = _write(
        tmp_path, "DATABASE_URL=/real/urls.db\nHTTPS_PROXY=http://user:s3cret@proxy:8080\n"
    )
    with caplog.at_level(logging.WARNING, logger="data_mining.env"):
        load_env(path)
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 2
    db_line = next(line for line in lines if "DATABASE_URL" in line)
    assert "/real/urls.db" in db_line
    assert "../data/urls.db" in db_line
    proxy_line = next(line for line in lines if "HTTPS_PROXY" in line)
    assert "s3cret" not in proxy_line
    assert "old-secret" not in proxy_line
    assert "proxy" not in proxy_line.replace("HTTPS_PROXY", "")


def test_a_value_that_equals_the_inherited_one_is_not_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DATABASE_URL", "/same.db")
    with caplog.at_level(logging.WARNING, logger="data_mining.env"):
        assert load_env(_write(tmp_path, "DATABASE_URL=/same.db\n")) == []
    assert caplog.records == []


def test_allow_list_is_the_one_the_maintainer_confirmed() -> None:
    assert {
        "DATABASE_URL",
        "UNIVERSE_DB_PATH",
        "DISCOVERY_START_DATE",
        "ALLOW_NEW_DB",
    } == LOGGED_VALUES
    assert "DISCOVERY_END_DATE" not in LOGGED_VALUES  # retired by T-077


def test_start_up_check_reports_env_as_the_source_after_load_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "../data/urls.db")
    path = _write(tmp_path, f"DATABASE_URL={tmp_path / 'real.db'}\n")
    load_env(path)
    report = inspect_database(path)
    assert report.source == ".env"
    assert report.path == (tmp_path / "real.db").resolve()
    assert report.warnings == []


def test_a_flag_applied_after_load_env_still_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unset(monkeypatch, "ALLOW_NEW_DB")
    load_env(_write(tmp_path, "DATABASE_URL=/x.db\n"))  # ALLOW_NEW_DB absent from .env
    apply_allow_new_db_flag(["--allow-new-db"])
    assert allow_new_db()
    # and a .env value loaded before the flag does not undo it
    load_env(_write(tmp_path, "ALLOW_NEW_DB=0\n"))
    apply_allow_new_db_flag(["--allow-new-db"])
    assert allow_new_db()


def test_real_process_from_another_directory_picks_the_repo_env(tmp_path: Path) -> None:
    """A fresh interpreter, working directory elsewhere, a fake root with its own `.env`."""
    root = tmp_path / "repo"
    (root / "src" / "data_mining").mkdir(parents=True)
    (root / "pyproject.toml").write_text("")
    (root / ".env").write_text("DATABASE_URL=/from-env-file.db\n")
    (root / "src" / "data_mining" / "__init__.py").write_text("")
    (root / "src" / "data_mining" / "env.py").write_text(
        (REPO / "src" / "data_mining" / "env.py").read_text()
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    code = (
        "import os, sys; sys.path.insert(0, sys.argv[1]);"
        "from data_mining.env import load_env; load_env(); print(os.environ['DATABASE_URL'])"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code, str(root / "src")],
        cwd=elsewhere,
        env={"DATABASE_URL": "../data/urls.db", "PATH": ""},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "/from-env-file.db"


def test_start_up_check_without_a_path_ignores_the_real_env_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The conftest guard points `root_env_file` at a missing file: no `.env` value, no warning."""
    monkeypatch.setenv("DATABASE_URL", "/some/test.db")
    report = inspect_database()
    assert report.source == "inherited"
    assert report.warnings == []


def test_start_up_check_without_a_path_reads_the_file_load_env_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, f"DATABASE_URL={tmp_path / 'real.db'}\n")
    monkeypatch.setattr(env, "root_env_file", lambda: path)
    monkeypatch.setenv("DATABASE_URL", "../data/urls.db")
    load_env()
    report = inspect_database()
    assert report.source == ".env"
    assert report.warnings == []
    assert report.path == (tmp_path / "real.db").resolve()


def _real_db(tmp_path: Path) -> Path:
    db = tmp_path / "real.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE discovered_urls (id INTEGER)")
    conn.execute("INSERT INTO discovered_urls VALUES (1)")
    conn.commit()
    conn.close()
    return db


def test_log_again_puts_the_override_and_the_report_in_a_late_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`load_env()` and `check_startup` run before the run's file handler exists."""
    db = _real_db(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "../data/urls.db")
    monkeypatch.setenv("HTTPS_PROXY", "http://old-user:old-secret@proxy:1")
    path = _write(tmp_path, f"DATABASE_URL={db}\nHTTPS_PROXY=http://user:s3cret@proxy:8080\n")
    load_env(path)
    check_startup(creates_db=False, dotenv_path=path)
    caplog.clear()  # nothing so far reached a handler installed afterwards
    with caplog.at_level(logging.INFO, logger="data_mining.startup"):
        log_again()
    messages = [r.getMessage() for r in caplog.records]
    overrides = [m for m in messages if ".env override" in m]
    assert len(overrides) == 2
    assert any(str(db) in m and "../data/urls.db" in m for m in overrides)
    proxy = next(m for m in overrides if "HTTPS_PROXY" in m)
    assert "s3cret" not in proxy
    assert "old-secret" not in proxy
    assert any("source=.env" in m for m in messages)
    assert max(i for i, m in enumerate(messages) if ".env override" in m) < next(
        i for i, m in enumerate(messages) if "source=" in m
    )  # the reason comes before the line that names the source


def test_no_override_means_no_override_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db = _real_db(tmp_path)
    monkeypatch.setenv("DATABASE_URL", str(db))
    path = _write(tmp_path, f"DATABASE_URL={db}\n")
    load_env(path)
    check_startup(creates_db=False, dotenv_path=path)
    with caplog.at_level(logging.INFO, logger="data_mining.startup"):
        log_again()
    assert not [r for r in caplog.records if ".env override" in r.getMessage()]


def test_retired_variable_in_the_file_gets_one_notice_by_name_and_is_not_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write(tmp_path, "DISCOVERY_END_DATE=2026-08-10\n")
    with caplog.at_level(logging.WARNING, logger="data_mining.env"):
        assert load_env(path) == []
        load_env(path)
        load_env(path)
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1  # deduplicated by name, three calls
    assert "DISCOVERY_END_DATE" in lines[0]
    assert "2026-08-10" not in lines[0]
    assert "DISCOVERY_END_DATE" not in os.environ


def test_retired_variable_in_the_system_environment_gets_the_notice_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DISCOVERY_END_DATE", "2026-08-10")
    with caplog.at_level(logging.WARNING, logger="data_mining.env"):
        load_env(tmp_path / "missing.env")  # no file at all: the system value still counts
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1
    assert "system environment" in lines[0]
    assert "2026-08-10" not in lines[0]


def test_a_blank_retired_variable_gives_no_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DISCOVERY_END_DATE", "  ")
    with caplog.at_level(logging.WARNING, logger="data_mining.env"):
        load_env(_write(tmp_path, "DISCOVERY_END_DATE=\n"))
    assert caplog.records == []


def test_log_again_labels_each_notice_and_the_retired_one_is_not_an_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DATABASE_URL", "../data/urls.db")
    load_env(_write(tmp_path, "DATABASE_URL=/real.db\nDISCOVERY_END_DATE=2026-08-10\n"))
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="data_mining.startup"):
        log_again()
    messages = [r.getMessage() for r in caplog.records]
    retired = [m for m in messages if "DISCOVERY_END_DATE" in m]
    assert len(retired) == 1
    assert retired[0].startswith("retired variable: ")
    assert ".env override" not in retired[0]
    assert sum(m.startswith(".env override: ") for m in messages) == 1
