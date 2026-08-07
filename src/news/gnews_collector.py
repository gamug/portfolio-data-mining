"""
Fetch news for a list of S&P500 companies over a time window, restricted to
a given set of trusted domains, using the `gnews` package (a free wrapper
around Google News search — no API key needed).

`gnews` doesn't support an "only these domains" filter directly, so this
combines Google's `site:` search operator with a loop over (company, domain)
pairs, then dedupes by URL.

All results are appended to one CSV as queries complete. In rolling mode,
every window for the identifier uses that same CSV:
    general['paths']['news_links']/<identifier>/news_links.csv
        (single-window mode, i.e. window_days=None)
    general['paths']['news_links']/<identifier>/news_links.csv
        (both single-window and rolling-window modes)

Progress is tracked in a state file alongside the CSV. In rolling mode the
state keys include the window label, while URL deduplication is global to the
identifier. A run spanning thousands of queries can be stopped (Ctrl+C) and
resumed later without creating a directory per window.

    pip install gnews pandas
"""

import csv
import hashlib, json, logging, os, random, tempfile, time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from itertools import islice
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
from gnews import GNews

from src.config import general

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sp500_news")


@dataclass
class NewsItem:
    company: str
    ticker: str
    domain: str
    title: str
    description: str
    url: str
    published_date: str | None
    publisher: str | None

