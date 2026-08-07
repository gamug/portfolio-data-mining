"""Fast, resumable Google News RSS link collector.

Unlike :mod:`gnews_collector`, this module does not use the ``gnews`` Python
package and never tries to resolve ``news.google.com`` redirect URLs. That
keeps the collection pass fast and bounded: each RSS request uses an explicit
connect/read timeout. Resolve or crawl the exported URLs later in a separate,
bounded worker pool.

All output for an identifier is kept in one directory:
    general['paths']['news_links']/<identifier>/news_links.csv
    general['paths']['news_links']/<identifier>/_query_state.json

The state is window-aware, so rolling runs use the same two files without
needing a directory per window.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import random
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from itertools import islice
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit
from html import unescape

import pandas as pd
import requests

from src.config import general


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


class FastSP500NewsFetcher:
    """Collect Google News RSS links without resolving publisher URLs.

    ``url`` may be a ``news.google.com/rss/articles/...`` redirect. This is
    deliberate: fetching RSS links is fast; following/decoding them belongs in
    the later crawling stage, where timeouts and concurrency can be controlled.
    """

    RSS_ENDPOINT = "https://news.google.com/rss/search"
    USER_AGENT = "portfolio-data-mining/1.0 (+https://github.com/gamug/portfolio-data-mining)"

    def __init__(
        self,
        companies: dict[str, str],
        domains: list[str],
        start_date: datetime,
        end_date: datetime,
        language: str = "en",
        country: str = "US",
        max_results_per_query: int = 50,
        request_delay_seconds: float = 1.0,
        domain_batch_size: int = 4,
        request_timeout: tuple[float, float] = (10.0, 30.0),
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
        checkpoint_every: int = 25,
        identifier: str | None = None,
        window_days: int | None = None,
    ) -> None:
        if not companies:
            raise ValueError("companies must not be empty")
        if not (domains := list(dict.fromkeys(d.lower().strip() for d in domains if d.strip()))):
            raise ValueError("domains must contain at least one domain")
        if end_date <= start_date:
            raise ValueError("end_date must be after start_date")
        if window_days is not None and window_days <= 0:
            raise ValueError("window_days must be positive")

        self.companies = companies
        self.domains = domains
        self.start_date = start_date
        self.end_date = end_date
        self.language = language
        self.country = country
        self.max_results_per_query = max(1, max_results_per_query)
        self.request_delay_seconds = max(0.0, request_delay_seconds)
        self.domain_batch_size = max(1, domain_batch_size)
        self.request_timeout = request_timeout
        self.max_retries = max(0, max_retries)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.checkpoint_every = max(1, checkpoint_every)
        self.window_days = window_days
        self._current_window: str | None = None

        self.identifier = identifier or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.output_dir = os.path.join(general["paths"]["news_links"], self.identifier)
        os.makedirs(self.output_dir, exist_ok=True)
        self.csv_path = os.path.join(self.output_dir, "news_links.csv")
        self.state_path = os.path.join(self.output_dir, "_query_state.json")
        log_dir = general["paths"]["logs"]
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.log_path = os.path.join(log_dir, f"gnews_rss_{self.identifier}_{timestamp}.log")
        self.logger = self._configure_logger()

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
        self.state = self._load_state()
        self._seen_urls = self._load_seen_urls_from_csv()
        self.items: list[NewsItem] = []
        self.errors: list[dict] = []

    def _configure_logger(self) -> logging.Logger:
        """Write progress to both the console and this run's log file."""
        run_logger = logging.getLogger(f"sp500_news_rss.{self.identifier}")
        run_logger.setLevel(logging.INFO)
        run_logger.propagate = False
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

        for handler in run_logger.handlers[:]:
            run_logger.removeHandler(handler)
            handler.close()

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        file_handler = logging.FileHandler(self.log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        run_logger.addHandler(console_handler)
        run_logger.addHandler(file_handler)
        return run_logger

    # ------------------------------------------------------------------
    # Persistent state and output
    # ------------------------------------------------------------------
    @staticmethod
    def _query_key(ticker: str, domains: tuple[str, ...], window: str | None) -> str:
        raw = f"{window or 'single'}|{ticker}|{'|'.join(domains)}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def _load_state(self) -> dict[str, dict]:
        if not os.path.exists(self.state_path):
            return {}
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            done = sum(1 for entry in state.values() if entry.get("status") == "done")
            self.logger.info("Resuming %s (%d/%d queries already done)", self.state_path, done, len(state))
            return state
        except (json.JSONDecodeError, OSError) as error:
            self.logger.warning("Could not read query state (%s); starting with an empty state.", error)
            return {}

    def _save_state(self) -> None:
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(self.state_path), prefix="_query_state_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp_path, self.state_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def _mark(self, ticker: str, domains: tuple[str, ...], status: str, **extra: object) -> None:
        self.state[self._query_key(ticker, domains, self._current_window)] = {
            "ticker": ticker,
            "domains": list(domains),
            "window": self._current_window,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **extra,
        }

    def _is_done(self, ticker: str, domains: tuple[str, ...]) -> bool:
        entry = self.state.get(self._query_key(ticker, domains, self._current_window))
        return bool(entry and entry.get("status") == "done")

    @staticmethod
    def _normalise_url(url: str) -> str:
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))

    def _load_seen_urls_from_csv(self) -> set[str]:
        if not os.path.exists(self.csv_path):
            return set()
        try:
            existing = pd.read_csv(self.csv_path, usecols=["url"])
            return {self._normalise_url(url) for url in existing["url"].dropna()}
        except (pd.errors.EmptyDataError, ValueError, OSError):
            return set()

    def _append_to_csv(self, results: list[NewsItem]) -> None:
        if not results:
            return
        write_header = not os.path.exists(self.csv_path)
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=NewsItem.__dataclass_fields__.keys())
            if write_header:
                writer.writeheader()
            writer.writerows(asdict(item) for item in results)

    # ------------------------------------------------------------------
    # RSS fetching
    # ------------------------------------------------------------------
    @staticmethod
    def _strip_html(text: str | None) -> str:
        if not text:
            return ""
        return " ".join(re.sub(r"<[^>]+>", " ", unescape(text)).split())

    @staticmethod
    def _matching_domain(url: str, domains: tuple[str, ...]) -> str | None:
        host = (urlsplit(url).hostname or "").lower()
        matches = [domain for domain in domains if host == domain or host.endswith(f".{domain}")]
        return max(matches, key=len) if matches else None

    def _build_query(self, company_name: str, domains: tuple[str, ...], start: datetime, end: datetime) -> str:
        site_filter = " OR ".join(f"site:{domain}" for domain in domains)
        return f'"{company_name}" ({site_filter}) after:{start:%Y-%m-%d} before:{end:%Y-%m-%d}'

    def _fetch_rss(self, query: str) -> list[dict] | None:
        params = {"q": query, "hl": self.language, "gl": self.country, "ceid": f"{self.country}:{self.language}"}
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(self.RSS_ENDPOINT, params=params, timeout=self.request_timeout)
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                root = ET.fromstring(response.content)
                return [
                    {
                        "title": item.findtext("title", default=""),
                        "description": item.findtext("description", default=""),
                        "url": item.findtext("link", default=""),
                        "published_date": item.findtext("pubDate"),
                        "publisher": item.findtext("source"),
                        "source_url": (item.find("source").get("url") if item.find("source") is not None else ""),
                    }
                    for item in root.findall("./channel/item")[: self.max_results_per_query]
                ]
            except (requests.RequestException, ET.ParseError) as error:
                if attempt == self.max_retries:
                    self.logger.warning("RSS request failed after %d attempt(s): %s", attempt + 1, error)
                    return None
                delay = self.retry_backoff_seconds * (2**attempt) + random.uniform(0, 0.5)
                self.logger.warning("RSS request failed; retrying in %.1fs: %s", delay, error)
                time.sleep(delay)
        return None  # pragma: no cover - loop always returns

    def _search_one(
        self, ticker: str, company_name: str, domains: tuple[str, ...], start: datetime, end: datetime
    ) -> list[NewsItem] | None:
        raw_results = self._fetch_rss(self._build_query(company_name, domains, start, end))
        if raw_results is None:
            self.errors.append({"ticker": ticker, "domains": list(domains), "error": "RSS request failed"})
            return None

        found = []
        for result in raw_results:
            url = result["url"]
            normalised_url = self._normalise_url(url) if url else ""
            if not url or normalised_url in self._seen_urls:
                continue
            self._seen_urls.add(normalised_url)
            source_url = result["source_url"] or ""
            domain = self._matching_domain(source_url, domains) or self._matching_domain(url, domains) or domains[0]
            found.append(
                NewsItem(
                    company=company_name,
                    ticker=ticker,
                    domain=domain,
                    title=result["title"],
                    description=self._strip_html(result["description"]),
                    url=url,
                    published_date=result["published_date"],
                    publisher=result["publisher"],
                )
            )
        return found

    # ------------------------------------------------------------------
    # Execution and progress
    # ------------------------------------------------------------------
    def _batched_domains(self) -> Iterator[tuple[str, ...]]:
        iterator = iter(self.domains)
        while batch := tuple(islice(iterator, self.domain_batch_size)):
            yield batch

    def _run_window(self, start: datetime, end: datetime, retry_failed: bool) -> list[NewsItem]:
        domain_batches = list(self._batched_domains())
        total = len(self.companies) * len(domain_batches)
        pending = []
        skipped = 0
        for ticker, company in self.companies.items():
            for domains in domain_batches:
                entry = self.state.get(self._query_key(ticker, domains, self._current_window), {})
                if self._is_done(ticker, domains) or (not retry_failed and entry.get("status") == "failed"):
                    skipped += 1
                    continue
                pending.append((ticker, company, domains))

        self.logger.info(
            "Query plan: %d total, %d already done/skipped, %d pending. Window %s -> %s. Output: %s. Log: %s",
            total, skipped, len(pending), start.date(), end.date(), self.output_dir, self.log_path,
        )
        for index, (ticker, company, domains) in enumerate(pending, start=1):
            started = time.monotonic()
            results = self._search_one(ticker, company, domains, start, end)
            if results is None:
                self._mark(ticker, domains, "failed")
                result_count = 0
            else:
                self._append_to_csv(results)
                self.items.extend(results)
                self._mark(ticker, domains, "done", result_count=len(results))
                result_count = len(results)

            if index % self.checkpoint_every == 0:
                self._save_state()
            self.logger.info(
                "[%d/%d | %5.1f%%] %s @ %s: %d articles in %.1fs -> %s",
                skipped + index, total, (skipped + index) / total * 100,
                ticker, ", ".join(domains), result_count, time.monotonic() - started, self.csv_path,
            )
            time.sleep(self.request_delay_seconds)
        return self.items

    def run(self, retry_failed: bool = True) -> list[NewsItem]:
        """Run one date range, writing RSS redirect links immediately."""
        self._current_window = None
        try:
            return self._run_window(self.start_date, self.end_date, retry_failed)
        except KeyboardInterrupt:
            self._save_state()
            self.logger.warning("Interrupted. State and completed-query CSV rows are saved.")
            raise
        finally:
            self._save_state()

    def _generate_windows(self) -> Iterator[tuple[datetime, datetime]]:
        if not self.window_days:
            yield self.start_date, self.end_date
            return
        current = self.start_date
        delta = timedelta(days=self.window_days)
        while current < self.end_date:
            window_end = min(current + delta, self.end_date)
            yield current, window_end
            current = window_end

    @staticmethod
    def _window_label(start: datetime, end: datetime) -> str:
        return f"{start.date()}_to_{end.date()}"

    def run_rolling(self, retry_failed: bool = True) -> list[NewsItem]:
        """Run all date windows while keeping output in one identifier directory."""
        if not self.window_days:
            raise ValueError("run_rolling() requires window_days")
        self.items = []
        try:
            for start, end in self._generate_windows():
                self._current_window = self._window_label(start, end)
                self.logger.info("=== Window %s ===", self._current_window)
                self._run_window(start, end, retry_failed)
            return self.items
        except KeyboardInterrupt:
            self._save_state()
            self.logger.warning("Interrupted. State and completed-query CSV rows are saved.")
            raise
        finally:
            self._save_state()

    def progress(self) -> dict:
        """Return progress for the active window (or the single-window run)."""
        total = len(self.companies) * sum(1 for _ in self._batched_domains())
        current = [entry for entry in self.state.values() if entry.get("window") == self._current_window]
        done = sum(entry.get("status") == "done" for entry in current)
        failed = sum(entry.get("status") == "failed" for entry in current)
        return {
            "total_queries": total,
            "done": done,
            "failed": failed,
            "remaining": total - done - failed,
            "pct_complete": round(done / total * 100, 1) if total else 0.0,
            "output_dir": self.output_dir,
        }

    def progress_all_windows(self) -> dict:
        """Return compact aggregate progress for a rolling run."""
        windows = list(self._generate_windows())
        per_window_total = len(self.companies) * sum(1 for _ in self._batched_domains())
        total_done = total_failed = 0
        for start, end in windows:
            label = self._window_label(start, end)
            entries = [entry for entry in self.state.values() if entry.get("window") == label]
            done = sum(entry.get("status") == "done" for entry in entries)
            failed = sum(entry.get("status") == "failed" for entry in entries)
            total_done += done
            total_failed += failed

        total_queries = per_window_total * len(windows)
        return {
            "total_windows": len(windows),
            "total_queries": total_queries,
            "done": total_done,
            "failed": total_failed,
            "remaining": total_queries - total_done - total_failed,
            "pct_complete": round(total_done / total_queries * 100, 1) if total_queries else 0.0,
            "output_dir": self.output_dir,
            "log_path": self.log_path,
        }

    def urls(self) -> list[str]:
        """Links found in this Python session; these may be Google redirects."""
        return [item.url for item in self.items]

    def to_pandas(self, from_disk: bool = True) -> pd.DataFrame:
        if from_disk and os.path.exists(self.csv_path):
            return pd.read_csv(self.csv_path)
        return pd.DataFrame.from_records(asdict(item) for item in self.items)

    def to_pandas_all_windows(self) -> pd.DataFrame:
        """Alias for the shared rolling-output CSV."""
        return self.to_pandas(from_disk=True)
