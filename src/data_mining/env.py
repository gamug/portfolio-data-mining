"""The repo-root `.env`, loaded so that it wins over the inherited environment (T-066b).

`load_dotenv()` leaves an inherited variable alone, so a machine-wide `DATABASE_URL` (this
machine's `/etc/environment` has one) hid the project's own. `load_env()` reverses that: a
non-empty value in `.env` replaces the inherited one, and the inherited value is the fallback
when the file, the variable or its value is missing (constitution rule 7).

Every entry point (`apps/*.py`, `cli/*.py`, `news_collector/main.py`) calls it before any
project import that reads the environment; `tests/data_mining/test_env_entrypoints.py` checks.
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
        "DISCOVERY_END_DATE",
        "ALLOW_NEW_DB",
    }
)


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


def load_env(path: str | Path | None = None) -> list[str]:
    """Set every non-empty `.env` value over the inherited one; return the names it overrode.

    `path` defaults to the repo-root `.env`. A missing file, a variable absent from it and a
    blank value (`NAME=`) all leave the inherited value as it is. One warning per variable
    whose inherited value differed.
    """
    if path is None:
        path = root_env_file()
        if path is None:
            log.warning(
                "No pyproject.toml above %s: .env not read, the environment stands", __file__
            )
            return []
    path = Path(path)
    if not path.is_file():
        return []
    overridden: list[str] = []
    for name, value in dotenv_values(path).items():
        if value is None or not value.strip():
            continue
        inherited = os.environ.get(name)
        os.environ[name] = value
        if inherited is not None and inherited != value:
            overridden.append(name)
            if name in LOGGED_VALUES:
                log.warning(
                    "%s from %s (%r) replaces the inherited value (%r)",
                    name,
                    path,
                    value,
                    inherited,
                )
            else:
                log.warning(
                    "%s from %s replaces the inherited value (values not logged)", name, path
                )
    return overridden
