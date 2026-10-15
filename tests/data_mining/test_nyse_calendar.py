"""Tests for data_mining/nyse_calendar.py -- the NYSE calendar ported from
gamug/portfolio-financial-analysis's `src/kg_schema/trading_calendar.py`.

Hermetic: pure date arithmetic, no network. The headline test is A3's: the
ported calendar must reproduce report §2's "expected NYSE sessions" count for
all 35 requested leaver lines (counts only -- committable, no prices).
"""

from __future__ import annotations

from datetime import date

from data_mining import nyse_calendar

# Report §2 (Task B feasibility report): the 35 request lines' last membership
# day (the request doc's "Covers" end) and the expected NYSE session count over
# 2019-01-02 .. valid_to, inclusive. Counts and dates only.
REPORT_EXPECTED_SESSIONS: dict[str, tuple[str, int]] = {
    "XLNX": ("2022-02-14", 787),
    "INFO": ("2022-03-01", 797),
    "PBCT": ("2022-04-03", 820),
    "DISCK": ("2022-04-10", 825),
    "CERN": ("2022-06-07", 865),
    "CTXS": ("2022-10-02", 945),
    "DRE": ("2022-10-02", 945),
    "NLSN": ("2022-10-11", 952),
    "TWTR": ("2022-10-31", 966),
    "ABMD": ("2022-12-21", 1002),
    "SBNY": ("2023-03-14", 1057),
    "SIVB": ("2023-03-14", 1057),
    "FRC": ("2023-05-03", 1092),
    "DISH": ("2023-06-19", 1123),
    "ATVI": ("2023-10-17", 1207),
    "SEE": ("2023-12-17", 1249),
    "PXD": ("2024-05-07", 1346),
    "CMA": ("2024-06-23", 1377),
    "WRK": ("2024-07-07", 1386),
    "MRO": ("2024-11-25", 1486),
    "CTLT": ("2024-12-22", 1504),
    "QRVO": ("2024-12-22", 1504),
    "DFS": ("2025-05-18", 1603),
    "JNPR": ("2025-07-08", 1637),
    "ANSS": ("2025-07-17", 1644),
    "HES": ("2025-07-22", 1647),
    "WBA": ("2025-08-27", 1673),
    "IPG": ("2025-11-27", 1737),
    "K": ("2025-12-10", 1746),
    "DAY": ("2026-02-08", 1785),
    "HOLX": ("2026-04-08", 1826),
    "CTRA": ("2026-05-06", 1846),
    "EA": ("2026-08-04", 1907),
    "AVB": ("2026-08-17", 1916),
    "WBD": ("2026-10-05", 1950),
}


def test_calendar_reproduces_report_expected_sessions_for_all_35_lines() -> None:
    for ask, (valid_to, expected) in REPORT_EXPECTED_SESSIONS.items():
        got = nyse_calendar.sessions(date(2019, 1, 2), date.fromisoformat(valid_to))
        assert len(got) == expected, f"{ask}: {len(got)} != {expected}"


def test_weekends_are_not_sessions() -> None:
    assert not nyse_calendar.is_trading_day(date(2024, 6, 1))  # Saturday
    assert not nyse_calendar.is_trading_day(date(2024, 6, 2))  # Sunday
    assert nyse_calendar.is_trading_day(date(2024, 6, 3))  # Monday


def test_regular_holidays() -> None:
    assert not nyse_calendar.is_trading_day(date(2024, 1, 1))  # New Year's Day
    assert not nyse_calendar.is_trading_day(date(2024, 3, 29))  # Good Friday
    assert not nyse_calendar.is_trading_day(date(2024, 5, 27))  # Memorial Day
    assert not nyse_calendar.is_trading_day(date(2024, 7, 4))  # Independence Day
    assert not nyse_calendar.is_trading_day(date(2024, 11, 28))  # Thanksgiving
    assert not nyse_calendar.is_trading_day(date(2024, 12, 25))  # Christmas


def test_observed_holidays() -> None:
    # 2021-07-04 is a Sunday -> observed Monday 2021-07-05.
    assert not nyse_calendar.is_trading_day(date(2021, 7, 5))
    assert nyse_calendar.is_trading_day(date(2021, 7, 6))
    # 2021-12-25 is a Saturday -> observed Friday 2021-12-24.
    assert not nyse_calendar.is_trading_day(date(2021, 12, 24))


def test_juneteenth_from_2022() -> None:
    # 2021-06-18 (Friday) is a session; Juneteenth starts in 2022.
    assert nyse_calendar.is_trading_day(date(2021, 6, 18))
    assert not nyse_calendar.is_trading_day(date(2022, 6, 20))  # observed Monday


def test_special_closures() -> None:
    assert not nyse_calendar.is_trading_day(date(2018, 12, 5))  # President G. H. W. Bush
    assert not nyse_calendar.is_trading_day(date(2025, 1, 9))  # President Carter
    assert not nyse_calendar.is_trading_day(date(2012, 10, 29))  # Hurricane Sandy


def test_next_and_add_trading_days() -> None:
    # Friday -> Monday.
    assert nyse_calendar.next_trading_day(date(2024, 6, 7)) == date(2024, 6, 10)
    # Across a holiday.
    assert nyse_calendar.next_trading_day(date(2024, 12, 24)) == date(2024, 12, 26)
    assert nyse_calendar.add_trading_days(date(2024, 12, 24), 1) == date(2024, 12, 26)
    assert nyse_calendar.add_trading_days(date(2024, 12, 24), 0) == date(2024, 12, 24)


def test_sessions_are_inclusive_and_ordered() -> None:
    got = nyse_calendar.sessions(date(2024, 6, 3), date(2024, 6, 7))
    assert got == [
        date(2024, 6, 3),
        date(2024, 6, 4),
        date(2024, 6, 5),
        date(2024, 6, 6),
        date(2024, 6, 7),
    ]
