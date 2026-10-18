"""Start-up check of the shared pipeline database (T-066a).

A service that opens `urls.db` on a wrong path used to answer with zero counts and no
warning (the literal `data/urls.db` is a schema with no rows). `check_startup` logs which
database the process resolved and why, warns when an inherited `DATABASE_URL` hides the
one in `.env`, and refuses to start on a database that cannot be the real one.

`.env` wins over the inherited environment (T-066b, `data_mining.env.load_env`, which every entry
point calls first). A difference that remains here means `load_env()` did not run in this process.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from data_mining import env
from data_mining.db import resolve_db_path

log = logging.getLogger("data_mining.startup")

ALLOW_NEW_DB_FLAG = "--allow-new-db"
_ALLOW_NEW_DB_ENV = "ALLOW_NEW_DB"
_TRUE = {"1", "true", "yes", "on"}

# What was inspected in this process already, by resolved path and source: the gateway forwards
# the lifespan of the services it mounts, so each of them would repeat the same lines and the
# same read of the file (a `COUNT(*)` over about a million rows takes 0.65 s).
_reports: dict[str, DbReport] = {}


class StartupCheckError(RuntimeError):
    """The database is not usable by this service; the message says what to do."""


@dataclass
class DbReport:
    path: Path
    source: str  # ".env", "inherited" or "default"
    exists: bool
    size: int | None
    parent_exists: bool
    has_table: bool = False
    rows: int | None = None
    error: str | None = None  # the file exists but cannot be read as a database
    warnings: list[str] = field(default_factory=list)


def apply_allow_new_db_flag(argv: list[str]) -> None:
    """`--allow-new-db` on a command line sets `ALLOW_NEW_DB=1` for this process."""
    if ALLOW_NEW_DB_FLAG in argv:
        os.environ[_ALLOW_NEW_DB_ENV] = "1"


def allow_new_db() -> bool:
    return os.environ.get(_ALLOW_NEW_DB_ENV, "").strip().lower() in _TRUE


def _resolve_source(dotenv_path: str | Path | None) -> tuple[Path, str, list[str]]:
    """The absolute path, where the value came from, and the warnings about it.

    `.env` is the file `load_env()` reads (`env.root_env_file()`, looked up at call time so a
    test can replace it) unless `dotenv_path` names another.

    The source is `.env`, `inherited` or `default`. A value that is both inherited and in `.env`
    (identical) reports `.env`.
    """
    inherited = os.environ.get("DATABASE_URL", "").strip()
    # Read the file: after `load_env()` the two agree, and a difference means it was not applied.
    env_file = env.root_env_file() if dotenv_path is None else dotenv_path
    from_env_file = (dotenv_values(env_file).get("DATABASE_URL") or "").strip() if env_file else ""
    warnings: list[str] = []
    if not inherited:
        source = "default"
        if "DATABASE_URL" in os.environ and from_env_file:
            warnings.append(
                f"DATABASE_URL is set but empty in the environment while .env has "
                f"{from_env_file!r}: load_env() did not run in this process; the default path is used"
            )
    elif from_env_file and from_env_file == inherited:
        source = ".env"
    else:
        source = "inherited"
        if from_env_file:
            warnings.append(
                f"DATABASE_URL in the environment ({inherited!r}) differs from .env "
                f"({from_env_file!r}): load_env() did not run in this process; the environment value is used"
            )
    return Path(resolve_db_path()).resolve(), source, warnings


def _look(path: Path, source: str, warnings: list[str]) -> DbReport:
    report = DbReport(
        path=path,
        source=source,
        exists=path.is_file(),
        size=path.stat().st_size if path.is_file() else None,
        parent_exists=path.parent.is_dir(),
        warnings=warnings,
    )
    if report.exists:
        _read_counts(report)
    return report


def inspect_database(dotenv_path: str | Path | None = None) -> DbReport:
    """Resolve `DATABASE_URL` the way the services do and look at the file, read-only."""
    return _look(*_resolve_source(dotenv_path))


def _read_counts(report: DbReport) -> None:
    try:
        conn = sqlite3.connect(f"file:{report.path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        report.error = str(exc)
        return
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='discovered_urls'"
        ).fetchone()
        report.has_table = row is not None
        if report.has_table:
            report.rows = conn.execute("SELECT COUNT(*) FROM discovered_urls").fetchone()[0]
    except sqlite3.Error as exc:  # not a database, or locked, or a stale WAL (see _problem)
        report.error = str(exc)
    finally:
        conn.close()


def _log_line(report: DbReport) -> str:
    if not report.exists:
        state = "missing"
    elif report.error:
        state = f"unreadable ({report.error})"
    elif not report.has_table:
        state = f"{report.size} bytes, no discovered_urls table"
    else:
        state = f"{report.size} bytes, discovered_urls={report.rows} rows"
    return f"DATABASE_URL source={report.source} path={report.path} file: {state}"


def check_startup(
    *,
    creates_db: bool,
    allow_new: bool | None = None,
    dotenv_path: str | Path | None = None,
) -> DbReport:
    """Log the effective database, warn on a hidden `.env` value, refuse an unusable one.

    The first call per process inspects the file and logs; later calls (the gateway forwards the
    lifespan of the apps it mounts) reuse that report and apply their own rule to it.
    `creates_db=False` (gateway, crawler): refuse a missing file, a missing `discovered_urls`
    table or no rows (a schema with no rows is exactly the file that once served `total: 0`).
    `creates_db=True` (collector, which creates the database on first use): refuse only a
    missing parent directory or an unreadable file; a new database is a notice.
    `allow_new` (default: `ALLOW_NEW_DB`, or `--allow-new-db` through
    `apply_allow_new_db_flag`) lets the first kind start on an empty database.
    Raises `StartupCheckError`.
    """
    path, source, warnings = _resolve_source(dotenv_path)
    key = f"{path}|{source}"
    first = key not in _reports
    if first:
        _reports[key] = _look(path, source, warnings)
    report = _reports[key]
    if first:
        log.info(_log_line(report))
        for warning in report.warnings:
            log.warning(warning)

    problem = _problem(report, creates_db, allow_new_db() if allow_new is None else allow_new)
    if problem:
        log.error("Refusing to start: %s. %s", _log_line(report), problem)
        raise StartupCheckError(
            f"{_log_line(report)}. {problem} Set DATABASE_URL to the real database in .env "
            f"(it wins over an inherited value); to start on an empty database on purpose, set "
            f"{_ALLOW_NEW_DB_ENV}=1 or pass {ALLOW_NEW_DB_FLAG}."
        )
    if creates_db and not report.exists and first:
        log.warning("Notice: %s does not exist; it will be created on first use", report.path)
    return report


def _problem(report: DbReport, creates_db: bool, allow_new: bool) -> str | None:
    """Why this service must not start on `report`, or None."""
    if report.error:
        return (
            "The file could not be opened read-only: this can be a stale or locked WAL "
            "(a leftover -wal/-shm beside it), not necessarily a corrupt file."
        )
    if creates_db:
        return (
            None if report.parent_exists else f"The directory {report.path.parent} does not exist."
        )
    if allow_new:
        return None
    checks = (
        (report.exists, "The database file does not exist."),
        (report.has_table, "The database has no discovered_urls table."),
        (bool(report.rows), "discovered_urls has no rows."),
    )
    return next((message for ok, message in checks if not ok), None)
