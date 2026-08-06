"""
Fetch news for a list of S&P500 companies over a time window, restricted to
a given set of trusted domains, using the `gnews` package (a free wrapper
around Google News search — no API key needed).

`gnews` doesn't support an "only these domains" filter directly, so this
combines Google's `site:` search operator with a loop over (company, domain)
pairs, then dedupes by URL.

All results across every (ticker, domain) query for a single window are
appended to a single CSV as they come in:
    general['paths']['news_links']/<identifier>/news_links.csv
        (single-window mode, i.e. window_days=None)
    general['paths']['news_links']/<identifier>/<window_start>_to_<window_end>/news_links.csv
        (rolling-window mode, i.e. window_days is set)

Progress is tracked in a state file alongside each CSV, so a run spanning
thousands of queries (optionally across many rolling windows) can be safely
stopped (Ctrl+C) and resumed later, picking up only the queries that
haven't completed yet.

    pip install gnews pandas
"""

import hashlib, json, logging, os, tempfile, time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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

    def to_pandas(self) -> pd.DataFrame:
        """Return a single-row DataFrame for this NewsItem."""
        return pd.DataFrame([self.__dict__])


class SP500NewsFetcher:
    """
    Searches Google News (via gnews) for each (company, domain) pair within
    a time window, and returns a deduplicated list of NewsItem results.

    Resumable + incrementally exported to CSV:
        - Every (ticker, domain) query's results are appended to a
        news_links.csv as soon as that query completes — so you never lose
        already-fetched results even if the run is stopped partway through
        thousands of queries.
        - A `_query_state.json` file alongside the CSV tracks which
        (ticker, domain) pairs are done, so re-running with the same
        `identifier` (and, for rolling runs, the same window) skips
        completed queries automatically.

    Two modes:
        - Single window (default, window_days=None): behaves exactly as
        before — one CSV/state file for the whole [start_date, end_date]
        range. Use `.run()`.
        - Rolling window (window_days=N): splits [start_date, end_date] into
        consecutive N-day windows, and runs the full (ticker, domain) query
        plan independently for each window, each with its own CSV/state
        file in a window-labeled subfolder. Use `.run_rolling()`. This is
        useful for very large date ranges, since Google News search tends
        to return incomplete results for very wide windows, and it lets you
        resume window-by-window.

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
        identifier: str | None = None,
        window_days: int | None = None,
    ):
        self.companies = companies
        self.domains = [d.lower() for d in domains]
        self.start_date = start_date
        self.end_date = end_date
        self.max_results_per_query = max_results_per_query
        self.request_delay_seconds = request_delay_seconds
        self.window_days = window_days

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

        # In single-window mode these point straight at the base dir, same
        # as before. In rolling mode, run_rolling() repoints these at each
        # window's subfolder via _switch_window() before calling run().
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
    def _query_key(ticker: str, domain: str) -> str:
        raw = f"{ticker}|{domain}"
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

    def _mark(self, ticker: str, domain: str, status: str, **extra):
        key = self._query_key(ticker, domain)
        entry = {
            "ticker": ticker,
            "domain": domain,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        entry.update(extra)
        self.state[key] = entry

    def _is_done(self, ticker: str, domain: str) -> bool:
        entry = self.state.get(self._query_key(ticker, domain))
        return bool(entry and entry.get("status") == "done")

    # ------------------------------------------------------------------ #
    # Single-file CSV export (append-as-you-go)
    # ------------------------------------------------------------------ #
    def _load_seen_urls_from_csv(self) -> set[str]:
        if os.path.exists(self.csv_path):
            try:
                existing = pd.read_csv(self.csv_path, usecols=["url"])
                return set(existing["url"].dropna().tolist())
            except (pd.errors.EmptyDataError, ValueError, OSError):
                return set()
        return set()

    def _append_to_csv(self, results: list[NewsItem]) -> None:
        """Append new rows to the single shared CSV. Writes the header only
        the first time the file is created."""
        if not results:
            return
        df = pd.concat([item.to_pandas() for item in results], ignore_index=True)
        write_header = not os.path.exists(self.csv_path)
        df.to_csv(self.csv_path, mode="a", header=write_header, index=False)

    # ------------------------------------------------------------------ #
    def _search_one(self, ticker: str, company_name: str, domain: str) -> list[NewsItem]:
        query = f'{company_name} site:{domain}'
        try:
            raw_results = self.gnews.get_news(query)
        except Exception as e:
            logger.warning(f"  query failed: {query!r} -> {e}")
            self.errors.append({"ticker": ticker, "domain": domain, "error": str(e)})
            return []

        found = []
        for r in raw_results or []:
            url = r.get("url")
            if not url or url in self._seen_urls:
                continue
            self._seen_urls.add(url)
            found.append(
                NewsItem(
                    company=company_name,
                    ticker=ticker,
                    domain=domain,
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
        Safe to Ctrl+C at any point — each query's result is saved to CSV
        and marked in the state file immediately after that query
        completes, so re-running resumes from the next unfinished query
        rather than starting over.

        In single-window mode (window_days=None) this covers the whole
        [start_date, end_date] range, same as before. In rolling mode, call
        this indirectly via run_rolling(), which repoints the fetcher at
        each window before calling this.
        """
        all_pairs = [(t, c, d) for t, c in self.companies.items() for d in self.domains]

        pending = []
        skipped = 0
        for ticker, company_name, domain in all_pairs:
            if self._is_done(ticker, domain):
                skipped += 1
                continue
            if not retry_failed and self.state.get(self._query_key(ticker, domain), {}).get("status") == "failed":
                skipped += 1
                continue
            pending.append((ticker, company_name, domain))

        total = len(all_pairs)
        logger.info(
            f"Query plan: {total} total (ticker,domain) pairs, {skipped} already done/skipped, "
            f"{len(pending)} pending. Window {self.gnews.start_date.date() if hasattr(self.gnews.start_date, 'date') else self.gnews.start_date} "  # type: ignore
            f"-> {self.gnews.end_date.date() if hasattr(self.gnews.end_date, 'date') else self.gnews.end_date}. "  # type: ignore
            f"Output: {self.output_dir}"
        )

        progress_pct = 0.0
        try:
            for i, (ticker, company_name, domain) in enumerate(pending, start=1):
                results = self._search_one(ticker, company_name, domain)
                self._append_to_csv(results)
                self.items.extend(results)

                self._mark(ticker, domain, "done", result_count=len(results))
                self._save_state()  # after every single query — cheap, and maximizes safety

                progress_pct = (skipped + i) / total * 100
                logger.info(
                    f"[{skipped + i}/{total} | {progress_pct:5.1f}%] "
                    f"{ticker} @ {domain}: {len(results)} articles -> {self.csv_path}"
                )
                time.sleep(self.request_delay_seconds)  # be polite to Google News

        except (KeyboardInterrupt,):
            logger.warning(
                f"Interrupted at {progress_pct:5.1f}%. "
                "State + all completed query CSVs are saved. Re-run with the same "
                "identifier (and window, if rolling) to resume."
            )
            raise

        logger.info(
            f"Done. {len(self.items)} unique articles across {len(pending)} queries run, "
            f"{len(self.errors)} failed queries."
        )
        return self.items

    def progress(self) -> dict:
        """Quick status snapshot for the CURRENT window, without running
        anything — good for checking in on a long-running job from another
        process/notebook."""
        total = len(self.companies) * len(self.domains)
        done = sum(1 for v in self.state.values() if v.get("status") == "done")
        failed = sum(1 for v in self.state.values() if v.get("status") == "failed")
        total_articles = sum(v.get("result_count", 0) for v in self.state.values() if v.get("status") == "done")
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
        return pd.concat([item.to_pandas() for item in self.items], ignore_index=True)

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
        """Repoints the fetcher's gnews client, output paths, state and
        seen-URL cache at a specific window's subfolder."""
        self.gnews.start_date = w_start
        self.gnews.end_date = w_end

        # self.output_dir = os.path.join(self._base_output_dir, label)

        self.csv_path = os.path.join(self._base_output_dir, "news_links.csv")
        self.state_path = os.path.join(self._base_output_dir, "_query_state.json")
        self.state = self._load_state()
        self._seen_urls = self._load_seen_urls_from_csv()
        self.items = []

    def run_rolling(self, retry_failed: bool = True) -> list[NewsItem]:
        """
        Runs the full (ticker, domain) query plan independently for every
        window_days-sized window between start_date and end_date. Each
        window gets its own CSV + state file (under a
        <window_start>_to_<window_end> subfolder of the identifier's output
        dir), so a run spanning years can be Ctrl+C'd and resumed — it will
        pick back up on the current window's remaining queries, then
        continue to the next window.

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

    def _window_dirs(self) -> list[str]:
        if not os.path.isdir(self._base_output_dir):
            return []
        return sorted(
            os.path.join(self._base_output_dir, d)
            for d in os.listdir(self._base_output_dir)
            if os.path.isdir(os.path.join(self._base_output_dir, d))
        )

    def to_pandas_all_windows(self) -> pd.DataFrame:
        """DataFrame combining news_links.csv from every window subfolder
        found on disk under this identifier's output dir."""
        dfs = []
        for window_dir in self._window_dirs():
            csv_path = os.path.join(window_dir, "news_links.csv")
            if os.path.exists(csv_path):
                dfs.append(pd.read_csv(csv_path))
        if not dfs:
            return pd.DataFrame(columns=list(NewsItem.__dataclass_fields__.keys()))
        return pd.concat(dfs, ignore_index=True)

    def progress_all_windows(self) -> dict:
        """Aggregated status snapshot across every window in the rolling
        plan, without running anything."""
        windows = self._generate_windows()
        per_query_total = len(self.companies) * len(self.domains)

        per_window = []
        total_done = total_failed = total_articles = 0
        for w_start, w_end in windows:
            label = self._window_label(w_start, w_end)
            state_path = os.path.join(self._base_output_dir, label, "_query_state.json")
            done = failed = articles = 0
            if os.path.exists(state_path):
                try:
                    with open(state_path, "r", encoding="utf-8") as f:
                        state = json.load(f)
                    done = sum(1 for v in state.values() if v.get("status") == "done")
                    failed = sum(1 for v in state.values() if v.get("status") == "failed")
                    articles = sum(v.get("result_count", 0) for v in state.values() if v.get("status") == "done")
                except (json.JSONDecodeError, OSError):
                    pass
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