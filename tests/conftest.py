"""Shared test setup.

No test may read the real repo-root `.env` (T-066b): `load_env()` would override the variables
a test sets. The replacement is made at import time too, because entry-point modules under
`apps/` and `cli/` call `load_env()` when a test module imports them, during collection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data_mining import env

_MISSING_ENV_FILE = Path("/nonexistent-for-tests/.env")
env.root_env_file = lambda: _MISSING_ENV_FILE


@pytest.fixture(autouse=True)
def _no_real_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env, "root_env_file", lambda: _MISSING_ENV_FILE)
    monkeypatch.setattr(env, "_notices", [])  # what one test's load_env() logged is its own
    monkeypatch.setattr(env, "_retired_noticed", set())
    for name in env.RETIRED:  # a machine that still exports one must not change a test
        monkeypatch.delenv(name, raising=False)
