"""
src/pricing/market_data.py

Lightweight Finnhub company/market-data lookups: profile, peers, and basic
financial metrics. This is a distinct concern from src/pricing/fetcher.py
(daily OHLCV collection) — it's read-through company/sector metadata rather
than a price time series, so it lives in its own module within the pricing
package.

Every method returns {"success": bool, "data"|"error": ...}, matching
EdgarAgent's convention: never raise, always give the caller something
inspectable, since several of these endpoints are premium-gated on Finnhub's
free tier and that's an expected, not exceptional, outcome.
"""

from typing import Dict

import finnhub


class MarketDataClient:
    """Read-only Finnhub company/market-data lookups."""

    def __init__(self, finnhub_api_key: str):
        self.client = finnhub.Client(api_key=finnhub_api_key)

    def get_company_profile(self, ticker: str) -> Dict:
        """
        Company profile: country, currency, exchange, ipo date, market cap,
        name, shares outstanding, ticker, web url, logo, industry.
        """
        try:
            data = self.client.company_profile2(symbol=ticker)
            if not data:
                return {"success": False, "error": f"No profile found for '{ticker}'."}
            return {"success": True, "data": data}
        except Exception as e:
            return {"success": False, "error": f"Failed to fetch profile for '{ticker}': {e}"}

    def get_company_peers(self, ticker: str) -> Dict:
        """Sector/industry comparable tickers, e.g. ["AAPL", "MSFT", ...]."""
        try:
            peers = self.client.company_peers(ticker)
            return {"success": True, "data": peers or []}
        except Exception as e:
            return {"success": False, "error": f"Failed to fetch peers for '{ticker}': {e}"}

    def get_basic_financials(self, ticker: str, metric: str = "all") -> Dict:
        """Basic financial metrics (valuation ratios, margins, growth, etc.)."""
        try:
            data = self.client.company_basic_financials(ticker, metric)
            if not data or not data.get("metric"):
                return {
                    "success": False,
                    "error": f"No basic financials for '{ticker}' (may require a paid Finnhub plan).",
                }
            return {"success": True, "data": data}
        except Exception as e:
            return {"success": False, "error": f"Failed to fetch basic financials for '{ticker}': {e}"}
