"""`load_env()`: `.env` wins over the inherited environment (T-066b)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from data_mining import env
from data_mining.env import LOGGED_VALUES, load_env
from data_mining.startup_check import allow_new_db, apply_allow_new_db_flag, inspect_database

REPO = Path(__file__).resolve().parents[2]


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
    monkeypatch.delenv("NEW_ONE", raising=False)
    load_env(_write(tmp_path, "FINNHUB_API_KEY=new\nNEW_ONE=1\n"))
    assert os.environ["FINNHUB_API_KEY"] == "new"
    assert os.environ["NEW_ONE"] == "1"
    monkeypatch.delenv("NEW_ONE")


@pytest.mark.parametrize(
    "line", ["DATABASE_URL=\n", "DATABASE_URL=   \n", "DATABASE_URL\n", "OTHER=x\n"]
)
def test_blank_or_absent_value_leaves_the_inherited_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", "/system.db")
    assert "DATABASE_URL" not in load_env(_write(tmp_path, line))
    assert os.environ["DATABASE_URL"] == "/system.db"
    monkeypatch.delenv("OTHER", raising=False)


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
        "DISCOVERY_END_DATE",
        "ALLOW_NEW_DB",
    } == LOGGED_VALUES


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
    monkeypatch.delenv("ALLOW_NEW_DB", raising=False)
    load_env(_write(tmp_path, "DATABASE_URL=/x.db\n"))  # ALLOW_NEW_DB absent from .env
    apply_allow_new_db_flag(["--allow-new-db"])
    assert allow_new_db()
    # and a .env value loaded before the flag does not undo it
    load_env(_write(tmp_path, "ALLOW_NEW_DB=0\n"))
    apply_allow_new_db_flag(["--allow-new-db"])
    assert allow_new_db()
    monkeypatch.delenv("ALLOW_NEW_DB", raising=False)


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
