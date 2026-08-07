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

Notes on Google News links
---------------------------
`news.google.com/rss/articles/<id>` links are NOT normal redirects. The page
that loads there is a JS-driven interstitial that calls an internal Google
"batchexecute" endpoint (using signed params embedded in the page) to figure
out the real destination. A headless browser just renders that interstitial
shell — there's no article content to extract, which is why crawl4ai was
writing out effectively-empty markdown for these links.

To fix this, BulkCrawler now resolves Google News links to their real target
URL *before* handing anything to crawl4ai, so the crawler is pointed at the
actual article (e.g. stocktwits.com/...) instead of the google.com wrapper.
This relies on scraping some internal Google News page attributes and
calling their batchexecute endpoint — it's not a public API, so it can break
if Google changes the page format. If that ever happens, swap
`_resolve_google_news_url` for the `googlenewsdecoder` PyPI package, which
implements the same technique and is more likely to get updated for you.
"""

import asyncio, hashlib, json, logging, os, random, re, tempfile, time
from datetime import datetime, timezone
from urllib.parse import urlparse
from typing import Any

import requests

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig
from crawl4ai.async_dispatcher import MemoryAdaptiveDispatcher

from src.config import general

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("bulk_crawler")

GOOGLE_NEWS_HOST = "news.google.com"

# A plain `requests` default User-Agent (python-requests/x.y.z) is an
# instant giveaway to Google's bot detection and gets you bounced to a
# /sorry/ captcha page (429). Look like an ordinary browser instead.
GOOGLE_NEWS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class BulkCrawler:
    """
    Splits a large list of URLs into chunks, crawls each chunk concurrently,
    and writes each successful result to disk as markdown.

    Directory layout:
        general['markdown']/<date_identifier>/<file_name>.md

    file_name is derived from the URL itself (domain + slugified path),
    with a short hash suffix to guarantee uniqueness even when two URLs
    would otherwise slugify to the same string.

    Google News RSS links (news.google.com/rss/articles/...) are resolved
    to their real target URL before crawling — see module docstring.
    """

    def __init__(
        self,
        chunk_size: int = 50,
        max_concurrent: int = 10,
        memory_threshold_percent: float = 80.0,
        date_identifier: str | None = None,
        run_config: CrawlerRunConfig | None = None,
        google_news_resolve_concurrency: int = 1,
        google_news_resolve_timeout: int = 10,
        google_news_resolve_delay: tuple[float, float] = (1.5, 3.5),
    ):
        self.chunk_size = chunk_size
        self.max_concurrent = max_concurrent
        self.memory_threshold_percent = memory_threshold_percent
        # One shared date_identifier per BulkCrawler run, so all chunks in
        # a single call land in the same output folder.
        self.date_identifier = date_identifier or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.run_config = run_config

        # Kept deliberately lower than max_concurrent by default — this hits
        # an unofficial Google endpoint, so we're gentler about it than we
        # are with the actual target sites. Serialized (concurrency=1) with
        # a randomized delay between requests by default, since Google's
        # bot detection triggers fast on a burst of simultaneous requests.
        self.google_news_resolve_concurrency = google_news_resolve_concurrency
        self.google_news_resolve_timeout = google_news_resolve_timeout
        self.google_news_resolve_delay = google_news_resolve_delay

        # One session (with a browser-like User-Agent) reused across all
        # Google News resolutions in this run, so cookies set by the first
        # interstitial-page GET are sent along on later requests too.
        self._gn_session = requests.Session()
        self._gn_session.headers.update(GOOGLE_NEWS_HEADERS)

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
        # NOTE: state is always keyed on the ORIGINAL url passed to `run()`
        # (e.g. the news.google.com link), never the resolved target — that
        # way resuming a run doesn't depend on Google's resolution being
        # deterministic across attempts.
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
    # Google News link resolution
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_google_news_link(url: str) -> bool:
        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        if parsed.hostname != GOOGLE_NEWS_HOST:
            return False
        parts = [p for p in parsed.path.split("/") if p]
        return len(parts) >= 2 and parts[-2] in ("articles", "read")

    def _gn_request(self, method: str, url: str, max_retries: int = 3, **kwargs) -> requests.Response:
        """
        requests.request wrapper with retry/backoff specifically for
        Google's 429 "sorry" wall. Respects a Retry-After header if Google
        sends one; otherwise backs off with a growing, jittered delay.
        """
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            resp = self._gn_session.request(method, url, timeout=self.google_news_resolve_timeout, **kwargs)
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp

            last_exc = requests.HTTPError(f"429 Too Many Requests for url: {resp.url}", response=resp)
            if attempt == max_retries:
                break
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else (2 ** attempt) + random.uniform(1, 3)
            logger.warning(f"    429 from Google, backing off {wait:.1f}s (attempt {attempt + 1}/{max_retries})...")
            time.sleep(wait)
        raise last_exc

    def _resolve_google_news_url(self, url: str) -> str:
        """
        Resolve a news.google.com/rss/articles/<id> (or /read/<id>) link to
        the real article URL it ultimately points at.

        This mirrors what the page's own JS does: it pulls three signed
        attributes (data-n-a-sg / data-n-a-ts / data-n-a-id) out of the
        interstitial HTML, then POSTs them to Google's internal batchexecute
        endpoint, which hands back the real target URL. Raises ValueError /
        requests.RequestException on failure — callers should catch and
        treat as a resolution failure for that URL.
        """
        resp = self._gn_request("GET", url)

        sig_m = re.search(r'data-n-a-sg="([^"]+)"', resp.text)
        ts_m = re.search(r'data-n-a-ts="([^"]+)"', resp.text)
        id_m = re.search(r'data-n-a-id="([^"]+)"', resp.text)
        if not (sig_m and ts_m and id_m):
            raise ValueError("Could not find decoding attributes on Google News interstitial page")

        signature, timestamp, base64_str = sig_m.group(1), ts_m.group(1), id_m.group(1)

        inner_args = json.dumps(
            [
                "garturlreq",
                [
                    ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
                    "X",
                    "X",
                    1,
                    [1, 1, 1],
                    1,
                    1,
                    None,
                    0,
                    0,
                    None,
                    0,
                ],
                base64_str,
                timestamp,
                signature,
            ]
        )
        payload = json.dumps([[["Fbv4je", inner_args, None, "generic"]]])

        batch_resp = self._gn_request(
            "POST",
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            headers={"content-type": "application/x-www-form-urlencoded;charset=UTF-8"},
            data={"f.req": payload},
        )

        # Response is prefixed with an anti-XSSI `)]}'` line; the real
        # payload is the JSON chunk after the first blank line.
        chunks = batch_resp.text.split("\n\n")
        if len(chunks) < 2:
            raise ValueError("Unexpected batchexecute response shape")

        parsed_outer = json.loads(chunks[1])[:-2]
        inner_json = json.loads(parsed_outer[0][2])
        decoded_url = inner_json[1]
        if not decoded_url or not decoded_url.startswith("http"):
            raise ValueError(f"Decoded value doesn't look like a URL: {decoded_url!r}")
        return decoded_url

    async def _resolve_pending_urls(self, urls: list[str]) -> list[tuple[str, str]]:
        """
        Given a list of original URLs, return (original_url, crawl_url)
        pairs. For non-Google-News URLs, crawl_url == original_url. For
        Google News links, crawl_url is the resolved real article URL.

        URLs that fail to resolve are marked "failed" in state (so they're
        retried on a future run, same as a crawl failure) and are dropped
        from the returned list — they don't get passed to crawl4ai at all.
        """
        google_news_urls = [u for u in urls if self._is_google_news_link(u)]
        if google_news_urls:
            logger.info(f"Resolving {len(google_news_urls)} Google News link(s) to their real URLs...")

        sem = asyncio.Semaphore(self.google_news_resolve_concurrency)
        lo, hi = self.google_news_resolve_delay

        async def resolve_one(u: str) -> tuple[str, str | None]:
            if not self._is_google_news_link(u):
                return u, u
            async with sem:
                # Small randomized pause before each request — even at
                # concurrency=1, firing these back-to-back with no gap looks
                # scripted and can still trip Google's rate limiting.
                await asyncio.sleep(random.uniform(lo, hi))
                try:
                    resolved = await asyncio.to_thread(self._resolve_google_news_url, u)
                    logger.info(f"  RESOLVED {u} -> {resolved}")
                    return u, resolved
                except Exception as e:
                    logger.warning(f"  RESOLVE FAIL {u} -> {e}")
                    self._mark(u, "failed", error=f"google_news_resolve: {e}")
                    return u, None

        results = await asyncio.gather(*[resolve_one(u) for u in urls])

        pairs = [(orig, resolved) for orig, resolved in results if resolved is not None]

        if google_news_urls:
            self._save_state()  # persist resolution failures immediately

        return pairs

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

        e.g. https://stocktwits.com/news/some-headline-here
             -> stocktwits.com__news-some-headline-here__a1b2c3d4.md
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
    def _chunk(pairs: list[tuple[str, str]], size: int):
        for i in range(0, len(pairs), size):
            yield pairs[i : i + size]

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    def _export_markdown(self, original_url: str, crawl_url: str, markdown: str) -> Any:
        filename = self._make_filename(crawl_url)
        filepath = os.path.join(self.output_dir, filename)

        header = f"<!-- source_url: {original_url} -->\n"
        if crawl_url != original_url:
            header += f"<!-- resolved_url: {crawl_url} -->\n"
        header += f"<!-- crawled_at: {datetime.now(timezone.utc).isoformat()} -->\n\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(header + markdown)

        return filepath

    # ------------------------------------------------------------------ #
    # Core crawl logic
    # ------------------------------------------------------------------ #
    async def _crawl_chunk(self, crawler: AsyncWebCrawler, pairs: list[tuple[str, str]], chunk_index: int):
        logger.info(f"Chunk {chunk_index}: crawling {len(pairs)} URLs...")

        crawl_urls = [crawl_url for _, crawl_url in pairs]
        original_by_crawl_url = {crawl_url: orig for orig, crawl_url in pairs}

        dispatcher = MemoryAdaptiveDispatcher(
            max_session_permit=self.max_concurrent,
            memory_threshold_percent=self.memory_threshold_percent,
        )

        kwargs = {"urls": crawl_urls, "dispatcher": dispatcher}
        if self.run_config is not None:
            kwargs["config"] = self.run_config

        results = await crawler.arun_many(**kwargs)

        for result in results:
            # arun_many results carry the url that was actually requested;
            # fall back to it directly if for some reason it's not in our map
            # (e.g. the site itself issued a further redirect).
            original_url = original_by_crawl_url.get(result.url, result.url)

            if result.success and result.markdown:
                filepath = self._export_markdown(original_url, result.url, result.markdown)
                logger.info(f"  OK   {original_url} -> {filepath}")
                self.results_summary.append(
                    {"url": original_url, "crawled_url": result.url, "success": True, "path": filepath}
                )
                self._mark(original_url, "done", path=filepath, crawled_url=result.url)
            else:
                error = getattr(result, "error_message", "unknown error")
                logger.warning(f"  FAIL {original_url} -> {error}")
                self.results_summary.append(
                    {"url": original_url, "crawled_url": result.url, "success": False, "error": error}
                )
                # "failed" (not "done") so a future run retries it automatically.
                self._mark(original_url, "failed", error=str(error), crawled_url=result.url)

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

        Google News links (news.google.com/rss/articles/...) are resolved
        to their real target URL before crawling. If resolution fails for a
        given link, it's marked "failed" (retried next run, same as any
        other failure) and skipped for this run.

        Safe to interrupt (Ctrl+C) between chunks — state is saved after
        every chunk, so re-running with the same `date_identifier` picks up
        where it left off.

        Returns a summary list of dicts: {url, crawled_url, success, path|error}.
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

        # Resolve Google News links to real URLs before crawling. Anything
        # that fails to resolve is marked "failed" and dropped from `pairs`.
        pairs = await self._resolve_pending_urls(pending)
        resolve_failures = len(pending) - len(pairs)

        chunks = list(self._chunk(pairs, self.chunk_size))
        logger.info(
            f"Bulk crawl: {len(deduped)} URLs total, {skipped} already handled/skipped, "
            f"{resolve_failures} failed Google News resolution, "
            f"{len(pairs)} pending in {len(chunks)} chunks "
            f"(chunk_size={self.chunk_size}, max_concurrent={self.max_concurrent}) "
            f"-> {self.output_dir}"
        )

        if not pairs:
            logger.info("Nothing to do.")
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
                    for orig, _ in chunk:
                        self._mark(orig, "in_progress")
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
            counts[entry.get("status", "in_progress")] = counts.get(entry.get("status"), 0) + 1  # type: ignore
        return counts


# ---------------------------------------------------------------------- #
# Example usage
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    example_urls = [
        "https://news.google.com/rss/articles/CBMiSkFVX3lxTE56MnJtWFVjT252emdlc2I5ZkVKclJDeC1ud0hORzA4aUpiQWtzYXB4MkJlOElmOE45NjhobGh4V196M1ZVN3Q4eEp3?oc=5",
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