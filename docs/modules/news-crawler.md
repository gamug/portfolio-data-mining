# extractor (news_crawler) — pipeline stage 2: full-text extraction

**Source:** `news-crawler/` → `src/extractor/` + `apps/news_crawler_extract.py` (CLI) +
`apps/news_crawler_api.py` (dev/test API) + `tests/extractor/`

> Kept as the `extractor` package name (not renamed to `news_crawler`) — its internal
> modules use absolute imports like `from extractor.parse import ...`, and preserving the
> name avoided rewriting every import for zero behavioral gain. `apps/news_crawler_*.py`
> is the clearly-named entrypoint layer on top of it.

Turns [news_collector](news-collector.md)'s queue of discovered URLs into a clean,
structured dataset of full article text + metadata: reads `discovered_urls` rows with
`status='pending'` (never a matcher-rejected pair, see below) from the shared `data/urls.db` (override with `$DATABASE_URL`, see
root `.env.example`), fetches each with `httpx`, extracts
title/author/pub_date/body via JSON-LD + `trafilatura`, classifies the result
(`ok` / `paywalled` / `thin_content` / `failed`; a `review`/`reject` pair is then set to `quarantined`, see below), and writes a matching row to `articles`
(same `id` as the source `discovered_urls` row, enforced as a `FOREIGN KEY`).

`cnbc.com` is ~86% of the discovered corpus and gets its own concurrency budget
(`src/extractor/scheduler.py`'s `DomainScheduler`) so it doesn't starve every other
domain. No checkpoint file — every row commits to SQLite immediately, so stopping and
re-running picks up exactly where it left off.

## Running

```bash
# Batch/unattended (this is what actually drives a full run) — direct, no server
.venv\Scripts\python.exe cli\news_crawler_cli.py --limit 20

# Retry URLs that previously came back status='failed' (http_status>=400 or a
# network error) instead of processing 'pending' rows — no separate reset step
.venv\Scripts\python.exe cli\news_crawler_cli.py --retry-failed

# Dev/test API — inspect DB state, re-extract one URL, preview a parse — NOT for
# production/public exposure (no auth, some endpoints mutate the DB)
.venv\Scripts\python.exe apps\news_crawler_api.py
# -> http://127.0.0.1:8002/docs
```

`get_urls_by_status()` (`src/extractor/db.py`) backs both the CLI's `--retry-failed` flag
and the API's `POST /extract/run?status=failed` — `get_pending_urls()` is now a thin
wrapper over it (`status IN ('pending')`). Only `failed` rows are worth retrying this way;
`paywalled`/`thin_content` reflect the page's actual content, not a fetch error, so
re-running them produces the same result.

A body that is a wall rather than an article (`extractor.parse.wall_kind`, T-060) is classified from the first 300
characters: a bot challenge ("Just a moment", "Security Verification", ...) is `failed` (transient, so
`--retry-failed` retries it, and one that never clears is retried on every such run), a login or subscribe
prompt is `paywalled` (gated, not retried). "Just a moment" and "Access denied" count only at the very start of the body (as whole words), so "Please wait. Just a moment..." is not caught; the other phrases count anywhere in those 300 characters. A phrase deeper in an article's prose is not a wall. Walls are
read with HTTP 200, so `failed` with `http_status_code` 200 means a challenge page, not a network error.

`cli/news_crawler_cli.py` (was `news-crawler/run_extraction.py`, then briefly
`apps/news_crawler_extract.py` during the initial consolidation) lives in `cli/` rather
than `apps/` since it never raises a server — that distinction is what `cli/` vs `apps/`
means across every service in this repo.

## Matcher-rejected pairs

`news_collector` classes every `(url, ticker)` pair `accept` / `review` / `reject` (see the
"Matching columns" section of [news-collector.md](news-collector.md)); a rejected pair is stored
`status = 'skipped' AND match_class = 'reject'` and is never extracted. What that changes here:

- `get_urls_by_status()` and `get_pending_urls()` never return a `reject` row, **whatever status is
  asked for**: `POST /extract/run?status=skipped` returns paywall skips, not rejected pairs.
- `POST /extract/{id}` answers **409** for a rejected row too (it runs "regardless of status", but a
  rejected pair must never get an `articles` row: that is what `portfolio-nlp` reads).
- `POST /discovered/{id}/reset` answers **409** for a rejected row (it is promoted by rediscovery or
  by the re-tag decision, not reset); `review` and `accept` rows reset as before.
- `GET /discovered` and `GET /discovered/{id}` return `match_class` and `match_reason`, and the list
  filters on `match_class`; the `status` filter's `skipped` includes rejected rows.
- `get_status_counts()` and `GET /status` leave rejected rows out of `counts`/`total` (they were never
  work, so they must not read as processed); `GET /status` adds `by_match_class` and
  `get_match_class_counts()` gives the same numbers.
- The CLI start-up line counts only non-rejected rows as "Already processed" and prints
  "Matcher-rejected, not fetched: N" separately when N is above zero.

## Quarantined articles

An article whose pair is not `accept` has `fetch_status = 'quarantined'` (`data_mining.quarantine`, T-057), with
its previous status kept in `articles.prev_fetch_status`; `record_fetch` applies it after every fetch, and a
promotion to `accept` restores it. So `GET /articles?fetch_status=quarantined` and `GET /articles/{id}` return
these rows (the body is kept, nothing is deleted), while `/extract/run` counts and the `/extract/{id}` response report the extractor's own verdict
(`ok`, ...) because the quarantine is applied after it. `prev_fetch_status` is restore bookkeeping and is
deliberately **not** in the `Article` response model.

## Post-fetch re-evaluation

After every fetch the CLI and both extract endpoints call `extractor.db.record_fetch`: save the article,
set the row's status, and, when the page was read (any `fetch_status` but `failed`: `ok`, `paywalled`, `thin_content`), `reevaluate_match` re-classes
the pair with `data_mining.matching` using the stored and the fetched title. It only promotes
(`review`→`accept`), is guarded in SQL on the class that was read, skips `reject` rows, and commits once
per promoted row on purpose (the collector writes the same table).

## Testing

```bash
.venv\Scripts\python.exe -m pytest tests/extractor -q
```

55 pass at the time of writing (2026-10-15), no known issues.
