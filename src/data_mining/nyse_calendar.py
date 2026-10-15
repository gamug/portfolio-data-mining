"""data_mining/nyse_calendar.py

The NYSE trading calendar, used by the leaver-prices fetch
(`data_mining.leaver_prices`) to count a line's *expected* sessions over its
membership range and to refuse a date that falls off the calendar.

Ported verbatim (calendar functions only) from
`gamug/portfolio-financial-analysis`'s
`src/kg_schema/trading_calendar.py`, commit
`87dd9bfdaf3ef1dd79604fa5446413b7521675fe` (2026-10-13) -- the same calendar
the Task B feasibility report's "expected sessions" counts were computed
with, so the fetch reproduces those counts exactly. That file's own
provenance note still applies here: the calendar is rule-based (weekends,
the NYSE's regular holidays with their observance rules) plus the
unscheduled closures in :data:`SPECIAL_CLOSURES`, and was checked against
every session of the stored daily-price spine and the NYSE's published
holiday lists.

Only the pure-calendar functions are copied; the source file's
filings/pricing-spine helpers (`available_from`, `session_final_at`,
`last_final_session`, ...) are specific to that repo and are not needed here.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import cache

# Unscheduled full-day closures since 2000 (national days of mourning, weather, 9/11).
SPECIAL_CLOSURES: frozenset[date] = frozenset(
    {
        date(2001, 9, 11),
        date(2001, 9, 12),
        date(2001, 9, 13),
        date(2001, 9, 14),
        date(2004, 6, 11),  # President Reagan
        date(2007, 1, 2),  # President Ford
        date(2012, 10, 29),  # Hurricane Sandy
        date(2012, 10, 30),
        date(2018, 12, 5),  # President G. H. W. Bush
        date(2025, 1, 9),  # President Carter
    }
)


_SATURDAY, _SUNDAY = 5, 6
_JUNETEENTH_FIRST_YEAR = 2022


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * ell) // 433
    month = (h + ell - 7 * m + 90) // 25
    return date(year, month, (h + ell - 7 * m + 33 * month + 19) % 32)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The *n*-th *weekday* (Mon = 0) of the month; ``n = -1`` is the last."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    """A Saturday holiday is observed on the Friday before, a Sunday one on the Monday after."""
    if day.weekday() == _SATURDAY:
        return day - timedelta(days=1)
    if day.weekday() == _SUNDAY:
        return day + timedelta(days=1)
    return day


@cache
def nyse_holidays(year: int) -> frozenset[date]:
    """The NYSE's full-day closures in *year* (weekends excluded)."""
    days = {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter(year) - timedelta(days=2),  # Good Friday
        _nth_weekday(year, 5, 0, -1),  # Memorial Day
        _observed(date(year, 7, 4)),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),  # Christmas
    }
    # New Year's Day: a Saturday one is not moved back into December (NYSE Rule 7.2).
    new_year = date(year, 1, 1)
    if new_year.weekday() != _SATURDAY:
        days.add(_observed(new_year))
    if year >= _JUNETEENTH_FIRST_YEAR:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth
    days |= {d for d in SPECIAL_CLOSURES if d.year == year}
    return frozenset(days)


def is_trading_day(day: date) -> bool:
    """True when *day* is a regular NYSE session (a weekday that is not a holiday)."""
    return day.weekday() < _SATURDAY and day not in nyse_holidays(day.year)


def next_trading_day(day: date) -> date:
    """The first NYSE trading day strictly after *day*."""
    nxt = day + timedelta(days=1)
    while not is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def add_trading_days(day: date, n: int) -> date:
    """The NYSE trading day *n* sessions after *day* (``n >= 0``; *day* itself need not be one).
    ``add_trading_days(d, 0)`` is *d*; ``add_trading_days(d, 1)`` is :func:`next_trading_day`."""
    if n < 0:
        raise ValueError("n must be >= 0")
    out = day
    for _ in range(n):
        out = next_trading_day(out)
    return out


def sessions(start: date, end: date) -> list[date]:
    """Every NYSE session in ``[start, end]``, inclusive and oldest-first."""
    out: list[date] = []
    day = start
    while day <= end:
        if is_trading_day(day):
            out.append(day)
        day = date.fromordinal(day.toordinal() + 1)
    return out


__all__ = [
    "SPECIAL_CLOSURES",
    "add_trading_days",
    "is_trading_day",
    "next_trading_day",
    "nyse_holidays",
    "sessions",
]
