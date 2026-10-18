"""Shared discovery defaults: the start date from the environment, the end date from the clock.

The CLI, the API request schemas, and the orchestrator's fallback all need the
same default date range — keep that logic in one place instead of duplicating
literal dates across `main.py`, `api/schemas.py`, and `orchestrator.py`.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

from news_collector.models import DateRange

# Env var name
ENV_START_DATE = "DISCOVERY_START_DATE"

# Fallback when the env var is unset
_FALLBACK_START_DATE = date(2022, 1, 1)


def default_start_date() -> date:
    """Inclusive discovery start date. Set via DISCOVERY_START_DATE (YYYY-MM-DD)."""
    raw = os.environ.get(ENV_START_DATE)
    return date.fromisoformat(raw) if raw else _FALLBACK_START_DATE


def today_utc() -> date:
    """The default end date: today in UTC, taken when the command or request starts (T-077).

    The end date is an input (`--end-date`, `end_date`), not an environment variable: it is
    never read from `DISCOVERY_END_DATE`. A fixed end date is passed on each run, so a batch
    that spans midnight UTC keeps its `discovery_progress` range by passing the same value.
    """
    return datetime.now(UTC).date()


class InvalidRangeError(ValueError):
    """The two dates of a run are in the wrong order: the caller's input, not the server's."""


def build_date_range(start: date | None, end: date | None) -> DateRange:
    """The range of one run: `start` or the default start, `end` or today (UTC).

    Raises InvalidRangeError when start > end (the API answers 422, the CLI exits 1). A future
    end date is allowed. A malformed `DISCOVERY_START_DATE` is the server's configuration, not
    the caller's input: its plain ValueError is not an InvalidRangeError and passes through.
    """
    start = start or default_start_date()
    end = end or today_utc()
    try:
        return DateRange(start=start, end=end)
    except ValueError as exc:
        raise InvalidRangeError(str(exc)) from exc
