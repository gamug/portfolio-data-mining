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

## New S&P 500 members backfill automatically (no special-casing needed)

The universe is mutable (see `common.universe_history`, [pricing.md](pricing.md)): a ticker
can join the tracked S&P 500 at any time, and until `discover` is re-run for it, it simply
has zero rows in `discovered_urls`/`articles` — a real gap, not a bug, if nobody re-runs the
pipeline.

The fix needs no code, because of how `discover` already behaves by default: with neither
`--tickers` nor `--sp500` given, it fetches the **live, current** Wikipedia S&P 500 list
every time (`news_collector.sp500.fetch_sp500_companies`) and requests the **full**
`$DISCOVERY_START_DATE`–`$DISCOVERY_END_DATE` range for every ticker in it — never an
incremental tail. `--resume` (on by default) only skips a `(ticker, domain)` pair that
already has a `discovery_progress` row for that *exact* date range (`orchestrator.py`); a
ticker that just joined the universe has no such row, so it gets the full historical range
on the very next run, exactly like every other ticker did on its own first run — same
treatment, no "is this ticker new?" branch anywhere.

This mirrors the pattern `portfolio-financial-analysis`'s `pricing_agent`/`fundamental_agent`
use for the same problem (mutable universe, per-agent price/filing data): *"One `run` makes
a request per ticker for the whole date range... windows already stored are skipped, so
re-runs resume"* — full universe × full range × idempotent skip, every invocation, so a new
member is never silently under-covered. Operationally here: after `pricing_cli.py
universe-snapshot` reports an `added` ticker, just re-run `news_collector discover` (default
args) — it will pick up that ticker's full backlog on its own.

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
