"""Shared discovery defaults, overridable via environment variables.

The CLI, the API request schemas, and the orchestrator's fallback all need the
same default date range — keep that logic in one place instead of duplicating
literal dates across `main.py`, `api/schemas.py`, and `orchestrator.py`.
"""

from __future__ import annotations

import os
from datetime import date

# Env var names
ENV_START_DATE = "DISCOVERY_START_DATE"
ENV_END_DATE = "DISCOVERY_END_DATE"

# Fallbacks when the env vars are unset
_FALLBACK_START_DATE = date(2022, 1, 1)


def default_start_date() -> date:
    """Inclusive discovery start date. Set via DISCOVERY_START_DATE (YYYY-MM-DD)."""
    raw = os.environ.get(ENV_START_DATE)
    return date.fromisoformat(raw) if raw else _FALLBACK_START_DATE


def default_end_date() -> date:
    """Inclusive discovery end date. Set via DISCOVERY_END_DATE (YYYY-MM-DD); defaults to today."""
    raw = os.environ.get(ENV_END_DATE)
    return date.fromisoformat(raw) if raw else date.today()
