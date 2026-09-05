"""Shared exception type for upstream data-provider failures (Finnhub,
yfinance, SEC EDGAR).

Most consumer wrappers already return ``{"success": False, "error": ...}``
dicts instead of raising; ``UpstreamDataError``, paired with a FastAPI
exception handler on the consuming service, is the safety net for anything
that raises unexpectedly instead (network errors, malformed input), so a
route never leaks a raw traceback to the client.
"""


class UpstreamDataError(Exception):
    """Raised when an upstream provider fails in an expected way (premium
    gated, rate limited, bad ticker) and the caller wants a clean,
    explanatory JSON error instead of a generic 500."""

    def __init__(self, message: str, status_code: int = 502, provider: str = "unknown") -> None:
        self.message = message
        self.status_code = status_code
        self.provider = provider
        super().__init__(message)
