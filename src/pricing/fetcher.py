"""
src/pricing/fetcher.py

StockPriceFetcher: daily OHLCV candles for a ticker over a date range.

Tries Finnhub's `stock_candles` first. Finnhub's free tier no longer returns
historical daily candle data for stocks (paid-plan-only since 2023) — a
free key typically gets back {"s": "no_data"} rather than an error. On any
failure (no data, exception, malformed response), this transparently falls
back to `yfinance` (no API key required) for the same ticker/date range.

Both sources are normalized to one common row shape so callers never need to
know which source actually served a given request — the response always
reports which one did, plus a human-readable "warning" when a fallback
occurred, so the caller can surface that to the user if useful.
"""

import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import finnhub
import yfinance as yf

DailyCandle = Dict[str, object]
# {"date": "YYYY-MM-DD", "open": float, "high": float, "low": float,
#  "close": float, "volume": int, "source": "finnhub" | "yfinance"}


class StockPriceFetcher:
    """Fetch daily OHLCV price history for a ticker, Finnhub-first with a
    transparent yfinance fallback for free-tier accounts."""

    def __init__(self, finnhub_api_key: str, sleep_between_calls: float = 1.1):
        self.client = finnhub.Client(api_key=finnhub_api_key)
        self.sleep_between_calls = sleep_between_calls

    def get_daily_candles(self, ticker: str, start_date: str, end_date: str) -> Dict:
        """
        Fetch daily candles for `ticker` between `start_date` and `end_date`
        (inclusive, format YYYY-MM-DD).

        Never raises for expected failure modes (bad ticker, premium-gated
        endpoint, empty range) — always returns the shape below, with
        candles=[] and a "warning" explaining why, mirroring EdgarAgent's
        never-raise convention.

        Returns
        -------
        dict:
            {
              "ticker": str, "start_date": str, "end_date": str,
              "source": "finnhub" | "yfinance",
              "candles": List[DailyCandle],
              "warning": Optional[str],
            }
        """
        finnhub_result = self._try_finnhub(ticker, start_date, end_date)
        if finnhub_result is not None:
            return {
                "ticker": ticker,
                "start_date": start_date,
                "end_date": end_date,
                "source": "finnhub",
                "candles": finnhub_result,
                "warning": None,
            }

        yfinance_result, yf_error = self._try_yfinance(ticker, start_date, end_date)
        warning = (
            "Finnhub stock_candles unavailable (likely premium-gated on the free "
            "tier) — served from yfinance instead."
            if yfinance_result else
            f"Both Finnhub and yfinance failed to return data: {yf_error}"
        )
        return {
            "ticker": ticker,
            "start_date": start_date,
            "end_date": end_date,
            "source": "yfinance",
            "candles": yfinance_result,
            "warning": warning,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _try_finnhub(self, ticker: str, start_date: str, end_date: str) -> Optional[List[DailyCandle]]:
        try:
            _from = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
            _to = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp()) + 86399
            resp = self.client.stock_candles(ticker, "D", _from, _to)
            time.sleep(self.sleep_between_calls)

            if not resp or resp.get("s") != "ok" or not resp.get("t"):
                return None  # "no_data" / premium-gated -> trigger fallback

            return [
                {
                    "date": datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                    "open": o, "high": h, "low": l, "close": c, "volume": v,
                    "source": "finnhub",
                }
                for t, o, h, l, c, v in zip(
                    resp["t"], resp["o"], resp["h"], resp["l"], resp["c"], resp["v"]
                )
            ]
        except Exception as e:
            print(f"[ERROR] Finnhub stock_candles({ticker}): {e}")
            return None  # any exception -> fall back, don't raise

    def _try_yfinance(self, ticker: str, start_date: str, end_date: str) -> Tuple[List[DailyCandle], Optional[str]]:
        try:
            end_inclusive = (
                datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            ).strftime("%Y-%m-%d")  # yfinance's 'end' is exclusive

            df = yf.download(
                ticker, start=start_date, end=end_inclusive,
                progress=False, auto_adjust=False,
            )
            if df is None or df.empty:
                return [], "yfinance returned no rows for this ticker/date range"

            # Newer yfinance versions can return MultiIndex columns (ticker,
            # field) even for a single symbol — flatten to plain field names.
            if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)

            rows: List[DailyCandle] = [
                {
                    "date": idx.strftime("%Y-%m-%d"),
                    "open": float(r["Open"]),
                    "high": float(r["High"]),
                    "low": float(r["Low"]),
                    "close": float(r["Close"]),
                    "volume": int(r["Volume"]),
                    "source": "yfinance",
                }
                for idx, r in df.iterrows()
            ]
            return rows, None
        except Exception as e:
            print(f"[ERROR] yfinance download({ticker}): {e}")
            return [], str(e)
