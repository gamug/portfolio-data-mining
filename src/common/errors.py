"""
src/common/errors.py

Shared exception type for upstream data-provider failures (Finnhub, yfinance,
Edgar). Most of the wrapper classes in this codebase already return
{"success": False, "error": ...} dicts instead of raising (see EdgarAgent,
MarketDataClient, FinnhubNewsFetcher.fetch_news_sentiment) — endpoints check
`success` and set the HTTP status accordingly. UpstreamDataError plus the
FastAPI exception handler registered in app.py is the safety net for
anything that raises unexpectedly instead (network errors, malformed input,
etc.), so a route never leaks a raw traceback to the client.
"""


class UpstreamDataError(Exception):
    """Raised when an upstream provider fails in an expected way (premium
    gated, rate limited, bad ticker) and the caller wants a clean,
    explanatory JSON error instead of a generic 500."""

    def __init__(self, message: str, status_code: int = 502, provider: str = "unknown"):
        self.message = message
        self.status_code = status_code
        self.provider = provider
        super().__init__(message)
