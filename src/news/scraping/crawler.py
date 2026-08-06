"""
Bulk crawler: takes a large list of URLs, splits them into chunks,
crawls each chunk concurrently with crawl4ai, and exports the resulting
markdown to:

    os.path.join(general['markdown'], date_identifier, file_name)

Usage (as a plain script, NOT inside Jupyter on Windows — see notes at bottom):

    from bulk_crawler import BulkCrawler

    urls = [...]  # thousands of urls
    crawler = BulkCrawler(chunk_size=50, max_concurrent=10)
    asyncio.run(crawler.run(urls))
"""

import asyncio, hashlib, json, logging, os, re, tempfile
from datetime import datetime, timezone
from urllib.parse import urlparse
from typing import Any

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig
from crawl4ai.async_dispatcher import MemoryAdaptiveDispatcher

from src.config import general

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("bulk_crawler")


class BulkCrawler:
    """
    Splits a large list of URLs into chunks, crawls each chunk concurrently,
    and writes each successful result to disk as markdown.

    Directory layout:
        general['markdown']/<date_identifier>/<file_name>.md

    file_name is derived from the URL itself (domain + slugified path),
    with a short hash suffix to guarantee uniqueness even when two URLs
    would otherwise slugify to the same string.
    """

    def __init__(
        self,
        chunk_size: int = 50,
        max_concurrent: int = 10,
        memory_threshold_percent: float = 80.0,
        date_identifier: str | None = None,
        run_config: CrawlerRunConfig | None = None,
    ):
        self.chunk_size = chunk_size
        self.max_concurrent = max_concurrent
        self.memory_threshold_percent = memory_threshold_percent
        # One shared date_identifier per BulkCrawler run, so all chunks in
        # a single call land in the same output folder.
        self.date_identifier = date_identifier or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.run_config = run_config

        self.output_dir = os.path.join(general["paths"]["news_markdown"], self.date_identifier)
        os.makedirs(self.output_dir, exist_ok=True)

        # Track filenames used so far in this run, in case two URLs slugify
        # identically (rare, but the hash suffix should already prevent it —
        # this is just a belt-and-suspenders check).
        self._used_filenames: set[str] = set()

        self.results_summary: list[dict] = []

        # ---- Resumability -------------------------------------------- #
        # One state file per date_identifier folder. Keyed by url_hash so
        # it's stable across runs regardless of order/casing/dupes.
        self.state_path = os.path.join(self.output_dir, "_crawl_state.json")
        self.state: dict[str, dict] = self._load_state()

    # ------------------------------------------------------------------ #
    # State persistence
    # ------------------------------------------------------------------ #
    @staticmethod
    def _url_key(url: str) -> str:
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

    def _load_state(self) -> dict:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                done = sum(1 for v in data.values() if v.get("status") == "done")
                logger.info(
                    f"Resuming from existing state file: {self.state_path} "
                    f"({done}/{len(data)} previously marked done)"
                )
                return data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Could not read state file ({e}), starting fresh.")
        return {}

    def _save_state(self):
        """Atomic write so a crash/interrupt mid-write never corrupts the file."""
        dir_ = os.path.dirname(self.state_path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix="_crawl_state_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp_path, self.state_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def _mark(self, url: str, status: str, **extra):
        key = self._url_key(url)
        entry = {"url": url, "status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
        entry.update(extra)
        self.state[key] = entry

    def _is_done(self, url: str) -> bool:
        entry = self.state.get(self._url_key(url))
        return bool(entry and entry.get("status") == "done")

    # ------------------------------------------------------------------ #
    # Filename generation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _slugify(text: str, max_len: int = 80) -> str:
        text = text.strip().lower()
        text = re.sub(r"[^a-z0-9]+", "-", text)
        text = re.sub(r"-{2,}", "-", text).strip("-")
        return text[:max_len].rstrip("-") or "untitled"

    def _make_filename(self, url: str) -> str:
        """
        Build a readable, collision-safe filename from a URL.

        e.g. https://www.cnbc.com/2026/08/01/traders-go-full-time.html
             -> cnbc.com__2026-08-01-traders-go-full-time__a1b2c3d4.md
        """
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")
        path_slug = self._slugify(parsed.path.rsplit(".", 1)[0])  # drop extension like .html
        url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]

        base = f"{domain}__{path_slug}__{url_hash}"
        filename = f"{base}.md"

        # Extremely defensive uniqueness guard
        counter = 1
        while filename in self._used_filenames:
            filename = f"{base}-{counter}.md"
            counter += 1

        self._used_filenames.add(filename)
        return filename

    # ------------------------------------------------------------------ #
    # Chunking
    # ------------------------------------------------------------------ #
    @staticmethod
    def _chunk(urls: list[str], size: int):
        for i in range(0, len(urls), size):
            yield urls[i : i + size]

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    def _export_markdown(self, url: str, markdown: str) -> Any:
        filename = self._make_filename(url)
        filepath = os.path.join(self.output_dir, filename)

        header = f"<!-- source_url: {url} -->\n<!-- crawled_at: {datetime.now(timezone.utc).isoformat()} -->\n\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(header + markdown)

        return filepath

    # ------------------------------------------------------------------ #
    # Core crawl logic
    # ------------------------------------------------------------------ #
    async def _crawl_chunk(self, crawler: AsyncWebCrawler, urls: list[str], chunk_index: int):
        logger.info(f"Chunk {chunk_index}: crawling {len(urls)} URLs...")

        dispatcher = MemoryAdaptiveDispatcher(
            max_session_permit=self.max_concurrent,
            memory_threshold_percent=self.memory_threshold_percent,
        )

        kwargs = {"urls": urls, "dispatcher": dispatcher}
        if self.run_config is not None:
            kwargs["config"] = self.run_config

        results = await crawler.arun_many(**kwargs)

        for result in results:
            if result.success and result.markdown:
                filepath = self._export_markdown(result.url, result.markdown)
                logger.info(f"  OK   {result.url} -> {filepath}")
                self.results_summary.append(
                    {"url": result.url, "success": True, "path": filepath}
                )
                self._mark(result.url, "done", path=filepath)
            else:
                error = getattr(result, "error_message", "unknown error")
                logger.warning(f"  FAIL {result.url} -> {error}")
                self.results_summary.append(
                    {"url": result.url, "success": False, "error": error}
                )
                # "failed" (not "done") so a future run retries it automatically.
                self._mark(result.url, "failed", error=str(error))

        # Persist after every chunk, not just at the very end, so an
        # interrupt (Ctrl+C, crash, kill) never loses more than one
        # chunk's worth of progress.
        self._save_state()

    async def run(self, urls: list[str], retry_failed: bool = True):
        """
        Entry point: crawl all URLs (in chunks) and export markdown files.

        Resumable: URLs already marked "done" in the state file are skipped.
        By default, URLs previously marked "failed" are retried; pass
        retry_failed=False to skip those too and only crawl brand-new URLs.

        Safe to interrupt (Ctrl+C) between chunks — state is saved after
        every chunk, so re-running with the same `date_identifier` picks up
        where it left off.

        Returns a summary list of dicts: {url, success, path|error}.
        """
        # Dedup while preserving order, and figure out what's actually left to do.
        seen = set()
        deduped = [u for u in urls if not (u in seen or seen.add(u))]

        pending = []
        skipped = 0
        for u in deduped:
            if self._is_done(u):
                skipped += 1
                continue
            if not retry_failed and self.state.get(self._url_key(u), {}).get("status") == "failed":
                skipped += 1
                continue
            pending.append(u)

        chunks = list(self._chunk(pending, self.chunk_size))
        logger.info(
            f"Bulk crawl: {len(deduped)} URLs total, {skipped} already handled/skipped, "
            f"{len(pending)} pending in {len(chunks)} chunks "
            f"(chunk_size={self.chunk_size}, max_concurrent={self.max_concurrent}) "
            f"-> {self.output_dir}"
        )

        if not pending:
            logger.info("Nothing to do — all URLs already completed.")
            return self.results_summary

        try:
            browser_config = BrowserConfig(
                headless=True,
                browser_type="chromium",
                # this is the key one — patches automation fingerprints
                extra_args=["--disable-blink-features=AutomationControlled"],
            )
            async with AsyncWebCrawler(config=browser_config) as crawler:
                for i, chunk in enumerate(chunks, start=1):
                    for u in chunk:
                        self._mark(u, "in_progress")
                    await self._crawl_chunk(crawler, chunk, i)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.warning("Interrupted — progress up to the last completed chunk is saved.")
            self._save_state()
            raise
        except Exception:
            logger.exception("Unexpected error — saving state before re-raising.")
            self._save_state()
            raise

        ok = sum(1 for r in self.results_summary if r["success"])
        fail = len(self.results_summary) - ok
        logger.info(f"Done. {ok} succeeded, {fail} failed. Output dir: {self.output_dir}")

        return self.results_summary

    def status(self) -> dict:
        """Quick counts of done/failed/in_progress URLs in the current state file."""
        counts = {"done": 0, "failed": 0, "in_progress": 0}
        for entry in self.state.values():
            counts[entry.get("status", "in_progress")] = counts.get(entry.get("status"), 0) + 1 # type: ignore
        return counts


# ---------------------------------------------------------------------- #
# Example usage
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    example_urls = [
        "https://www.cnbc.com/2026/08/01/traders-go-full-time-on-prediction-markets-using-ai-bots-and-antennas.html",
        # ... thousands more
    ]

    # IMPORTANT for resuming later: pass the SAME date_identifier explicitly.
    # If you let it default to "today", a run that spans midnight (or one you
    # restart the next day) will look in a different folder and think
    # everything is new again.
    crawler = BulkCrawler(
        chunk_size=50,
        max_concurrent=10,
        date_identifier="2026-08-05-news-batch-1",  # pick your own stable id
    )

    print("Current progress:", crawler.status())

    # Ctrl+C at any point is safe — state is flushed to disk after every chunk.
    # Just re-run this same script (same date_identifier) to pick up where
    # you left off; already-"done" URLs are skipped automatically.
    summary = asyncio.run(crawler.run(example_urls))