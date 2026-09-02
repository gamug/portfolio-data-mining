"""Tests for src/common/portfolio.py -- the live S&P 500 universe scrape and
its as_of delegation to common.universe_history. No test hits the network:
Wikipedia fetches go through an injected httpx.Client wired to an
httpx.MockTransport, matching the convention in tests/extractor/test_reference.py.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import httpx
import pandas as pd
import pytest

from common import portfolio

CONSTITUENTS_HTML = """
<html><body>
<table id="constituents" class="wikitable sortable">
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th>
    <th>Headquarters Location</th><th>Date added</th><th>CIK</th><th>Founded</th></tr>
<tr><td>MMM</td><td>3M</td><td>Industrials</td><td>Industrial Conglomerates</td>
    <td>Saint Paul, Minnesota</td><td>1957-03-04</td><td>0000066740 |</td><td>1902</td></tr>
<tr><td>aapl</td><td>Apple Inc.</td><td>Information Technology</td><td>Technology Hardware</td>
    <td>Cupertino, California</td><td>1982-11-30</td><td>0000320193</td><td>1976</td></tr>
</table>
</body></html>
"""

NO_TABLE_HTML = "<html><body><p>Page structure changed, no table here.</p></body></html>"
ZERO_ROWS_HTML = """
<html><body>
<table id="constituents"><tr><th>Symbol</th><th>Security</th></tr></table>
</body></html>
"""


def test_parse_constituents_table_maps_all_eight_columns() -> None:
    df = portfolio._parse_constituents_table(CONSTITUENTS_HTML)

    assert list(df.columns) == portfolio.COLUMNS
    assert len(df) == 2
    row = df.iloc[0]
    assert row["Symbol"] == "MMM"
    assert row["GICS Sub-Industry"] == "Industrial Conglomerates"
    assert row["Founded"] == "1902"


def test_parse_constituents_table_uppercases_symbol() -> None:
    df = portfolio._parse_constituents_table(CONSTITUENTS_HTML)

    assert df.iloc[1]["Symbol"] == "AAPL"


def test_parse_constituents_table_strips_pipe_artifact() -> None:
    df = portfolio._parse_constituents_table(CONSTITUENTS_HTML)

    assert df.iloc[0]["CIK"] == "0000066740"


def test_parse_constituents_table_missing_table_raises() -> None:
    with pytest.raises(ValueError, match="constituents table not found"):
        portfolio._parse_constituents_table(NO_TABLE_HTML)


def test_parse_constituents_table_zero_rows_raises() -> None:
    with pytest.raises(ValueError, match="Parsed 0 companies"):
        portfolio._parse_constituents_table(ZERO_ROWS_HTML)


def test_fetch_universe_from_wikipedia_uses_injected_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == portfolio.WIKIPEDIA_SP500_URL
        return httpx.Response(200, text=CONSTITUENTS_HTML)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    df = portfolio._fetch_universe_from_wikipedia(client=client)

    assert len(df) == 2
    client.close()


def test_fetch_universe_from_wikipedia_raises_for_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        portfolio._fetch_universe_from_wikipedia(client=client)
    client.close()


# ---------------------------------------------------------------------
# list_universe / resolve_symbol -- as_of=None path (unchanged behavior)
# ---------------------------------------------------------------------


def _fake_universe_df() -> pd.DataFrame:
    return pd.DataFrame.from_records(
        [
            {
                "Symbol": "MMM",
                "Security": "3M",
                "GICS Sector": "Industrials",
                "GICS Sub-Industry": "Industrial Conglomerates",
                "Headquarters Location": "Saint Paul, Minnesota",
                "Date added": "1957-03-04",
                "CIK": "66740",
                "Founded": "1902",
            },
            {
                "Symbol": "AAPL",
                "Security": "Apple Inc.",
                "GICS Sector": "Information Technology",
                "GICS Sub-Industry": "Technology Hardware",
                "Headquarters Location": "Cupertino, California",
                "Date added": "1982-11-30",
                "CIK": "320193",
                "Founded": "1976",
            },
        ],
        columns=portfolio.COLUMNS,
    )


def test_list_universe_filters_by_sector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portfolio, "load_universe", _fake_universe_df)

    rows = portfolio.list_universe(sector="industrials")

    assert [r["Symbol"] for r in rows] == ["MMM"]


def test_resolve_symbol_exact_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portfolio, "load_universe", _fake_universe_df)

    row = portfolio.resolve_symbol("aapl")

    assert row is not None
    assert row["Security"] == "Apple Inc."


def test_resolve_symbol_partial_name_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portfolio, "load_universe", _fake_universe_df)

    row = portfolio.resolve_symbol("apple")

    assert row is not None
    assert row["Symbol"] == "AAPL"


def test_resolve_symbol_no_match_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portfolio, "load_universe", _fake_universe_df)

    assert portfolio.resolve_symbol("NOPE") is None


def test_list_universe_default_never_touches_universe_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The as_of=None path must stay exactly as it was -- no DB, no import
    of common.universe_history at all."""
    monkeypatch.setattr(portfolio, "load_universe", _fake_universe_df)
    poison = MagicMock(side_effect=AssertionError("must not be called"))
    monkeypatch.setattr("common.universe_history.query_as_of", poison)

    portfolio.list_universe()

    poison.assert_not_called()


# ---------------------------------------------------------------------
# list_universe / resolve_symbol -- as_of=<date> delegates to universe_history
# ---------------------------------------------------------------------


def test_list_universe_with_as_of_delegates_to_universe_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = [{"Symbol": "XYZ"}]
    mock_query = MagicMock(return_value=sentinel)
    monkeypatch.setattr("common.universe_history.query_as_of", mock_query)

    as_of = date(2019, 1, 1)
    result = portfolio.list_universe(sector="Industrials", as_of=as_of)

    assert result == sentinel
    mock_query.assert_called_once_with(as_of, sector="Industrials")


def test_resolve_symbol_with_as_of_delegates_to_universe_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = {"Symbol": "XYZ"}
    mock_resolve = MagicMock(return_value=sentinel)
    monkeypatch.setattr("common.universe_history.resolve_as_of", mock_resolve)

    as_of = date(2019, 1, 1)
    result = portfolio.resolve_symbol("xyz", as_of=as_of)

    assert result == sentinel
    mock_resolve.assert_called_once_with("xyz", as_of)
