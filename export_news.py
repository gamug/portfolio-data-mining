"""
sp500_news_scraper.py

Scrapes news notices for S&P500 companies from a fixed set of financial
portals, using the Google News RSS search endpoint, across a date range
split into 7-day windows.

Query design
------------
Google News RSS accepts boolean `site:` filters combined with OR inside a
single `q=` parameter, e.g.:

    "Apple" (site:cnbc.com OR site:finance.yahoo.com OR site:ft.com) after:2024-01-01 before:2024-01-08

So instead of firing one HTTP request per (company, domain, window) triple
-- which would be 7x more requests than necessary -- the 7 target domains
are grouped into chunks of 3 (config: DOMAIN_CHUNK_SIZE) and each chunk is
folded into a single query. That gives, per company and per time window:

    ceil(len(DOMAINS) / DOMAIN_CHUNK_SIZE) queries

With 7 domains and chunk size 3: 3 queries per (company, window) instead of 7.

Each individual query result (the raw RSS/XML payload) is written to disk
immediately after being fetched -- not batched -- so a crash or rate-limit
kill partway through a run only loses at most the in-flight request, and a
re-run automatically skips anything already saved (idempotent/resumable).

Output layout
-------------
    {general['paths']['news']}/html/{identifier}/{company}__{domains}__{start}_{end}.xml

Logging
-------
A run-scoped log file is created at:
    {general['paths']['logs']}/sp500_news_scraper_{identifier}.log

Every completed query logs progress (n/total) plus a rolling ETA computed
from the mean time-per-query observed so far in the run.
"""

import os
import re
import time
import logging
import requests
from datetime import datetime, timedelta
from io import StringIO
import pandas as pd
from urllib.parse import quote

from src.config import general


