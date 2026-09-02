"""Tests for MarketDataClient (src/pricing/market_data.py) -- Finnhub
company profile / peers / basic-financials lookups. No test hits the
network: finnhub.Client is replaced with a MagicMock after construction.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pricing.market_data import MarketDataClient


@pytest.fixture
def market_data() -> MarketDataClient:
    md = MarketDataClient(finnhub_api_key="test-key")
    md.client = MagicMock()
    return md


# ---------------------------------------------------------------------
# get_company_profile
# ---------------------------------------------------------------------


def test_get_company_profile_success(market_data: MarketDataClient) -> None:
    market_data.client.company_profile2.return_value = {"name": "Apple Inc.", "ticker": "AAPL"}

    result = market_data.get_company_profile("AAPL")

    market_data.client.company_profile2.assert_called_once_with(symbol="AAPL")
    assert result == {"success": True, "data": {"name": "Apple Inc.", "ticker": "AAPL"}}


def test_get_company_profile_empty_response_is_an_error(market_data: MarketDataClient) -> None:
    market_data.client.company_profile2.return_value = {}

    result = market_data.get_company_profile("BADTICKER")

    assert result["success"] is False
    assert "BADTICKER" in result["error"]


def test_get_company_profile_exception_returns_error_dict(market_data: MarketDataClient) -> None:
    market_data.client.company_profile2.side_effect = Exception("boom")

    result = market_data.get_company_profile("AAPL")

    assert result == {"success": False, "error": "Failed to fetch profile for 'AAPL': boom"}


# ---------------------------------------------------------------------
# get_company_peers
# ---------------------------------------------------------------------


def test_get_company_peers_success(market_data: MarketDataClient) -> None:
    market_data.client.company_peers.return_value = ["MSFT", "GOOGL"]

    result = market_data.get_company_peers("AAPL")

    assert result == {"success": True, "data": ["MSFT", "GOOGL"]}


def test_get_company_peers_none_response_becomes_empty_list(market_data: MarketDataClient) -> None:
    market_data.client.company_peers.return_value = None

    result = market_data.get_company_peers("AAPL")

    assert result == {"success": True, "data": []}


def test_get_company_peers_exception_returns_error_dict(market_data: MarketDataClient) -> None:
    market_data.client.company_peers.side_effect = Exception("boom")

    result = market_data.get_company_peers("AAPL")

    assert result == {"success": False, "error": "Failed to fetch peers for 'AAPL': boom"}


# ---------------------------------------------------------------------
# get_basic_financials
# ---------------------------------------------------------------------


def test_get_basic_financials_success(market_data: MarketDataClient) -> None:
    market_data.client.company_basic_financials.return_value = {"metric": {"peRatio": 30.1}}

    result = market_data.get_basic_financials("AAPL")

    market_data.client.company_basic_financials.assert_called_once_with("AAPL", "all")
    assert result == {"success": True, "data": {"metric": {"peRatio": 30.1}}}


def test_get_basic_financials_passes_custom_metric(market_data: MarketDataClient) -> None:
    market_data.client.company_basic_financials.return_value = {"metric": {}}

    market_data.get_basic_financials("AAPL", metric="valuation")

    market_data.client.company_basic_financials.assert_called_once_with("AAPL", "valuation")


def test_get_basic_financials_missing_metric_key_is_an_error(market_data: MarketDataClient) -> None:
    market_data.client.company_basic_financials.return_value = {}

    result = market_data.get_basic_financials("AAPL")

    assert result["success"] is False
    assert "paid Finnhub plan" in result["error"]


def test_get_basic_financials_exception_returns_error_dict(market_data: MarketDataClient) -> None:
    market_data.client.company_basic_financials.side_effect = Exception("boom")

    result = market_data.get_basic_financials("AAPL")

    assert result["success"] is False
    assert "boom" in result["error"]
