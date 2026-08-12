"""Tests for the Wikipedia-sourced S&P 500 constituent list."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from news_collector.models import Company
from news_collector.sp500 import _parse_constituents, fetch_sp500_companies

_VALID_TABLE_HTML = """
<html><body>
<table id="constituents">
  <tbody>
    <tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th></tr>
    <tr><td>MMM</td><td>3M</td><td>Industrials</td><td>Industrial Conglomerates</td></tr>
    <tr><td>AOS</td><td>A. O. Smith</td><td>Industrials</td><td>Building Products</td></tr>
    <tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td><td>Multi-Sector Holdings</td></tr>
  </tbody>
</table>
</body></html>
"""


def test_parse_constituents_happy_path() -> None:
    companies = _parse_constituents(_VALID_TABLE_HTML)
    assert companies == [
        Company(ticker="MMM", name="3M", sector="Industrials"),
        Company(ticker="AOS", name="A. O. Smith", sector="Industrials"),
        Company(ticker="BRK.B", name="Berkshire Hathaway", sector="Financials"),
    ]


def test_parse_constituents_uppercases_ticker() -> None:
    html_lower_ticker = _VALID_TABLE_HTML.replace(">MMM<", ">mmm<")
    companies = _parse_constituents(html_lower_ticker)
    assert companies[0].ticker == "MMM"


def test_parse_constituents_missing_table_raises() -> None:
    with pytest.raises(ValueError, match="constituents"):
        _parse_constituents("<html><body><p>no table here</p></body></html>")


def test_parse_constituents_missing_columns_raises() -> None:
    bad_html = """
    <table id="constituents">
      <tbody>
        <tr><th>Ticker</th><th>Company</th></tr>
        <tr><td>MMM</td><td>3M</td></tr>
      </tbody>
    </table>
    """
    with pytest.raises(ValueError, match="Symbol"):
        _parse_constituents(bad_html)


def test_parse_constituents_no_data_rows_raises() -> None:
    empty_html = """
    <table id="constituents">
      <tbody>
        <tr><th>Symbol</th><th>Security</th></tr>
      </tbody>
    </table>
    """
    with pytest.raises(ValueError, match="Parsed 0 companies"):
        _parse_constituents(empty_html)


def test_parse_constituents_skips_short_rows() -> None:
    """A malformed row with fewer cells than expected is skipped, not crashed on."""
    html_with_short_row = """
    <table id="constituents">
      <tbody>
        <tr><th>Symbol</th><th>Security</th><th>GICS Sector</th></tr>
        <tr><td>MMM</td><td>3M</td><td>Industrials</td></tr>
        <tr><td>ONLYONE</td></tr>
      </tbody>
    </table>
    """
    companies = _parse_constituents(html_with_short_row)
    assert companies == [Company(ticker="MMM", name="3M", sector="Industrials")]


async def test_fetch_sp500_companies_uses_fetch_text(monkeypatch) -> None:
    async def fake_fetch_text(client, url, **kwargs):
        assert "List_of_S" in url
        return _VALID_TABLE_HTML

    monkeypatch.setattr("news_collector.sp500.fetch_text", fake_fetch_text)

    companies = await fetch_sp500_companies(client=AsyncMock())
    assert len(companies) == 3
    assert companies[0].ticker == "MMM"
