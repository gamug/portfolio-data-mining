"""Tests for data models."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from news_collector.models import Company, DateRange, DiscoveryStats, PartialStats

# ---------------------------------------------------------------------------
# DateRange
# ---------------------------------------------------------------------------


def test_date_range_contains() -> None:
    dr = DateRange(start=date(2022, 1, 1), end=date(2024, 12, 31))
    assert dr.contains(date(2022, 1, 1))
    assert dr.contains(date(2024, 12, 31))
    assert dr.contains(date(2023, 6, 15))
    assert not dr.contains(date(2021, 12, 31))
    assert not dr.contains(date(2025, 1, 1))


def test_date_range_invalid() -> None:
    with pytest.raises(ValueError):
        DateRange(start=date(2024, 1, 1), end=date(2022, 1, 1))


@given(
    start=st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 12, 31)),
    delta=st.integers(min_value=0, max_value=3000),
)
@settings(max_examples=200)
def test_date_range_contains_consistency(start: date, delta: int) -> None:
    end = start + timedelta(days=delta)
    dr = DateRange(start=start, end=end)
    mid = start + timedelta(days=delta // 2)
    assert dr.contains(mid)
    before = start - timedelta(days=1)
    assert not dr.contains(before)


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------


def test_company_ticker_normalized() -> None:
    c = Company(ticker="aapl", name="Apple Inc.")
    assert c.ticker == "AAPL"


def test_company_aliases_default() -> None:
    c = Company(ticker="MSFT", name="Microsoft Corporation")
    assert "MSFT" in c.aliases
    assert "Microsoft Corporation" in c.aliases


# ---------------------------------------------------------------------------
# DiscoveryStats
# ---------------------------------------------------------------------------


def test_discovery_stats_merge() -> None:
    stats = DiscoveryStats()
    partial = PartialStats(
        company="Apple Inc.",
        domain="cnbc.com",
        ddg_count=10,
        sitemap_count=5,
        inserted_count=12,
        duplicate_count=3,
    )
    stats.merge(partial)
    assert stats.total_discovered == 15
    assert stats.total_inserted == 12
    assert stats.duplicate_count == 3
    assert stats.ddg_count == 10
    assert stats.sitemap_count == 5
    assert stats.by_domain["cnbc.com"] == 12
    assert stats.by_company["Apple Inc."] == 12


def test_discovery_stats_merge_multiple() -> None:
    stats = DiscoveryStats()
    for i in range(5):
        stats.merge(
            PartialStats(
                company=f"Company {i}",
                domain="cnbc.com",
                ddg_count=2,
                sitemap_count=3,
                inserted_count=4,
                duplicate_count=1,
            )
        )
    assert stats.total_inserted == 20
    assert stats.by_domain["cnbc.com"] == 20
    assert len(stats.by_company) == 5
