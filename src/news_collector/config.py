"""Shared discovery defaults: the start date from the environment, the end date from the clock.

The CLI, the API request schemas, and the orchestrator's fallback all need the
same default date range — keep that logic in one place instead of duplicating
literal dates across `main.py`, `api/schemas.py`, and `orchestrator.py`.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import UTC, date, datetime, timedelta

from news_collector.models import DateRange

# Env var name
ENV_START_DATE = "DISCOVERY_START_DATE"

# Fallback when the env var is unset
_FALLBACK_START_DATE = date(2022, 1, 1)

# Minutes a process waits after a failed sitemap refresh before it crawls again (T-070a);
# 0 turns the back-off off. A forced refresh ignores it.
ENV_SITEMAP_RETRY_MINUTES = "SITEMAP_RETRY_MINUTES"
_FALLBACK_SITEMAP_RETRY_MINUTES = 5.0
_MAX_SITEMAP_RETRY_MINUTES = 30 * 24 * 60  # a month: more is a typo, not a policy

log = logging.getLogger(__name__)


def default_start_date() -> date:
    """Inclusive discovery start date. Set via DISCOVERY_START_DATE (YYYY-MM-DD)."""
    raw = os.environ.get(ENV_START_DATE)
    return date.fromisoformat(raw) if raw else _FALLBACK_START_DATE


def sitemap_retry_after() -> timedelta:
    """The back-off after a failed sitemap refresh. Set via SITEMAP_RETRY_MINUTES (>= 0).

    A value that is not a finite number between 0 and a month is ignored, with a warning: a typo in `.env` must
    not turn the back-off off or stop the run.
    """
    raw = (os.environ.get(ENV_SITEMAP_RETRY_MINUTES) or "").strip()
    if not raw:
        return timedelta(minutes=_FALLBACK_SITEMAP_RETRY_MINUTES)
    try:
        minutes = float(raw)
    except ValueError:
        minutes = -1.0
    if not (math.isfinite(minutes) and 0 <= minutes <= _MAX_SITEMAP_RETRY_MINUTES):
        log.warning(
            "%s=%r is not a number of minutes between 0 and a month: using %s",
            ENV_SITEMAP_RETRY_MINUTES,
            raw,
            _FALLBACK_SITEMAP_RETRY_MINUTES,
        )
        minutes = _FALLBACK_SITEMAP_RETRY_MINUTES
    return timedelta(minutes=minutes)


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
