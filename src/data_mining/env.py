"""The repo-root `.env`, loaded so that it wins over the inherited environment (T-066b).

`load_dotenv()` leaves an inherited variable alone, so a machine-wide `DATABASE_URL` (this
machine's `/etc/environment` has one) hid the project's own. `load_env()` reverses that: a
non-empty value in `.env` replaces the inherited one, and the inherited value is the fallback
when the file, the variable or its value is missing (constitution rule 7).

Every entry point (`apps/*.py`, `cli/*.py`, `news_collector/main.py`) calls it before any
project import that reads the environment; `tests/data_mining/test_env_entrypoints.py` checks.
A variable in `RETIRED` is no longer read by the code: `load_env()` gives it one notice and does
not apply it (T-077).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import dotenv_values

log = logging.getLogger("data_mining.env")

# Only these variables have their values printed when `.env` overrides an inherited one.
# Any other (a proxy URL, another engine's `DATABASE_URL`, an API key) is logged by name only.
LOGGED_VALUES = frozenset(
    {
        "DATABASE_URL",
        "UNIVERSE_DB_PATH",
        "DISCOVERY_START_DATE",
        "ALLOW_NEW_DB",
    }
)

# Variables the code no longer reads (T-077). A non-blank value, in `.env` or in the system
# environment, gets one notice by name (never the value) and is not applied to `os.environ`.
RETIRED = {
    "DISCOVERY_END_DATE": (
        "is no longer read: the end date is the `--end-date` / `end_date` input and defaults to "
        "today (UTC); delete the line"
    ),
}


# What `load_env()` did, as (label, line) pairs. It runs at import, before any `setup_logging()`
# installs the file handler of the run, so these lines are not in the run's file trace; the
# entry point that sets up a file trace calls `replay_notices()` once logging is configured.
_notices: list[tuple[str, str]] = []
_retired_noticed: set[str] = set()  # one notice per name and process, however often load_env runs

_OVERRIDE = ".env override"
_RETIRED = "retired variable"
_NOT_READ = ".env not read"


def _notice(label: str, message: str) -> None:
    _notices.append((label, message))
    log.warning("%s", message)


def override_notices() -> list[str]:
    """The notices `load_env()` logged in this process, for a caller that logs after set-up."""
    return [message for _, message in _notices]


def replay_notices(logger: logging.Logger) -> None:
    """Log the notices `load_env()` kept once more on `logger`, each under its own label."""
    for label, message in _notices:
        logger.warning("%s: %s", label, message)


def _repo_root() -> Path | None:
    """The first directory above this file that holds `pyproject.toml`, or None."""
    return next(
        (p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").is_file()), None
    )


def root_env_file() -> Path | None:
    """The repo-root `.env` (not the current directory's), or None when no root is found.

    Tests replace this function (tests/conftest.py) so none of them reads the real file.
    """
    root = _repo_root()
    return root / ".env" if root else None


def _warn_retired(file_values: dict[str, str | None]) -> None:
    """One notice per retired name that has a non-blank value in `.env` or the system."""
    for name, why in RETIRED.items():
        if name in _retired_noticed:
            continue
        in_file = (file_values.get(name) or "").strip()
        if in_file or os.environ.get(name, "").strip():
            _retired_noticed.add(name)
            where = ".env" if in_file else "the system environment"
            _notice(_RETIRED, f"{name} in {where} {why}")


def load_env(path: str | Path | None = None) -> list[str]:
    """Set every non-empty `.env` value over the inherited one; return the names it overrode.

    `path` defaults to the repo-root `.env`. A missing file, a variable absent from it and a
    blank value (`NAME=`) all leave the inherited value as it is. One warning per variable
    whose inherited value differed.
    """
    if path is None:
        path = root_env_file()
        if path is None:
            _notice(
                _NOT_READ,
                f"No pyproject.toml above {__file__}: .env not read, the environment stands",
            )
            _warn_retired({})
            return []
    path = Path(path)
    values = dotenv_values(path) if path.is_file() else {}
    _warn_retired(values)
    overridden: list[str] = []
    for name, value in values.items():
        if value is None or not value.strip() or name in RETIRED:
            continue
        inherited = os.environ.get(name)
        os.environ[name] = value
        if inherited is not None and inherited != value:
            overridden.append(name)
            if name in LOGGED_VALUES:
                _notice(
                    _OVERRIDE,
                    f"{name} from {path} ({value!r}) replaces the inherited value ({inherited!r})",
                )
            else:
                _notice(
                    _OVERRIDE,
                    f"{name} from {path} replaces the inherited value (values not logged)",
                )
    return overridden
