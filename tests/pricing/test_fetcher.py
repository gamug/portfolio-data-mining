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


# ---------------------------------------------------------------------
# get_corporate_actions -- dividends + splits from yfinance
# ---------------------------------------------------------------------


def _actions_series(rows: dict[str, float]) -> pd.Series:
    """A yfinance-shaped actions series: exchange-tz-aware midnight index -> value."""
    index = pd.DatetimeIndex(list(rows), tz="America/New_York")
    return pd.Series(list(rows.values()), index=index, dtype=float)


def _yf_ticker(dividends: pd.Series | None = None, splits: pd.Series | None = None) -> MagicMock:
    ticker = MagicMock()
    ticker.dividends = dividends if dividends is not None else _actions_series({})
    ticker.splits = splits if splits is not None else _actions_series({})
    return ticker


def test_get_corporate_actions_returns_dividends_and_splits_in_range(
    fetcher: StockPriceFetcher,
) -> None:
    ticker = _yf_ticker(
        dividends=_actions_series({"2024-02-09": 0.24, "2024-05-10": 0.25}),
        splits=_actions_series({"2024-06-10": 4.0}),
    )

    with patch("pricing.fetcher.yf.Ticker", return_value=ticker) as mock_ticker:
        result = fetcher.get_corporate_actions("AAPL", "2024-01-01", "2024-12-31")

    mock_ticker.assert_called_once_with("AAPL")
    assert result == {
        "ticker": "AAPL",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "source": "yfinance",
        "dividends": [
            {"date": "2024-02-09", "value": 0.24},
            {"date": "2024-05-10", "value": 0.25},
        ],
        "splits": [{"date": "2024-06-10", "value": 4.0}],
        "warning": None,
    }


def test_get_corporate_actions_excludes_rows_outside_the_range(
    fetcher: StockPriceFetcher,
) -> None:
    ticker = _yf_ticker(
        dividends=_actions_series(
            {"2023-12-31": 0.10, "2024-01-01": 0.20, "2024-03-31": 0.30, "2024-04-01": 0.40}
        ),
    )

    with patch("pricing.fetcher.yf.Ticker", return_value=ticker):
        result = fetcher.get_corporate_actions("AAPL", "2024-01-01", "2024-03-31")

    # Both bounds are inclusive; the day before and the day after are out.
    assert [row["date"] for row in result["dividends"]] == ["2024-01-01", "2024-03-31"]


def test_get_corporate_actions_empty_range_is_empty_lists_without_warning(
    fetcher: StockPriceFetcher,
) -> None:
    with patch("pricing.fetcher.yf.Ticker", return_value=_yf_ticker()):
        result = fetcher.get_corporate_actions("AAPL", "2024-01-01", "2024-01-31")

    assert result["dividends"] == []
    assert result["splits"] == []
    assert result["warning"] is None


def test_get_corporate_actions_probe_range_before_any_history_is_empty(
    fetcher: StockPriceFetcher,
) -> None:
    # The exact range portfolio-financial-analysis's QuantPricingClient.probe() sends:
    # it must read as "endpoint exists, nothing to report", not an error.
    ticker = _yf_ticker(dividends=_actions_series({"2024-02-09": 0.24}))

    with patch("pricing.fetcher.yf.Ticker", return_value=ticker):
        result = fetcher.get_corporate_actions("XOM", "1900-01-01", "1900-01-02")

    assert result["dividends"] == []
    assert result["splits"] == []
    assert result["warning"] is None


def test_get_corporate_actions_uses_the_exchange_local_calendar_date(
    fetcher: StockPriceFetcher,
) -> None:
    # 20:00 in New York on Feb 9 is already 01:00 UTC on Feb 10. The ex-date is the
    # exchange-local day, so converting to UTC first would report the wrong date.
    evening = pd.Series([0.24], index=pd.DatetimeIndex(["2024-02-09 20:00"], tz="America/New_York"))
    ticker = _yf_ticker(dividends=evening)

    with patch("pricing.fetcher.yf.Ticker", return_value=ticker):
        result = fetcher.get_corporate_actions("AAPL", "2024-02-09", "2024-02-09")

    assert result["dividends"] == [{"date": "2024-02-09", "value": 0.24}]


def test_get_corporate_actions_yfinance_raises_returns_warning_not_exception(
    fetcher: StockPriceFetcher,
) -> None:
    with patch("pricing.fetcher.yf.Ticker", side_effect=Exception("network down")):
        result = fetcher.get_corporate_actions("AAPL", "2024-01-01", "2024-12-31")

    assert result["source"] == "yfinance"
    assert result["dividends"] == []
    assert result["splits"] == []
    assert "network down" in result["warning"]


def test_get_corporate_actions_failure_on_second_series_drops_the_first(
    fetcher: StockPriceFetcher,
) -> None:
    # If dividends were read but splits blew up, don't return a half answer that
    # looks complete: both lists empty, warning set.
    ticker = MagicMock()
    ticker.dividends = _actions_series({"2024-02-09": 0.24})
    type(ticker).splits = property(lambda self: (_ for _ in ()).throw(RuntimeError("bad splits")))

    with patch("pricing.fetcher.yf.Ticker", return_value=ticker):
        result = fetcher.get_corporate_actions("AAPL", "2024-01-01", "2024-12-31")

    assert result["dividends"] == []
    assert result["splits"] == []
    assert "bad splits" in result["warning"]


def test_get_corporate_actions_malformed_date_returns_warning_not_exception(
    fetcher: StockPriceFetcher,
) -> None:
    with patch("pricing.fetcher.yf.Ticker", return_value=_yf_ticker()) as mock_ticker:
        result = fetcher.get_corporate_actions("AAPL", "not-a-date", "2024-12-31")

    mock_ticker.assert_not_called()
    assert result["dividends"] == []
    assert result["warning"] is not None
