# news_collector — pipeline stage 1: URL discovery

**Source:** `news-collector/` → `src/news_collector/` + `apps/news_collector_api.py` + `tests/news_collector/`

Hybrid news-link discovery for S&P 500 companies. Given a list of tickers (or none — the
entire S&P 500) and a date range, it discovers article **URLs** (not full article content)
across seven target financial-news domains (CNBC, Yahoo Finance, Financial Times,
Investing.com, Nasdaq, Seeking Alpha, StockTwits) and persists them to a durable SQLite
queue (`data/urls.db` by default — override with `$DATABASE_URL`, see root
`.env.example`; table `discovered_urls`) for [news_crawler](news-crawler.md) to consume.

## Architecture

- **Connector-per-domain** (`src/news_collector/connectors/`) — one class per news source.
- **Two discovery strategies** (`src/news_collector/strategies/`) — sitemap crawling and
  DuckDuckGo search, picked per-domain.
- **Orchestrator** (`src/news_collector/orchestrator.py`) — fans out across
  tickers/domains, tracks completed `(ticker, date)` pairs for resumable runs.
- **Persistence** (`src/news_collector/storage/queue.py`) — SQLite (WAL mode), safe for
  one writer + concurrent readers, so downstream stages can read while this stage writes.

Every row in `discovered_urls` carries a stable `id` primary key — the crawler holds it as
a foreign key back to this table, rather than re-matching on the `url` string.

## Running

```bash
# CLI (direct — no server; see src/news_collector/main.py for the full flag list)
.venv\Scripts\python.exe cli\news_collector_cli.py discover --tickers AAPL MSFT
.venv\Scripts\python.exe cli\news_collector_cli.py stats
.venv\Scripts\python.exe cli\news_collector_cli.py --help

# API
.venv\Scripts\python.exe apps\news_collector_api.py
# -> http://127.0.0.1:8001/docs
```

`cli/news_collector_cli.py` is a thin wrapper around `news_collector.main.main()`, which
already had a full argparse CLI (discover/stats/export subcommands) — it's still directly
runnable as `python -m news_collector.main ...` too; the `cli/` script just gives every
service one consistent, discoverable place to look for its non-server entrypoint.

## Testing

```bash
.venv\Scripts\python.exe -m pytest tests/news_collector -q
```

99/100 pass; `test_enqueue_inserts_at_most_len_input` is a pre-existing Windows-only flake
(a hypothesis-generated property test races a temp-SQLite-file cleanup against an open
connection — `PermissionError: file in use`, not a logic bug in this codebase).
