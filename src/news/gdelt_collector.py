"""
GDELTNewsFetcher
================

Fetch historical news articles (with basic tone/sentiment scores) from the
GDELT 2.0 Doc API. Unlike Finnhub's free tier (~12 months of history), GDELT
covers global news back to 2015 (and partial coverage further back), with no
API key required.

Docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
API endpoint: https://api.gdeltproject.org/api/v2/doc/doc

NOTES / CAVEATS
----------------
1. GDELT indexes articles via web crawls. It gives you the article URL,
   title, source domain, publish date, and a rough "tone" score - but NOT
   the full article body. For full text you generally need to fetch the
   URL yourself afterward (expect some dead links for older articles).
2. Query syntax matters: GDELT uses its own query language (supports
   keywords, "exact phrases", domain: filters, sourcelang:, etc). Simple
   keyword queries (e.g. company name) work fine to start.
3. Rate limits are informal but generous; still, avoid hammering it with
   no delay - a small sleep between calls is good practice.
4. maxrecords caps at 250 per request. For high article-volume periods,
   you'll want to chunk by smaller date windows (this class supports that,
   similar to the Finnhub fetcher).
"""

import csv, json, os, requests, random, time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from urllib.parse import quote
import pandas as pd

from src.config import general


class GDELTNewsFetcher:
    """Fetch and manage historical news articles from the GDELT 2.0 Doc API."""

    BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, sleep_between_calls: float = 6.0, timeout: int = 30):
        """
        Parameters
        ----------
        sleep_between_calls : float
            Base seconds to wait between API calls. GDELT's real-world
            tolerance seems closer to ~1 request per 5-6 seconds per IP,
            despite no official published limit. Random jitter is added
            on top of this (see fetch_many) to avoid a perfectly regular,
            bot-like request cadence.
        timeout : int
            Request timeout in seconds.
        """
        self.sleep_between_calls = sleep_between_calls
        self.timeout = timeout
        self.articles: List[Dict] = []

    # ------------------------------------------------------------------
    # Query building helpers
    # ------------------------------------------------------------------
    @staticmethod
    def append_language_filter(query: str, english_only: bool = True) -> str:
        """
        Append GDELT's sourcelang:english filter to an arbitrary query string.
        Useful if you're building queries manually rather than via
        build_or_query(), e.g.:

            q = fetcher.append_language_filter('Tesla earnings')
            # -> 'Tesla earnings sourcelang:english'
        """
        if english_only and "sourcelang:" not in query:
            return f"{query} sourcelang:english"
        return query

    @staticmethod
    def build_or_query(names: List[str], english_only: bool = True) -> str:
        """
        Combine multiple company names into a single GDELT OR query, e.g.
        ["Apple Inc", "Tesla"] -> '("Apple Inc" OR "Tesla") sourcelang:english'

        This lets you fetch multiple companies' news in ONE API call instead
        of one call per company - the biggest lever for avoiding 429s, since
        it directly cuts your total request count.

        Parameters
        ----------
        names : List[str]
            Company names to OR together.
        english_only : bool
            If True (default), appends GDELT's `sourcelang:english` filter
            so only English-language articles are returned.

        NOTE: with a combined query you lose the per-company "query" label
        on results (GDELT just returns whatever matched). If you need to
        know which article matched which company, you'll have to infer it
        from the title/text yourself, or keep calls separate but slower.
        """
        quoted = [f'"{name}"' for name in names]
        query = "(" + " OR ".join(quoted) + ")"
        if english_only:
            query += " sourcelang:english"
        return query

    # ------------------------------------------------------------------
    # Core fetching
    # ------------------------------------------------------------------
    def fetch_query(
        self,
        query: str,
        start_date: str,
        end_date: str,
        max_records: int = 10,
        max_retries: int = 4,
        initial_backoff: float = 8.0,
    ) -> List[Dict]:
        """
        Fetch articles matching a query within a date range.

        Parameters
        ----------
        query : str
            GDELT query string, e.g. '"Apple Inc" stock' or 'Tesla earnings'.
            You can use GDELT operators like domain:reuters.com, sourcelang:eng, etc.
        start_date, end_date : str
            Format YYYY-MM-DD (converted internally to GDELT's datetime format).
        max_records : int
            Max articles per request (GDELT caps this at 250).
        max_retries : int
            Number of retries on 429 (rate limit) or transient errors before
            giving up on this query/window.
        initial_backoff : float
            Seconds to wait before the first retry; doubles each subsequent
            retry (exponential backoff): 5s, 10s, 20s, 40s, 80s...

        Returns
        -------
        List[Dict]
            Raw article dicts as returned by GDELT.
        """
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").strftime("%Y%m%d000000")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").strftime("%Y%m%d235959")

        params = {
            "query": query,
            "mode": "artlist",
            "maxrecords": min(max_records, 250),
            "format": "json",
            "startdatetime": start_dt,
            "enddatetime": end_dt,
        }

        backoff = initial_backoff

        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.get(self.BASE_URL, params=params, timeout=self.timeout)

                if resp.status_code == 429:
                    # Respect the server's suggested wait time if it provides one
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait_time = float(retry_after)
                        except ValueError:
                            wait_time = backoff
                    else:
                        wait_time = backoff

                    # Add jitter (+/- up to 30%) so repeated calls don't fall
                    # into a perfectly regular, easily-flagged cadence
                    wait_time = wait_time * random.uniform(1.0, 1.3)

                    print(f"  [429] Rate limited on '{query}' "
                          f"(attempt {attempt}/{max_retries}). "
                          f"Waiting {wait_time:.1f}s before retry...")
                    time.sleep(wait_time)
                    backoff *= 2
                    continue

                resp.raise_for_status()

                # GDELT sometimes returns an empty body or an HTML error page
                # instead of JSON when overloaded - guard against that too.
                if not resp.text.strip():
                    print(f"  [WARN] Empty response body for '{query}' "
                          f"(attempt {attempt}/{max_retries}). Retrying in {backoff:.0f}s...")
                    time.sleep(backoff)
                    backoff *= 2
                    continue

                data = resp.json()
                return data.get("articles", [])

            except requests.exceptions.HTTPError as e:
                print(f"[ERROR] Query '{query}' ({start_date} -> {end_date}): {e}")
                return []
            except requests.exceptions.RequestException as e:
                print(f"[ERROR] Query '{query}' ({start_date} -> {end_date}): {e}")
                return []
            except json.JSONDecodeError:
                print(f"  [WARN] Non-JSON response for '{query}' "
                      f"(attempt {attempt}/{max_retries}). Retrying in {backoff:.0f}s...")
                time.sleep(backoff)
                backoff *= 2

        print(f"[FAILED] Gave up on '{query}' ({start_date} -> {end_date}) "
              f"after {max_retries} attempts.")
        return []

    def fetch_many(
        self,
        tickers_or_queries: List[str],
        start_date: str,
        end_date: str,
        farmed: list[str],
        verbose: bool = True,
        english_only: bool = True,
    ) -> tuple[List[Dict], pd.DataFrame]:
        """
        Fetch news for multiple tickers/company names over the same date range.
        Each entry in tickers_or_queries is used as-is as the GDELT query, so
        pass company names (e.g. "Apple Inc", "Tesla") rather than raw tickers
        for better recall - GDELT indexes news text, not ticker metadata.

        Parameters
        ----------
        tickers_or_queries : List[str]
            Company names or arbitrary GDELT queries to fetch.
        start_date : str
            Start date in YYYY-MM-DD format.
        end_date : str
            End date in YYYY-MM-DD format.
        farmed : list[str]
            List of already-farmed queries.
        verbose : bool
            If True (default), print progress messages.
        english_only : bool
            If True (default), automatically appends `sourcelang:english` to
            each query (unless it already contains a sourcelang: filter, e.g.
            from build_or_query()). Set to False to fetch all languages.
        
        Returns
        -------
        List[Dict]
            Raw article dicts as returned by GDELT.
        DataFrame
            A DataFrame containing the company names and status of each fetch.
        """
        new_rows, company_names, status = [], [], []

        for raw_query in tickers_or_queries:
            query = self.append_language_filter(raw_query, english_only=english_only)
            query_identifier = '_'.join(start_date.split('-')) + '_' + raw_query
            
            # Fetch articles for this query and date range
            if verbose:
                print(f"Fetching news for '{query}' ({start_date} -> {end_date}) ...")
            
            # Skip queries we've already fetched
            if query_identifier in farmed:
                if verbose:
                    print("  -> Skipping (already fetched).")
                continue

            # Fetch articles
            raw_articles = self.fetch_query(query, start_date, end_date) 
            if verbose:
                msg = (
                    f"  -> {len(raw_articles)} articles found." if raw_articles
                    else "  -> No articles returned."
                )
                print(msg)

            # Normalize and append
            new_rows.extend(self._normalize_article(raw_query, a) for a in raw_articles)
            company_names.append(query_identifier)
            status.append('Success' if raw_articles else 'Failed')

            time.sleep(self.sleep_between_calls * random.uniform(1.0, 1.3))

        # Update inventory
        self.articles.extend(new_rows)
        farmed_update = pd.DataFrame({"query": company_names, "status": status})
        return new_rows, farmed_update

    def inventory_control(self, checkpoint_csv: str, exclude_failed: bool = False) -> tuple[str, list[str]]:
        """
        Control the inventory of fetched articles by reading a list of already-farmed articles from a file.
        If the file does not exist, it initializes an empty list.
        
        Parameters
        ----------
        checkpoint_csv : str
            Path to the CSV file containing the list of already-farmed articles.
        Returns
        -------
        tuple[str, list[str]]
            The path to the CSV file and the list of already-farmed articles.
        """
        path = os.path.dirname(checkpoint_csv)
        file_name = os.path.basename(checkpoint_csv).split('.')[0]
        os.makedirs(path, exist_ok=True)
        farmed_path = os.path.join(path, f'{file_name}_farmed.csv')
        if os.path.exists(farmed_path):
            farmed = pd.read_csv(farmed_path)
            if exclude_failed:
                farmed = farmed[farmed.status == 'Success']['query'].tolist()
            else:
                farmed = farmed['query'].tolist()
        else:
            farmed = []
        return farmed_path, farmed
    # ------------------------------------------------------------------
    # Rolling date-window fetching (for long historical backfills)
    # ------------------------------------------------------------------
    def fetch_rolling_range(
        self,
        tickers_or_queries: List[str],
        start_date: str,
        end_date: str,
        window_days: int = 7,
        verbose: bool = True,
        checkpoint_csv: Optional[str] = None,
        english_only: bool = True,
        exclude_failed: bool = False,
    ) -> List[Dict]:
        """
        Chunk a long date range (e.g. multiple years) into smaller windows
        and fetch each one, optionally saving progress incrementally.

        english_only : bool
            Passed through to fetch_many() on each window - restricts
            results to English-language articles (sourcelang:english).
        """
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        
        if checkpoint_csv:
            farmed_path, farmed = self.inventory_control(checkpoint_csv, exclude_failed=exclude_failed)

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

            window_rows, farmed_update = self.fetch_many(
                tickers_or_queries, from_str, to_str, farmed,verbose=verbose, english_only=english_only
            )
            all_new_rows.extend(window_rows)

            if checkpoint_csv and window_rows:
                self._append_to_csv(window_rows, checkpoint_csv)
                if not os.path.exists(farmed_path):
                    farmed_update.to_csv(farmed_path, index=False)
                else:
                    farmed_update.to_csv(farmed_path, mode='a', header=False, index=False)

            window_start = window_end + timedelta(days=1)

        return all_new_rows

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_article(query: str, article: Dict) -> Dict:
        """Convert a raw GDELT article dict into a flat, consistent row."""
        return {
            "query": query,
            "seendate": article.get("seendate", ""),
            "title": article.get("title", ""),
            "url": article.get("url", ""),
            "domain": article.get("domain", ""),
            "language": article.get("language", ""),
            "sourcecountry": article.get("sourcecountry", ""),
            "tone": article.get("tone", ""),  # rough sentiment score if present
        }

    def _append_to_csv(self, rows: List[Dict], filepath: str) -> None:

        fieldnames = [
            "query", "seendate", "title", "url", "domain",
            "language", "sourcecountry", "tone"
        ]
        file_exists = os.path.isfile(filepath)

        with open(filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerows(rows)

    def clear(self) -> None:
        self.articles = []

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def to_csv(self, filepath: str) -> None:
        if not self.articles:
            print("No articles to save. Run fetch_many() or fetch_rolling_range() first.")
            return

        fieldnames = ["query", "seendate", "title", "url", "domain",
                      "language", "sourcecountry", "tone"]
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.articles)

        print(f"Saved {len(self.articles)} rows to {filepath}")

    def to_list(self) -> List[Dict]:
        return self.articles


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Use company names (not tickers) for better GDELT recall.
    COMPANIES = ["Apple Inc", "Microsoft", "Amazon", "Tesla"]

    fetcher = GDELTNewsFetcher(sleep_between_calls=6.0)

    # OPTION A (recommended - far fewer API calls, lower 429 risk):
    # combine all companies into a single OR query per date window.
    combined_query = fetcher.build_or_query(COMPANIES)
    fetcher.fetch_rolling_range(
        tickers_or_queries=[combined_query],
        start_date="2020-01-01",
        end_date="2020-03-31",
        window_days=7,
        checkpoint_csv="gdelt_news_checkpoint.csv",
    )

    # OPTION B (one call per company per window - more granular, but
    # multiplies your call count by len(COMPANIES); slower and more
    # likely to hit 429s. Uncomment if you need per-company query labels):
    #
    # fetcher.fetch_rolling_range(
    #     tickers_or_queries=COMPANIES,
    #     start_date="2020-01-01",
    #     end_date="2020-03-31",
    #     window_days=7,
    #     checkpoint_csv="gdelt_news_checkpoint.csv",
    # )

    fetcher.to_csv("gdelt_news_full.csv")