class SP500NewsFetcher:
    """
    Searches Google News (via gnews) for each (company, domain) pair within
    a time window, and returns a deduplicated list of NewsItem results.

    Resumable + incrementally exported to CSV:
        - Every query's results are appended to a
        news_links.csv as soon as that query completes — so you never lose
        already-fetched results even if the run is stopped partway through
        thousands of queries.
        - A `_query_state.json` file alongside the CSV tracks which query
        batches are done. In rolling mode each state entry includes its
        window label, so re-running the same identifier skips only completed
        batches for that window.

    Two modes:
        - Single window (default, window_days=None): behaves exactly as
        before — one CSV/state file for the whole [start_date, end_date]
        range. Use `.run()`.
        - Rolling window (window_days=N): splits [start_date, end_date] into
        consecutive N-day windows and appends them all to the same CSV/state
        files. State entries are window-scoped and URL deduplication spans
        the whole identifier. Use `.run_rolling()`.

    Example (single window):
        companies = {"AAPL": "Apple", "MSFT": "Microsoft"}
        domains = ["cnbc.com", "finance.yahoo.com", "ft.com",
                    "investing.com", "nasdaq.com", "seekingalpha.com",
                    "stocktwits.com"]

        fetcher = SP500NewsFetcher(
            companies=companies,
            domains=domains,
            start_date=datetime(2026, 8, 1),
            end_date=datetime(2026, 8, 5),
            max_results_per_query=15,
            identifier="2026-08-01_to_2026-08-05",
        )
        items = fetcher.run()
        urls = fetcher.urls()          # feed straight into BulkCrawler
        df = fetcher.to_pandas()       # DataFrame of everything in news_links.csv

    Example (rolling window over a much larger range):
        fetcher = SP500NewsFetcher(
            companies=companies,
            domains=domains,
            start_date=datetime(2020, 1, 1),
            end_date=datetime(2026, 8, 5),
            max_results_per_query=15,
            identifier="2020-01-01_to_2026-08-05",
            window_days=7,             # one query plan per 7-day window
        )
        items = fetcher.run_rolling()
        df = fetcher.to_pandas_all_windows()
        status = fetcher.progress_all_windows()
    """

    def __init__(
        self,
        companies: dict[str, str],   # {ticker: company_name}
        domains: list[str],
        start_date: datetime,
        end_date: datetime,
        language: str = "en",
        country: str = "US",
        max_results_per_query: int = 20,
        request_delay_seconds: float = 1.0,
        domain_batch_size: int = 4,
        checkpoint_every: int = 25,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
        identifier: str | None = None,
        window_days: int | None = None,
    ):
        self.companies = companies
        self.domains = list(dict.fromkeys(d.lower().strip() for d in domains if d.strip()))
        self.start_date = start_date
        self.end_date = end_date
        self.max_results_per_query = max_results_per_query
        self.request_delay_seconds = request_delay_seconds
        self.domain_batch_size = max(1, domain_batch_size)
        self.checkpoint_every = max(1, checkpoint_every)
        self.max_retries = max(0, max_retries)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.window_days = window_days
        self._current_window: str | None = None

        self.gnews = GNews(
            language=language,
            country=country,
            start_date=start_date,
            end_date=end_date,
            max_results=max_results_per_query,
        )

        # Base output folder for this whole run (identifier-scoped).
        self.identifier = identifier or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._base_output_dir = os.path.join(general["paths"]["news_links"], self.identifier)
        os.makedirs(self._base_output_dir, exist_ok=True)

        # Both modes use one identifier directory. Rolling-mode state keys
        # are window-scoped, rather than being stored in subdirectories.
        self.output_dir = self._base_output_dir
        self.csv_path = os.path.join(self.output_dir, "news_links.csv")
        self.state_path = os.path.join(self.output_dir, "_query_state.json")
        self.state: dict[str, dict] = self._load_state()

        # If resuming, seed _seen_urls from the CSV already on disk so we
        # don't re-append duplicate rows for URLs found by an earlier,
        # now-completed query in a prior run.
        self._seen_urls: set[str] = self._load_seen_urls_from_csv()
        self.items: list[NewsItem] = []
        self.errors: list[dict] = []

    # ------------------------------------------------------------------ #
    # State persistence (per query, not per URL — tracks progress through
    # the ticker x domain search space, not the crawl of individual articles)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _query_key(ticker: str, domains: tuple[str, ...], window: str | None) -> str:
        raw = f"{window or 'single'}|{ticker}|{'|'.join(domains)}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def _load_state(self) -> dict:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                done = sum(1 for v in data.values() if v.get("status") == "done")
                logger.info(
                    f"Resuming from existing query state: {self.state_path} "
                    f"({done}/{len(data)} queries previously done)"
                )
                return data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Could not read query state file ({e}), starting fresh.")
        return {}

    def _save_state(self):
        dir_ = os.path.dirname(self.state_path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix="_query_state_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp_path, self.state_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def _mark(self, ticker: str, domains: tuple[str, ...], status: str, **extra):
        key = self._query_key(ticker, domains, self._current_window)
        entry = {
            "ticker": ticker,
            "domains": list(domains),
            "window": self._current_window,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        entry.update(extra)
        self.state[key] = entry

    def _is_done(self, ticker: str, domains: tuple[str, ...]) -> bool:
        entry = self.state.get(self._query_key(ticker, domains, self._current_window))
        return bool(entry and entry.get("status") == "done")

    # ------------------------------------------------------------------ #
    # Single-file CSV export (append-as-you-go)
    # ------------------------------------------------------------------ #
    def _load_seen_urls_from_csv(self) -> set[str]:
        if os.path.exists(self.csv_path):
            try:
                existing = pd.read_csv(self.csv_path, usecols=["url"])
                return {self._normalise_url(url) for url in existing["url"].dropna()}
            except (pd.errors.EmptyDataError, ValueError, OSError):
                return set()
        return set()

    def _append_to_csv(self, results: list[NewsItem]) -> None:
        """Append new rows to the single shared CSV. Writes the header only
        the first time the file is created."""
        if not results:
            return
        write_header = not os.path.exists(self.csv_path)
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=NewsItem.__dataclass_fields__.keys())
            if write_header:
                writer.writeheader()
            writer.writerows(asdict(item) for item in results)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalise_url(url: str) -> str:
        """Normalise enough for deduplication without changing exported URLs."""
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))

    @staticmethod
    def _matching_domain(url: str, domains: tuple[str, ...]) -> str | None:
        host = (urlsplit(url).hostname or "").lower()
        matches = [domain for domain in domains if host == domain or host.endswith(f".{domain}")]
        return max(matches, key=len) if matches else None

    def _search_one(self, ticker: str, company_name: str, domains: tuple[str, ...]) -> list[NewsItem] | None:
        site_filter = " OR ".join(f"site:{domain}" for domain in domains)
        query = f'"{company_name}" ({site_filter})'
        for attempt in range(self.max_retries + 1):
            try:
                raw_results = self.gnews.get_news(query)
                break
            except Exception as e:
                if attempt == self.max_retries:
                    logger.warning(f"  query failed after {attempt + 1} attempt(s): {query!r} -> {e}")
                    self.errors.append({"ticker": ticker, "domains": list(domains), "error": str(e)})
                    return None
                delay = self.retry_backoff_seconds * (2 ** attempt) + random.uniform(0, 0.5)
                logger.warning(f"  query failed; retrying in {delay:.1f}s: {query!r} -> {e}")
                time.sleep(delay)

        found = []
        for r in raw_results or []:
            url = r.get("url")
            normalised_url = self._normalise_url(url) if url else ""
            if not url or normalised_url in self._seen_urls:
                continue
            self._seen_urls.add(normalised_url)
            # `gnews` can return a news.google.com redirect rather than the
            # publisher URL. The search itself is site-restricted, so do not
            # discard that result merely because its redirect host cannot be
            # matched to a requested domain.
            result_domain = self._matching_domain(url, domains) or (urlsplit(url).hostname or domains[0])
            found.append(
                NewsItem(
                    company=company_name,
                    ticker=ticker,
                    domain=result_domain,
                    title=r.get("title", ""),
                    description=r.get("description", ""),
                    url=url,
                    published_date=r.get("published date"),
                    publisher=(r.get("publisher") or {}).get("title"),
                )
            )
        return found

    def run(self, retry_failed: bool = True) -> list[NewsItem]:
        """
        Runs every (ticker, domain) query not already marked "done" in the
        state file for the CURRENT window (self.csv_path / self.state_path).
        Safe to Ctrl+C at any point: results are appended to CSV immediately,
        while state is checkpointed every `checkpoint_every` queries and on
        interruption. Re-running resumes from the last checkpoint.

        In single-window mode (window_days=None) this covers the whole
        [start_date, end_date] range, same as before. In rolling mode, call
        this indirectly via run_rolling(), which repoints the fetcher at
        each window before calling this.
        """
        domain_batches = list(self._batched_domains())
        total = len(self.companies) * len(domain_batches)
        pending = []
        skipped = 0
        for ticker, company_name in self.companies.items():
            for domains in domain_batches:
                if self._is_done(ticker, domains):
                    skipped += 1
                    continue
                entry = self.state.get(self._query_key(ticker, domains, self._current_window), {})
                if not retry_failed and entry.get("status") == "failed":
                    skipped += 1
                    continue
                pending.append((ticker, company_name, domains))
        logger.info(
            f"Query plan: {total} total (ticker,domain-batch) queries, {skipped} already done/skipped, "
            f"{len(pending)} pending. Window {self.gnews.start_date.date() if hasattr(self.gnews.start_date, 'date') else self.gnews.start_date} "  # type: ignore
            f"-> {self.gnews.end_date.date() if hasattr(self.gnews.end_date, 'date') else self.gnews.end_date}. "  # type: ignore
            f"Output: {self.output_dir}"
        )

        progress_pct = 0.0
        try:
            for i, (ticker, company_name, domains) in enumerate(pending, start=1):
                results = self._search_one(ticker, company_name, domains)
                if results is None:
                    self._mark(ticker, domains, "failed")
                    result_count = 0
                else:
                    self._append_to_csv(results)
                    self.items.extend(results)
                    self._mark(ticker, domains, "done", result_count=len(results))
                    result_count = len(results)
                if i % self.checkpoint_every == 0:
                    self._save_state()

                progress_pct = (skipped + i) / total * 100
                logger.info(
                    f"[{skipped + i}/{total} | {progress_pct:5.1f}%] "
                    f"{ticker} @ {', '.join(domains)}: {result_count} articles -> {self.csv_path}"
                )
                time.sleep(self.request_delay_seconds)  # be polite to Google News

        except KeyboardInterrupt:
            self._save_state()
            logger.warning(
                f"Interrupted at {progress_pct:5.1f}%. "
                "State + all completed query CSVs are saved. Re-run with the same "
                "identifier (and window, if rolling) to resume."
            )
            raise

        self._save_state()
        logger.info(
            f"Done. {len(self.items)} unique articles across {len(pending)} queries run, "
            f"{len(self.errors)} failed queries."
        )
        return self.items

    def _batched_domains(self):
        iterator = iter(self.domains)
        while batch := tuple(islice(iterator, self.domain_batch_size)):
            yield batch

    def progress(self) -> dict:
        """Quick status snapshot for the CURRENT window, without running
        anything — good for checking in on a long-running job from another
        process/notebook."""
        total = len(self.companies) * sum(1 for _ in self._batched_domains())
        current = [v for v in self.state.values() if v.get("window") == self._current_window]
        done = sum(1 for v in current if v.get("status") == "done")
        failed = sum(1 for v in current if v.get("status") == "failed")
        total_articles = sum(v.get("result_count", 0) for v in current if v.get("status") == "done")
        return {
            "total_queries": total,
            "done": done,
            "failed": failed,
            "remaining": total - done - failed,
            "pct_complete": round(done / total * 100, 1) if total else 0.0,
            "total_articles_found": total_articles,
            "output_dir": self.output_dir,
        }

    # ------------------------------------------------------------------ #
    def urls(self) -> list[str]:
        """Flat list of URLs — feed directly into BulkCrawler.run(urls)."""
        return [item.url for item in self.items]

    def to_dicts(self) -> list[dict]:
        return [item.__dict__ for item in self.items]

    def to_pandas(self, from_disk: bool = True) -> pd.DataFrame:
        """
        DataFrame of all results for the CURRENT window.

        from_disk=True (default): reads the full news_links.csv off disk, so
        it reflects everything ever fetched for this window, including from
        earlier runs before a resume — not just this session.
        from_disk=False: only the items fetched in this Python session.
        """
        if from_disk and os.path.exists(self.csv_path):
            return pd.read_csv(self.csv_path)
        if not self.items:
            return pd.DataFrame(columns=list(NewsItem.__dataclass_fields__.keys()))
        return pd.DataFrame.from_records(asdict(item) for item in self.items)

    def group_by_company(self) -> dict[str, list[NewsItem]]:
        grouped: dict[str, list[NewsItem]] = {}
        for item in self.items:
            grouped.setdefault(item.ticker, []).append(item)
        return grouped

    # ------------------------------------------------------------------ #
    # Rolling window support
    # ------------------------------------------------------------------ #
    def _generate_windows(self) -> list[tuple[datetime, datetime]]:
        """
        Splits [start_date, end_date] into consecutive windows of
        window_days each (last window may be shorter). If window_days is
        not set, returns a single window covering the whole range.
        """
        if not self.window_days:
            return [(self.start_date, self.end_date)]

        windows = []
        cur = self.start_date
        delta = timedelta(days=self.window_days)
        while cur < self.end_date:
            w_end = min(cur + delta, self.end_date)
            windows.append((cur, w_end))
            cur = w_end
        return windows

    @staticmethod
    def _window_label(w_start: datetime, w_end: datetime) -> str:
        return f"{w_start.date()}_to_{w_end.date()}"

    def _switch_window(self, w_start: datetime, w_end: datetime, label: str) -> None:
        """Set the active time window while retaining one output directory."""
        self.gnews.start_date = w_start
        self.gnews.end_date = w_end

        self._current_window = label
        self.items = []

    def run_rolling(self, retry_failed: bool = True) -> list[NewsItem]:
        """
        Runs the full (ticker, domain-batch) plan for every window. All
        windows append to one CSV and share one state file in the identifier
        directory; state entries are labelled by window for correct resume.

        Requires window_days to have been set on the constructor.
        """
        if not self.window_days:
            raise ValueError("run_rolling() requires window_days to be set on the constructor.")

        windows = self._generate_windows()
        logger.info(
            f"Rolling window plan: {len(windows)} window(s) of {self.window_days} day(s) each, "
            f"covering {self.start_date.date()} -> {self.end_date.date()}."
        )

        all_items: list[NewsItem] = []
        for idx, (w_start, w_end) in enumerate(windows, start=1):
            label = self._window_label(w_start, w_end)
            logger.info(f"=== Window {idx}/{len(windows)}: {label} ===")
            self._switch_window(w_start, w_end, label)
            window_items = self.run(retry_failed=retry_failed)
            all_items.extend(window_items)

        self.items = all_items
        return all_items

    def to_pandas_all_windows(self) -> pd.DataFrame:
        """DataFrame of all rolling-window output in the shared CSV."""
        return self.to_pandas(from_disk=True)

    def progress_all_windows(self) -> dict:
        """Aggregated status snapshot across every window in the rolling
        plan, without running anything."""
        windows = self._generate_windows()
        per_query_total = len(self.companies) * sum(1 for _ in self._batched_domains())

        per_window = []
        total_done = total_failed = total_articles = 0
        for w_start, w_end in windows:
            label = self._window_label(w_start, w_end)
            state = [v for v in self.state.values() if v.get("window") == label]
            done = sum(1 for v in state if v.get("status") == "done")
            failed = sum(1 for v in state if v.get("status") == "failed")
            articles = sum(v.get("result_count", 0) for v in state if v.get("status") == "done")
            per_window.append({
                "window": label,
                "done": done,
                "failed": failed,
                "remaining": per_query_total - done - failed,
                "pct_complete": round(done / per_query_total * 100, 1) if per_query_total else 0.0,
                "articles": articles,
            })
            total_done += done
            total_failed += failed
            total_articles += articles

        total_queries = per_query_total * len(windows)
        return {
            "total_windows": len(windows),
            "total_queries": total_queries,
            "done": total_done,
            "failed": total_failed,
            "remaining": total_queries - total_done - total_failed,
            "pct_complete": round(total_done / total_queries * 100, 1) if total_queries else 0.0,
            "total_articles_found": total_articles,
            "base_output_dir": self._base_output_dir,
        }


