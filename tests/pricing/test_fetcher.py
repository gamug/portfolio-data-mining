"""Tests for StockPriceFetcher (src/pricing/fetcher.py) -- daily OHLCV
candles, Finnhub-first with a transparent yfinance fallback.

No test hits the network: finnhub.Client and yfinance.download are mocked.
sleep_between_calls=0 is used throughout so tests don't actually sleep.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from pricing.fetcher import StockPriceFetcher


@pytest.fixture
def fetcher() -> StockPriceFetcher:
    f = StockPriceFetcher(finnhub_api_key="test-key", sleep_between_calls=0)
    f.client = MagicMock()
    return f


def _finnhub_ok_response() -> dict:
    return {
        "s": "ok",
        "t": [1704067200, 1704153600],  # 2024-01-01, 2024-01-02 (UTC)
        "o": [100.0, 101.0],
        "h": [105.0, 106.0],
        "l": [99.0, 100.0],
        "c": [104.0, 105.0],
        "v": [1000, 1200],
    }


# ---------------------------------------------------------------------
# get_daily_candles -- finnhub success path
# ---------------------------------------------------------------------


def test_get_daily_candles_uses_finnhub_when_available(fetcher: StockPriceFetcher) -> None:
    fetcher.client.stock_candles.return_value = _finnhub_ok_response()

    result = fetcher.get_daily_candles("AAPL", "2024-01-01", "2024-01-02")

    assert result["source"] == "finnhub"
    assert result["warning"] is None
    assert result["ticker"] == "AAPL"
    assert len(result["candles"]) == 2
    assert result["candles"][0] == {
        "date": "2024-01-01",
        "open": 100.0,
        "high": 105.0,
        "low": 99.0,
        "close": 104.0,
        "volume": 1000,
        "source": "finnhub",
    }


def test_get_daily_candles_passes_correct_epoch_range(fetcher: StockPriceFetcher) -> None:
    fetcher.client.stock_candles.return_value = _finnhub_ok_response()

    fetcher.get_daily_candles("AAPL", "2024-01-01", "2024-01-02")

    args, _ = fetcher.client.stock_candles.call_args
    ticker, resolution, _from, _to = args
    assert ticker == "AAPL"
    assert resolution == "D"
    assert _to - _from == 86399 + 86400  # end_date + 86399, minus start_date epoch


# ---------------------------------------------------------------------
# get_daily_candles -- fallback to yfinance
# ---------------------------------------------------------------------


def test_get_daily_candles_falls_back_when_finnhub_reports_no_data(
    fetcher: StockPriceFetcher,
) -> None:
    fetcher.client.stock_candles.return_value = {"s": "no_data"}
    yf_df = pd.DataFrame(
        {"Open": [10.0], "High": [11.0], "Low": [9.0], "Close": [10.5], "Volume": [500]},
        index=pd.to_datetime(["2024-01-01"]),
    )

    with patch("pricing.fetcher.yf.download", return_value=yf_df) as mock_download:
        result = fetcher.get_daily_candles("XYZ", "2024-01-01", "2024-01-01")

    mock_download.assert_called_once()
    assert result["source"] == "yfinance"
    assert "premium-gated" in result["warning"]
    assert result["candles"] == [
        {
            "date": "2024-01-01",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 500,
            "source": "yfinance",
        }
    ]


def test_get_daily_candles_falls_back_when_finnhub_raises(fetcher: StockPriceFetcher) -> None:
    fetcher.client.stock_candles.side_effect = Exception("boom")
    yf_df = pd.DataFrame(
        {"Open": [10.0], "High": [11.0], "Low": [9.0], "Close": [10.5], "Volume": [500]},
        index=pd.to_datetime(["2024-01-01"]),
    )

    with patch("pricing.fetcher.yf.download", return_value=yf_df):
        result = fetcher.get_daily_candles("XYZ", "2024-01-01", "2024-01-01")

    assert result["source"] == "yfinance"
    assert len(result["candles"]) == 1


def test_get_daily_candles_both_sources_fail(fetcher: StockPriceFetcher) -> None:
    fetcher.client.stock_candles.return_value = {"s": "no_data"}

    with patch("pricing.fetcher.yf.download", return_value=pd.DataFrame()):
        result = fetcher.get_daily_candles("XYZ", "2024-01-01", "2024-01-01")

    assert result["source"] == "yfinance"
    assert result["candles"] == []
    assert "Both Finnhub and yfinance failed" in result["warning"]


def test_get_daily_candles_yfinance_raises(fetcher: StockPriceFetcher) -> None:
    fetcher.client.stock_candles.return_value = {"s": "no_data"}

    with patch("pricing.fetcher.yf.download", side_effect=Exception("network down")):
        result = fetcher.get_daily_candles("XYZ", "2024-01-01", "2024-01-01")

    assert result["candles"] == []
    assert "network down" in result["warning"]


def test_get_daily_candles_flattens_multiindex_columns(fetcher: StockPriceFetcher) -> None:
    fetcher.client.stock_candles.return_value = {"s": "no_data"}
    yf_df = pd.DataFrame(
        {"Open": [10.0], "High": [11.0], "Low": [9.0], "Close": [10.5], "Volume": [500]},
        index=pd.to_datetime(["2024-01-01"]),
    )
    yf_df.columns = pd.MultiIndex.from_product([yf_df.columns, ["XYZ"]])

    with patch("pricing.fetcher.yf.download", return_value=yf_df):
        result = fetcher.get_daily_candles("XYZ", "2024-01-01", "2024-01-01")

    assert result["candles"][0]["open"] == 10.0


def test_get_daily_candles_missing_t_key_triggers_fallback(fetcher: StockPriceFetcher) -> None:
    fetcher.client.stock_candles.return_value = {"s": "ok"}  # no "t"

    with patch("pricing.fetcher.yf.download", return_value=pd.DataFrame()):
        result = fetcher.get_daily_candles("XYZ", "2024-01-01", "2024-01-01")

    assert result["source"] == "yfinance"
