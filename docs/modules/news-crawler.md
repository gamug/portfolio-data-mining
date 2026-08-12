# extractor (news_crawler) — pipeline stage 2: full-text extraction

**Source:** `news-crawler/` → `src/extractor/` + `apps/news_crawler_extract.py` (CLI) +
`apps/news_crawler_api.py` (dev/test API) + `tests/extractor/`

> Kept as the `extractor` package name (not renamed to `news_crawler`) — its internal
> modules use absolute imports like `from extractor.parse import ...`, and preserving the
> name avoided rewriting every import for zero behavioral gain. `apps/news_crawler_*.py`
> is the clearly-named entrypoint layer on top of it.

Turns [news_collector](news-collector.md)'s queue of discovered URLs into a clean,
structured dataset of full article text + metadata: reads `discovered_urls` rows with
`status='pending'` from the shared `data/urls.db`, fetches each with `httpx`, extracts
title/author/pub_date/body via JSON-LD + `trafilatura`, classifies the result
(`ok` / `paywalled` / `thin_content` / `failed`), and writes a matching row to `articles`
(same `id` as the source `discovered_urls` row, enforced as a `FOREIGN KEY`).

`cnbc.com` is ~86% of the discovered corpus and gets its own concurrency budget
(`src/extractor/scheduler.py`'s `DomainScheduler`) so it doesn't starve every other
domain. No checkpoint file — every row commits to SQLite immediately, so stopping and
re-running picks up exactly where it left off.

## Running

```bash
# Batch/unattended (this is what actually drives a full run)
.venv\Scripts\python.exe apps\news_crawler_extract.py --limit 20

# Dev/test API — inspect DB state, re-extract one URL, preview a parse — NOT for
# production/public exposure (no auth, some endpoints mutate the DB)
.venv\Scripts\python.exe apps\news_crawler_api.py
# -> http://127.0.0.1:8002/docs
```

## Testing

```bash
.venv\Scripts\python.exe -m pytest tests/extractor -q
```

33/33 pass, no known issues.