# ---------------------------------------------------------------------- #
# Example usage
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    # In practice, pull this from your S&P500 reference data (e.g. a CSV or
    # src.config), not hardcoded — this is just a small sample.
    sp500_sample = {
        "AAPL": "Apple",
        "MSFT": "Microsoft",
        "NVDA": "Nvidia",
        "AMZN": "Amazon",
        "GOOGL": "Alphabet",
    }

    domains = [
        "cnbc.com", "finance.yahoo.com", "ft.com",
        "investing.com", "nasdaq.com", "seekingalpha.com",
        "stocktwits.com",
    ]

    # --- Single window (same behavior as before) ---
    fetcher = SP500NewsFetcher(
        companies=sp500_sample,
        domains=domains,
        start_date=datetime(2026, 8, 1),
        end_date=datetime(2026, 8, 5),
        max_results_per_query=15,
        request_delay_seconds=1.0,
        identifier="2026-08-01_to_2026-08-05",  # fixed id so it's resumable
    )

    print("Progress before starting:", fetcher.progress())
    items = fetcher.run()
    print("Progress after run:", fetcher.progress())
    print(f"All results in one file: {fetcher.csv_path}")
    print(fetcher.to_pandas().head())

    # --- Rolling window over a much larger range ---
    rolling_fetcher = SP500NewsFetcher(
        companies=sp500_sample,
        domains=domains,
        start_date=datetime(2020, 1, 1),
        end_date=datetime(2026, 8, 5),
        max_results_per_query=15,
        request_delay_seconds=1.0,
        identifier="2020-01-01_to_2026-08-05",
        window_days=7,  # one independent query plan per 7-day window
    )

    print("Rolling progress before starting:", rolling_fetcher.progress_all_windows())
    rolling_items = rolling_fetcher.run_rolling()
    print("Rolling progress after run:", rolling_fetcher.progress_all_windows())
    print(rolling_fetcher.to_pandas_all_windows().head())