class SP500NewsScraper:
    """Scrape S&P500-related notices via Google News RSS for a fixed
    set of financial portals, over a date range split into weekly windows.
    """

    DOMAINS = [
        "cnbc.com", "finance.yahoo.com", "ft.com",
        "investing.com", "Nasdaq.com", "seekingalpha.com",
        "stocktwits.com",
    ]

    GNEWS_RSS_BASE = "https://news.google.com/rss/search"

    def __init__(
        self,
        identifier: str,
        companies,
        start_date,
        end_date,
        domain_chunk_size: int = 3,
        window_days: int = 7,
        request_delay: float = 1.0,
        max_retries: int = 3,
        timeout: int = 15,
        skip_existing: bool = True,
    ):
        """
        Parameters
        ----------
        identifier : str
            Run identifier. Used as the output subfolder name and as a
            suffix on the log filename, so different runs never collide.
        companies : list[str]
            Company names (or "name" search terms) to query, e.g. S&P500
            constituent names. See `get_sp500_companies()` for a helper
            that pulls the current list from Wikipedia.
        start_date, end_date : str ("YYYY-MM-DD") or datetime
            Overall date range to cover. Split internally into
            `window_days`-sized windows.
        domain_chunk_size : int
            How many domains to OR together into a single query. 3 is the
            default per the "one query per window, per company, per group
            of three domains" scheme described above.
        window_days : int
            Width of each time window in days (default 7).
        request_delay : float
            Seconds to sleep between requests (politeness / rate-limit
            avoidance).
        max_retries : int
            Retries with exponential backoff on transient HTTP failures.
        timeout : int
            Per-request timeout in seconds.
        skip_existing : bool
            If True, a query whose output file already exists on disk is
            skipped (makes reruns resumable and cheap).
        """
        self.identifier = identifier
        self.companies = list(companies)
        self.start_date = self._parse_date(start_date)
        self.end_date = self._parse_date(end_date)
        self.domain_chunk_size = domain_chunk_size
        self.window_days = window_days
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.timeout = timeout
        self.skip_existing = skip_existing

        # --- output path: {general['paths']['news']}/html/{identifier} ---
        self.output_dir = os.path.join(
            general["paths"]["news"], "html", self.identifier
        )
        os.makedirs(self.output_dir, exist_ok=True)

        # --- logging: {general['paths']['logs']}/... ---
        self.log_dir = general["paths"]["logs"]
        os.makedirs(self.log_dir, exist_ok=True)
        self.logger = self._build_logger()

        self.domain_groups = self._chunk_domains()
        self.time_windows = self._build_time_windows()

        self.total_queries = (
            len(self.companies) * len(self.domain_groups) * len(self.time_windows)
        )
        self.completed_queries = 0
        self.skipped_queries = 0
        self.failed_queries = 0
        self._run_start_ts = None

    # ------------------------------------------------------------------ #
    # Setup helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_date(d):
        if isinstance(d, datetime):
            return d
        return datetime.strptime(d, "%Y-%m-%d")

    def _build_logger(self):
        logger = logging.getLogger(f"SP500NewsScraper.{self.identifier}")
        logger.setLevel(logging.INFO)
        logger.propagate = False

        if not logger.handlers:
            fmt = logging.Formatter(
                "%(asctime)s | %(levelname)-7s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            log_path = os.path.join(
                self.log_dir, f"sp500_news_scraper_{self.identifier}.log"
            )
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)

            ch = logging.StreamHandler()
            ch.setFormatter(fmt)
            logger.addHandler(ch)

        return logger

    def _chunk_domains(self):
        return [
            self.DOMAINS[i : i + self.domain_chunk_size]
            for i in range(0, len(self.DOMAINS), self.domain_chunk_size)
        ]

    def _build_time_windows(self):
        windows = []
        cur = self.start_date
        while cur < self.end_date:
            win_end = min(cur + timedelta(days=self.window_days), self.end_date)
            windows.append((cur, win_end))
            cur = win_end
        return windows

    # ------------------------------------------------------------------ #
    # Query construction / fetching / saving
    # ------------------------------------------------------------------ #
    def _build_query_url(self, company, domains, window_start, window_end):
        site_filter = " OR ".join(f"site:{d}" for d in domains)
        after = window_start.strftime("%Y-%m-%d")
        before = window_end.strftime("%Y-%m-%d")
        query = f'"{company}" ({site_filter}) after:{after} before:{before}'
        return f"{self.GNEWS_RSS_BASE}?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"

    @staticmethod
    def _slugify(text):
        text = text.strip().replace(" ", "_")
        return re.sub(r"[^A-Za-z0-9_.-]", "", text)

    def _output_path(self, company, domains, window_start, window_end):
        company_slug = self._slugify(company)
        domains_slug = "-".join(d.replace(".", "_") for d in domains)
        fname = (
            f"{company_slug}__{domains_slug}__"
            f"{window_start.strftime('%Y%m%d')}_{window_end.strftime('%Y%m%d')}.xml"
        )
        return os.path.join(self.output_dir, fname)

    def _fetch(self, url):
        headers = {"User-Agent": "Mozilla/5.0 (compatible; SP500NewsScraper/1.0)"}
        backoff = 2.0
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.get(url, headers=headers, timeout=self.timeout)
                resp.raise_for_status()
                return resp.text
            except requests.RequestException as exc:
                last_exc = exc
                self.logger.warning(
                    f"Fetch attempt {attempt}/{self.max_retries} failed "
                    f"for {url}: {exc}"
                )
                if attempt < self.max_retries:
                    time.sleep(backoff)
                    backoff *= 2
        raise last_exc

    # ------------------------------------------------------------------ #
    # ETA
    # ------------------------------------------------------------------ #
    def _log_progress(self):
        done = self.completed_queries + self.skipped_queries
        elapsed = time.time() - self._run_start_ts
        processed_for_rate = max(self.completed_queries, 1)  # avoid div/0, skips are ~free
        avg_per_query = elapsed / processed_for_rate
        remaining = self.total_queries - done
        eta_seconds = max(remaining, 0) * avg_per_query
        eta_str = str(timedelta(seconds=int(eta_seconds)))
        pct = (done / self.total_queries * 100) if self.total_queries else 100.0
        self.logger.info(
            f"Progress {done}/{self.total_queries} ({pct:5.1f}%) | "
            f"ok={self.completed_queries} skip={self.skipped_queries} "
            f"fail={self.failed_queries} | ETA: {eta_str}"
        )

    # ------------------------------------------------------------------ #
    # Main entrypoint
    # ------------------------------------------------------------------ #
    def run(self):
        self._run_start_ts = time.time()
        self.logger.info(
            f"Starting run '{self.identifier}': {len(self.companies)} companies x "
            f"{len(self.domain_groups)} domain-groups x {len(self.time_windows)} "
            f"windows = {self.total_queries} queries. Output: {self.output_dir}"
        )

        for company in self.companies:
            for domains in self.domain_groups:
                for window_start, window_end in self.time_windows:
                    out_path = self._output_path(company, domains, window_start, window_end)

                    if self.skip_existing and os.path.exists(out_path):
                        self.skipped_queries += 1
                        self.logger.info(f"Skip (exists): {out_path}")
                        self._log_progress()
                        continue

                    url = self._build_query_url(company, domains, window_start, window_end)
                    try:
                        content = self._fetch(url)
                        with open(out_path, "w", encoding="utf-8") as f:
                            f.write(content)
                        self.completed_queries += 1
                        self.logger.info(f"Saved: {out_path}")
                    except Exception as exc:
                        self.failed_queries += 1
                        self.logger.error(
                            f"FAILED company={company!r} domains={domains} "
                            f"window=({window_start.date()}..{window_end.date()}): {exc}"
                        )
                    finally:
                        self._log_progress()
                        time.sleep(self.request_delay)

        self.logger.info(
            f"Run '{self.identifier}' complete. "
            f"ok={self.completed_queries} skip={self.skipped_queries} "
            f"fail={self.failed_queries} total={self.total_queries}"
        )
        return {
            "completed": self.completed_queries,
            "skipped": self.skipped_queries,
            "failed": self.failed_queries,
            "total": self.total_queries,
            "output_dir": self.output_dir,
        }


def get_sp500_companies():
    """Convenience helper: pull current S&P500 constituent names from
    Wikipedia. Requires `pandas` + `lxml`/`html5lib`. Returns a list[str]
    of company names suitable to pass as `companies` above.
    """
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    response = requests.get(url, headers=headers)
    response.raise_for_status()

    # Wrap the HTML text in StringIO so pandas treats it as a file-like object
    html = StringIO(response.text)

    tables = pd.read_html(html)
    return tables[0]


if __name__ == "__main__":
    # Example usage
    companies = get_sp500_companies().head()  # or a manual list, e.g. ["Apple", "Microsoft"]

    scraper = SP500NewsScraper(
        identifier="sp500_run_2024",
        companies=companies,
        start_date="2024-01-01",
        end_date="2024-03-01",
        domain_chunk_size=3,
        window_days=7,
        request_delay=1.0,
    )
    summary = scraper.run()
    print(summary)