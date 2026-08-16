"""
FinnhubNewsFetcher
==================

A reusable class to fetch company news from Finnhub for one or more tickers
over a date range, with built-in rate limiting, error handling, and CSV export.

IMPORTANT LIMITATION:
Finnhub's FREE tier only serves company news for roughly the last 12 months.
Requests for dates further back typically return an empty list `[]`, not an
error. Test with a recent date range first to confirm your key/setup works.

Docs: https://finnhub.io/docs/api/company-news
"""

import time
import csv
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import finnhub


class FinnhubNewsFetcher:
    """Fetch and manage company news from the Finnhub API."""

    def __init__(self, api_key: str, sleep_between_calls: float = 1.1):
        """
        Parameters
        ----------
        api_key : str
            Your Finnhub API key.
        sleep_between_calls : float
            Seconds to wait between API calls to respect rate limits
            (free tier: 60 calls/minute).
        """
        self.client = finnhub.Client(api_key=api_key)
        self.sleep_between_calls = sleep_between_calls
        self.articles: List[Dict] = []  # accumulated results live here

    # ------------------------------------------------------------------
    # Core fetching
    # ------------------------------------------------------------------
    def fetch_ticker_news(self, ticker: str, from_date: str, to_date: str) -> List[Dict]:
        """
        Fetch news for a single ticker between from_date and to_date
        (format YYYY-MM-DD). Returns a list of raw article dicts from Finnhub.
        """
        try:
            return self.client.company_news(ticker, _from=from_date, to=to_date)
        except finnhub.exceptions.FinnhubAPIException as e: # type: ignore[union-attr]
            print(f"[ERROR] {ticker}: {e}")
            return []
        except Exception as e:
            print(f"[UNEXPECTED ERROR] {ticker}: {e}")
            return []

    def fetch_many(
        self,
        tickers: List[str],
        from_date: str,
        to_date: str,
        verbose: bool = True,
    ) -> List[Dict]:
        """
        Fetch news for multiple tickers over the same date range.
        Appends normalized rows to self.articles and also returns them.
        """
        new_rows = []

        for ticker in tickers:
            if verbose:
                print(f"Fetching news for {ticker} ({from_date} -> {to_date}) ...")

            raw_articles = self.fetch_ticker_news(ticker, from_date, to_date)

            if verbose:
                if not raw_articles:
                    print("  -> No articles returned (check date range / free-tier limit).")
                else:
                    print(f"  -> {len(raw_articles)} articles found.")

            new_rows.extend(self._normalize_article(ticker, a) for a in raw_articles)

            time.sleep(self.sleep_between_calls)

        self.articles.extend(new_rows)
        return new_rows

    def fetch_general_news(self, category: str = "general", min_id: int = 0) -> List[Dict]:
        """
        General market news (not ticker-specific), via Finnhub's /news
        endpoint.

        Parameters
        ----------
        category : str
            One of "general", "forex", "crypto", "merger".
        min_id : int
            Only return articles newer than this Finnhub article id
            (use 0 to get the latest batch).

        Returns a list of normalized rows (same shape as company news, but
        with ticker=None since these articles aren't tied to one company).
        Never raises; returns [] on error.
        """
        try:
            raw = self.client.general_news(category, min_id=min_id)
        except Exception as e:
            print(f"[ERROR] general_news({category}): {e}")
            return []
        return [self._normalize_article(None, a) for a in raw]

    def fetch_news_sentiment(self, ticker: str) -> Dict:
        """
        Finnhub's news-sentiment score for a ticker (buzz + bullish/bearish
        split). A cheap sentiment signal available today, ahead of a future
        dedicated NLP/FinBERT service.

        Returns {"success": bool, "data"|"error": ...}, matching
        EdgarAgent's convention -- this endpoint is premium-gated on some
        Finnhub plans, and callers need a clean way to detect that.
        """
        try:
            data = self.client.news_sentiment(ticker)
            if not data or not data.get("symbol"):
                return {
                    "success": False,
                    "error": f"No sentiment data for '{ticker}' (may require a paid Finnhub plan).",
                }
            return {"success": True, "data": data}
        except Exception as e:
            return {"success": False, "error": f"Failed to fetch sentiment for '{ticker}': {e}"}

    # ------------------------------------------------------------------
    # Rolling date-window fetching
    # ------------------------------------------------------------------
    def fetch_rolling_range(
        self,
        tickers: List[str],
        start_date: str,
        end_date: str,
        window_days: int = 7,
        verbose: bool = True,
        checkpoint_csv: Optional[str] = None,
    ) -> List[Dict]:
        """
        Fetch news for a long date range by automatically chunking it into
        smaller windows (e.g., 7-day chunks) and looping fetch_many() over
        each window. Useful for backfilling months/years without manually
        calling fetch_many() repeatedly.

        Parameters
        ----------
        tickers : List[str]
            Tickers to fetch news for.
        start_date, end_date : str
            Overall range, format YYYY-MM-DD (inclusive).
        window_days : int
            Size of each chunk in days. Finnhub doesn't require chunking,
            but smaller windows make it easier to track progress, resume
            after a failure, and avoid huge single responses.
        verbose : bool
            Print progress per window/ticker.
        checkpoint_csv : Optional[str]
            If provided, appends results to this CSV after every window,
            so partial progress is saved even if the run is interrupted
            (e.g., for long historical backfills).

        Returns
        -------
        List[Dict]
            All newly fetched, normalized article rows (also added to
            self.articles).
        """
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        if start > end:
            raise ValueError("start_date must be before end_date")

        all_new_rows = []
        window_start = start

        while window_start <= end:
            window_end = min(window_start + timedelta(days=window_days - 1), end)

            from_str = window_start.strftime("%Y-%m-%d")
            to_str = window_end.strftime("%Y-%m-%d")

            if verbose:
                print(f"\n=== Window: {from_str} -> {to_str} ===")

            window_rows = self.fetch_many(tickers, from_str, to_str, verbose=verbose)
            all_new_rows.extend(window_rows)

            # Save incremental progress if requested
            if checkpoint_csv and window_rows:
                self._append_to_csv(window_rows, checkpoint_csv)

            window_start = window_end + timedelta(days=1)

        return all_new_rows

    def _append_to_csv(self, rows: List[Dict], filepath: str) -> None:
        """Append rows to a CSV, writing the header only if the file is new."""
        import os

        fieldnames = ["ticker", "datetime", "headline", "summary", "source", "url", "category"]
        file_exists = os.path.isfile(filepath)

        with open(filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerows(rows)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_article(ticker: Optional[str], article: Dict) -> Dict:
        """Convert a raw Finnhub article dict into a flat, consistent row."""
        return {
            "ticker": ticker,
            "datetime": datetime.utcfromtimestamp(article.get("datetime", 0)).isoformat(),
            "headline": article.get("headline", ""),
            "summary": article.get("summary", ""),
            "source": article.get("source", ""),
            "url": article.get("url", ""),
            "category": article.get("category", ""),
        }

    def clear(self) -> None:
        """Reset accumulated articles."""
        self.articles = []

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def to_csv(self, filepath: str) -> None:
        """Save all accumulated articles to a CSV file."""
        if not self.articles:
            print("No articles to save. Run fetch_many() first.")
            return

        fieldnames = ["ticker", "datetime", "headline", "summary", "source", "url", "category"]
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.articles)

        print(f"Saved {len(self.articles)} rows to {filepath}")

    def to_list(self) -> List[Dict]:
        """Return accumulated articles as a list of dicts."""
        return self.articles


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    API_KEY = "YOUR_FINNHUB_API_KEY"  # <-- put your key here

    TICKERS = ["AAPL", "MSFT", "AMZN", "GOOGL", "TSLA"]

    fetcher = FinnhubNewsFetcher(api_key=API_KEY)

    # Option 1: single date range
    # fetcher.fetch_many(TICKERS, "2024-06-01", "2024-06-07")
    # fetcher.to_csv("sp500_news.csv")

    # Option 2: automatically chunk a long range into weekly windows,
    # with progress saved incrementally to a checkpoint CSV (safe to
    # interrupt and resume-ish, since each window is appended as it completes)
    fetcher.fetch_rolling_range(
        tickers=TICKERS,
        start_date="2024-01-01",
        end_date="2024-03-31",
        window_days=7,
        checkpoint_csv="sp500_news_checkpoint.csv",
    )

    # Final full export (same data, single clean file)
    fetcher.to_csv("sp500_news_full.csv")

    # NOTE: remember the free Finnhub tier generally only returns news for
    # roughly the last 12 months, regardless of how far back you request.