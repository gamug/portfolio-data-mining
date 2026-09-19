"""Tests for the corporate-actions route in apps/pricing_api.py.

No network: the route's StockPriceFetcher method is patched, so these cover the
HTTP contract (status codes, ticker casing, pass-through) and not yfinance itself
(that is tests/pricing/test_fetcher.py).
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # apps/pricing_api.py reads FINNHUB_API_KEY at import and fails fast without it.
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    pricing_api = importlib.import_module("apps.pricing_api")

    yield TestClient(pricing_api.app)


def _actions_result(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ticker": "XOM",
        "start_date": "2022-01-01",
        "end_date": "2026-08-27",
        "source": "yfinance",
        "dividends": [{"date": "2024-02-14", "value": 0.95}],
        "splits": [],
        "warning": None,
    }
    result.update(overrides)
    return result


def test_actions_route_returns_the_fetchers_result(client: TestClient) -> None:
    with patch(
        "apps.pricing_api.price_fetcher.get_corporate_actions", return_value=_actions_result()
    ) as mock_actions:
        response = client.get(
            "/pricing/xom/actions", params={"start_date": "2022-01-01", "end_date": "2026-08-27"}
        )

    assert response.status_code == 200
    assert response.json() == _actions_result()
    # The ticker is upper-cased and dates passed as ISO strings, like /pricing/{ticker}.
    mock_actions.assert_called_once_with("XOM", "2022-01-01", "2026-08-27")


def test_actions_route_empty_range_is_200_with_empty_lists_never_404(
    client: TestClient,
) -> None:
    # The request portfolio-financial-analysis's QuantPricingClient.probe() sends.
    empty = _actions_result(start_date="1900-01-01", end_date="1900-01-02", dividends=[], splits=[])
    with patch("apps.pricing_api.price_fetcher.get_corporate_actions", return_value=empty):
        response = client.get(
            "/pricing/XOM/actions", params={"start_date": "1900-01-01", "end_date": "1900-01-02"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["dividends"] == []
    assert body["splits"] == []
    assert body["warning"] is None


def test_actions_route_upstream_failure_is_200_with_a_warning(client: TestClient) -> None:
    failed = _actions_result(dividends=[], warning="yfinance failed to return corporate actions: x")
    with patch("apps.pricing_api.price_fetcher.get_corporate_actions", return_value=failed):
        response = client.get(
            "/pricing/XOM/actions", params={"start_date": "2024-01-01", "end_date": "2024-12-31"}
        )

    assert response.status_code == 200
    assert "yfinance failed" in response.json()["warning"]


def test_actions_route_rejects_a_reversed_range_with_400(client: TestClient) -> None:
    with patch("apps.pricing_api.price_fetcher.get_corporate_actions") as mock_actions:
        response = client.get(
            "/pricing/XOM/actions", params={"start_date": "2024-12-31", "end_date": "2024-01-01"}
        )

    assert response.status_code == 400
    mock_actions.assert_not_called()


def test_actions_route_rejects_a_malformed_date_with_422(client: TestClient) -> None:
    response = client.get(
        "/pricing/XOM/actions", params={"start_date": "yesterday", "end_date": "2024-01-01"}
    )

    assert response.status_code == 422


def test_actions_route_does_not_shadow_the_candles_route(client: TestClient) -> None:
    candles = {
        "ticker": "XOM",
        "start_date": "2024-01-01",
        "end_date": "2024-01-02",
        "source": "finnhub",
        "candles": [],
        "warning": None,
    }
    with patch("apps.pricing_api.price_fetcher.get_daily_candles", return_value=candles):
        response = client.get(
            "/pricing/XOM", params={"start_date": "2024-01-01", "end_date": "2024-01-02"}
        )

    assert response.status_code == 200
    assert response.json()["source"] == "finnhub"
