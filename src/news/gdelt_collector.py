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

CHANGELOG (perf / reliability fixes)
-------------------------------------
- max_records default raised from 10 -> 250 (the GDELT cap). This is the
  single biggest lever for reducing total call count: with the old default
  of 10, covering any real volume of news required many more, smaller date
  windows, which multiplied your total request count (and therefore your
  429 exposure) many times over.
- Fixed an O(n^2) bug in fetch_many()'s checkpoint logic: `company_names`/
  `status` were accumulated across the *entire* query loop but re-written
  in full to the farmed-queries CSV (in append mode) on every single
  checkpoint, duplicating earlier rows each time and writing ever-larger
  chunks to disk as the loop progressed. Now only the newly-completed
  query's row is appended, once.
- Added a shared requests.Session for connection pooling/reuse instead of
  opening a fresh TCP/TLS connection on every call.
- Added a simple adaptive throttle: sleep_between_calls now increases
  automatically after a 429 and slowly decays back down after sustained
  success, instead of staying fixed regardless of how the server is
  actually behaving.
- Raised the default base sleep_between_calls to better match GDELT's
  real-world tolerance (per the original docstring's own note, ~5-6s/req),
  which in practice causes far fewer 429s than starting from ~2s and
  relying on backoff to recover.
"""

import csv, json, os, requests, random, time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pandas as pd


class GDELTNewsFetcher:
    """Fetch and manage historical news articles from the GDELT 2.0 Doc API."""

    BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(
        self,
        sleep_between_calls: float = 5.5,
        timeout: int = 60,
        max_sleep_between_calls: float = 60.0,
        circuit_breaker_threshold: int = 3,
        circuit_breaker_cooldown: float = 180.0,
        user_agent: str = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    ):
        """
        Parameters
        ----------
        sleep_between_calls : float
            Base seconds to wait between API calls. GDELT's real-world
            tolerance seems closer to ~1 request per 5-6 seconds per IP,
            despite no official published limit, so that's the default now
            (previously 2s, which triggered frequent 429s on longer runs).
            Random jitter is added on top of this (see fetch_many) to avoid
            a perfectly regular, bot-like request cadence. This value is
            also adjusted automatically at runtime - see the adaptive
            throttle notes on _register_success / _register_rate_limit.
        timeout : int
            Request timeout in seconds.
        max_sleep_between_calls : float
            Ceiling for the adaptive throttle, so a bad streak of 429s
            can't grow the delay unboundedly.
        circuit_breaker_threshold : int
            Number of consecutive 429s (across calls, not just within a
            single fetch_query's retry loop) after which we assume we've
            tripped a sustained rate-limit block rather than just being
            briefly over the limit, and pause for circuit_breaker_cooldown
            seconds instead of continuing to retry. GDELT's sustained
            blocks don't clear from a slightly longer backoff - they need
            several minutes of the IP being quiet. Hammering through one
            with ever-shorter retries can keep resetting it.
        circuit_breaker_cooldown : float
            Seconds to pause once circuit_breaker_threshold consecutive
            429s have been seen.
        user_agent : str
            GDELT has been observed to rate-limit/block requests that use
            requests' default User-Agent string, even at low request
            volume, while identical requests with a browser-like
            User-Agent succeed. Set to None/"" to disable and use the
            requests default.
        """
        self.sleep_between_calls = sleep_between_calls
        self._base_sleep_between_calls = sleep_between_calls
        self.max_sleep_between_calls = max_sleep_between_calls
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_cooldown = circuit_breaker_cooldown
        self._consecutive_429s = 0
        self.timeout = timeout
        self.articles: List[Dict] = []
        self.session = requests.Session()
        if user_agent:
            self.session.headers.update({"User-Agent": user_agent})

    # ------------------------------------------------------------------
    # Adaptive throttling
    # ------------------------------------------------------------------
    def _register_rate_limit(self) -> bool:
        """Back off the baseline pacing after hitting a 429, and track
        consecutive 429s to detect a sustained block. Returns True if the
        circuit breaker should trip (caller should do a long cooldown
        instead of a normal short retry)."""
        self.sleep_between_calls = min(
            self.sleep_between_calls * 1.5, self.max_sleep_between_calls
        )
        self._consecutive_429s += 1
        return self._consecutive_429s >= self.circuit_breaker_threshold

    def _register_success(self) -> None:
        """Slowly relax pacing back toward the configured baseline after
        successful calls, so a single bad patch doesn't permanently slow
        down the rest of a long run."""
        self._consecutive_429s = 0
        if self.sleep_between_calls > self._base_sleep_between_calls:
            self.sleep_between_calls = max(
                self._base_sleep_between_calls, self.sleep_between_calls * 0.9
            )

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
        it directly cuts your total request count. Prefer this over passing
        many separate names to fetch_many/fetch_rolling_range whenever you
        don't strictly need per-company query labels.

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

    @staticmethod
    def build_domain_filter(domains: List[str], exact: bool = False) -> str:
        """
        Restrict a query to specific news portals/domains, e.g.
        ["reuters.com", "bloomberg.com"] -> "(domain:reuters.com OR domain:bloomberg.com)"

        Parameters
        ----------
        domains : List[str]
            Domains to restrict results to, e.g. "reuters.com", "cnn.com".
        exact : bool
            If False (default), uses GDELT's `domain:` operator, which
            matches the domain AND its subdomains (e.g. "cnn.com" also
            matches "edition.cnn.com"). If True, uses `domainis:`, which
            requires an exact domain match only.

        Combine this with build_or_query()/append_language_filter() by
        joining the strings with a space (GDELT ANDs space-separated
        terms together), e.g.:

            company_q = fetcher.build_or_query(["Tesla"], english_only=False)
            domain_q = fetcher.build_domain_filter(["reuters.com", "bloomberg.com"])
            query = fetcher.append_language_filter(f"{company_q} {domain_q}")
            # -> '("Tesla") (domain:reuters.com OR domain:bloomberg.com) sourcelang:english'
        """
        op = "domainis" if exact else "domain"
        return "(" + " OR ".join(f"{op}:{d}" for d in domains) + ")"

    # ------------------------------------------------------------------
    # Core fetching
    # ------------------------------------------------------------------
    def fetch_query(
        self,
        query: str,
        start_date: str,
        end_date: str,
        max_records: int = 250,
        max_retries: int = 4,
        initial_backoff: float = 5,
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
            Max articles per request. Defaults to 250, GDELT's hard cap -
            there's no reason to ask for fewer, since doing so just forces
            you to make more calls (via smaller windows) to get the same
            coverage.
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
                resp = self.session.get(self.BASE_URL, params=params, timeout=self.timeout)

                if resp.status_code == 429:
                    if should_trip_breaker := self._register_rate_limit():
                        # We've seen several 429s in a row across calls -
                        # this looks like a sustained IP-level block rather
                        # than a momentary "over the limit" response.
                        # GDELT's sustained blocks reportedly don't clear
                        # from progressively longer backoff alone; they
                        # need a real quiet period. Pause for a fixed,
                        # fairly long cooldown instead of continuing to
                        # retry with the normal (shorter) exponential
                        # backoff, then reset the counter and try again.
                        print(f"  [CIRCUIT BREAKER] {self._consecutive_429s} "
                              f"consecutive 429s - this looks like a "
                              f"sustained rate-limit block, not just "
                              f"momentary throttling. Pausing "
                              f"{self.circuit_breaker_cooldown:.0f}s before "
                              f"trying again...")
                        time.sleep(self.circuit_breaker_cooldown)
                        self._consecutive_429s = 0
                        continue

                    # Respect the server's suggested wait time if it provides one
                    if retry_after := resp.headers.get("Retry-After"):
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
                          f"Waiting {wait_time:.1f}s before retry... "
                          f"(baseline pacing now {self.sleep_between_calls:.1f}s)")
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
                self._register_success()
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
        checkpoint_csv: Optional[str] = None,
        farmed: List[str] = [],
        farmed_path: str = '',
        verbose: bool = True,
        english_only: bool = True,
    ) -> List[Dict]:
        """
        Fetch news for multiple tickers/company names over the same date range.
        Each entry in tickers_or_queries is used as-is as the GDELT query, so
        pass company names (e.g. "Apple Inc", "Tesla") rather than raw tickers
        for better recall - GDELT indexes news text, not ticker metadata.

        Tip: if you don't need per-company labels on the results, combine
        names with build_or_query() and pass a single-element list here -
        that turns N calls into 1 and is the biggest lever for avoiding 429s.

        Parameters
        ----------
        tickers_or_queries : List[str]
            Company names or arbitrary GDELT queries to fetch.
        start_date : str
            Start date in YYYY-MM-DD format.
        end_date : str
            End date in YYYY-MM-DD format.
        checkpoint_csv : Optional[str]
            Path to the CSV file to save the fetched news.
        farmed : List[str]
            List of already-farmed queries.
        farmed_path : str
            Path to the CSV file containing farmed queries.
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
        """
        new_rows = []

        for raw_query in tickers_or_queries:
            query = self.append_language_filter(raw_query, english_only=english_only)
            query_identifier = '_'.join(start_date.split('-')) + '_' + raw_query

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
            articles = [self._normalize_article(raw_query, a) for a in raw_articles]
            new_rows.extend(articles)

            this_status = 'Success' if raw_articles else 'Failed'

            # Update inventory and save to CSV if checkpointing is enabled.
            # NOTE: previously this rebuilt the *entire accumulated* list of
            # queries processed so far in this call and re-appended all of
            # it to farmed_path on every iteration, duplicating earlier rows
            # each time (O(n^2) writes + duplicate farmed entries). Now we
            # only ever append the single row for the query that just
            # finished.
            if checkpoint_csv and articles:
                self._append_to_csv(articles, checkpoint_csv)
                farmed_row = pd.DataFrame(
                    {"query": [query_identifier], "status": [this_status]}
                )
                if not os.path.exists(farmed_path):
                    farmed_row.to_csv(farmed_path, index=False)
                else:
                    farmed_row.to_csv(farmed_path, mode='a', header=False, index=False)

            # Keep the in-memory farmed list in sync too, so a query isn't
            # re-fetched later in the same run (e.g. if it appears again
            # for some reason) before the on-disk file is re-read.
            farmed.append(query_identifier)

            time.sleep(self.sleep_between_calls * random.uniform(1.0, 1.3))

        # Update inventory
        self.articles.extend(new_rows)
        return new_rows

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
            # De-duplicate here too, in case an old, buggy checkpoint file
            # (written before this fix) still has repeated rows.
            farmed = farmed.drop_duplicates(subset=["query"], keep="last")
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
        else:
            farmed_path, farmed = '', []

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

            window_rows = self.fetch_many(
                tickers_or_queries, from_str, to_str,
                checkpoint_csv=checkpoint_csv,
                farmed_path=farmed_path, farmed=farmed,
                verbose=verbose, english_only=english_only
            )
            all_new_rows.extend(window_rows)

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
    fetcher = GDELTNewsFetcher()

    # Combine all companies into a single OR query per date window - this is
    # the single biggest lever for cutting total API calls (and 429s).
    # Combined with window_days=7 and max_records=250 (now the default),
    # a multi-year backfill needs dramatically fewer requests than before.
    #
    # english_only=False here because we build the sourcelang:english
    # filter ourselves at the end, once, after adding the domain filter -
    # otherwise it would get appended twice.
    # Use company names (not tickers) for better GDELT recall.
    company_query = fetcher.build_or_query(["Apple Inc", "Microsoft", "Amazon", "Tesla"], english_only=False)

    if portals := ["reuters.com", "bloomberg.com", "cnbc.com"]:
        combined_query = fetcher.append_language_filter(
            f"{company_query} {fetcher.build_domain_filter(portals)}"
        )
    else:
        combined_query = fetcher.append_language_filter(company_query)

    # combined_query now looks like:
    # '("Apple Inc" OR "Microsoft" OR "Amazon" OR "Tesla") (domain:reuters.com OR domain:bloomberg.com OR domain:cnbc.com) sourcelang:english'

    fetcher.fetch_rolling_range(
        tickers_or_queries=[combined_query],
        start_date="2020-01-01",
        end_date="2020-03-31",
        window_days=7,
        checkpoint_csv="gdelt_news_checkpoint.csv",
    )

    fetcher.to_csv("gdelt_news_full.csv")