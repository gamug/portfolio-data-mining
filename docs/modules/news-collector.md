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

- **Company matching** (`src/data_mining/matching.py`, reached through
  `BaseConnector.classify_match`) — one shared rule for all seven connectors that decides whether a
  `(url, ticker)` pair is about the company. It reads only the URL path/query and the title and returns
  `accept` (the full normalized name or an alias, an explicit ticker form, or an upper-case non-word ticker
  in the title), `review` (only weaker evidence, such as a distinctive first word, never excluded) or
  `reject`. `url_matches_company` is `classify_match != "reject"`. A ticker never matches as a raw URL
  substring, the host is never matched, and an ordinary-word ticker (a cashtag `$ON` is `review`, an exchange form `NYSE: ON` is `accept`), a generic first word or an abbreviation
  alone is never `accept`. The class is stored on `discovered_urls` (`T-056b`, below), but the
  strategies do not set it until `T-056c`: until then `review` pairs are kept like `accept` ones and
  only `reject` pairs are dropped at discovery. **Do not feed a discovery run made before `T-056c` to
  `portfolio-nlp`**: its `review` rows (about 63k) are stored as `accept` and would enter the default
  selection. Design and evidence: `PLAN.md` Work item 10.

**Matching columns on `discovered_urls`** (schema version 2, `data_mining/schema.py`; constitution AI
behavior #10 confirmed 2026-10-15): `match_class` (`accept`/`review`/`reject`, `NOT NULL DEFAULT
'accept'`, indexed by `idx_match_class`), `match_reason` (the deciding signal) and `query` (the `ddg`
search query that returned the row; provenance only, it never changes the class). A matcher-rejected
pair is stored **`status = 'skipped' AND match_class = 'reject'`** at insert, its `title` untouched, so
the extractor's and the queue's `status = 'pending'` selection never returns it; `mark_skipped` (paywall,
irrelevant after inspection) keeps its meaning and leaves `match_class` alone. `enqueue_batch` no longer
just ignores a duplicate `(url, ticker)`: a rediscovery **promotes** the stored row (it keeps the higher
class `reject` < `review` < `accept`, takes the new `match_reason` with a higher class, fills a null
`match_reason`/`query`, and a row stored `reject` goes back to `pending` when its class rises). It never
demotes. Existing databases are upgraded by `run_migrations` (their rows default to `accept`; the
`T-057` re-tag reclassifies them). Both read APIs expose the class: the collector's
`/discovery/query` and `/discovery/pending` return `match_class`/`match_reason` and `/discovery/query`
filters on `match_class`; the crawler's `/discovered` does the same. The `status` filter's `skipped`
includes matcher-rejected rows, so use `by_match_class` (in `queue.stats()`, the collector's
`/discovery/stats` and the CLI `stats`) to tell them from paywall skips. `match_class` has a `CHECK`
constraint and `enqueue_batch` raises `ValueError` before writing for any other value. The extractor's
`get_urls_by_status` never returns a `reject` row, whatever status is asked for, and the crawler's
`POST /discovered/{id}/reset` answers 409 for one.

**Repeatable migration check** (run before any re-tag, on a copy, never the source):
```bash
cp data/urls.db /tmp/urls_copy.db
PYTHONPATH=src uv run python - <<'PY'
import sqlite3
from news_collector.storage.queue import URLQueue
p = "/tmp/urls_copy.db"
before = sqlite3.connect(p).execute("SELECT COUNT(*) FROM discovered_urls").fetchone()[0]
q = URLQueue(p); q.initialize()
print(before, q._conn.execute(
    "SELECT COUNT(*), SUM(match_class='accept'), (SELECT COUNT(*) FROM sqlite_master"
    " WHERE name='idx_match_class'), (SELECT * FROM pragma_user_version) FROM discovered_urls").fetchone()[:])
PY
```
Expect the same row count before and after, every row `accept`, the index present and user_version 2
(the 481,332-row corpus gave exactly that on 2026-10-15).

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

## Auditing ticker tagging

`scripts/audit_ticker_tagging.py` is a read-only audit of `urls.db` (default `$DATABASE_URL`,
else `data/urls.db`). It classifies every `discovered_urls` row by how the current matching
rule accepts it (ticker only / company token only / both / raw-substring shortcut only) and
breaks the English-word tickers and generic first words out per connector and source:

```bash
PYTHONPATH=src uv run python scripts/audit_ticker_tagging.py [--json] [--min-rows N] [--generic-share F]
```

`PYTHONPATH=src` is required because `scripts/` is not a package and `src/` is not on
`sys.path` by default (pytest gets it from `pytest.ini`). This section uses `uv run`, the
constitution's convention; the Windows `.venv\Scripts\python.exe` commands above predate it.
The script opens `urls.db` through `data_mining.db.connect` as a `mode=ro` URI, so it stays
inside the engine seam (NR-002/NR-006).

Background and results: `PLAN.md` Work item 10 (`T-053`).

`scripts/ticker_tagging_sample.py` (`T-054`) draws the shared labeled sample of `(url, ticker)`
pairs (`sample`) and reports wrong-company rates and known-correct retention from the labeled CSV
(`report`). The committed sample is `data/ticker_tagging_sample.csv`, intended for reuse by
`portfolio-nlp` (its plan cites it as `T-117`). Labels are `yes`/`no`/`unclear` with `labeled_by` =
`hand` or `assistant`. `manual-export` writes a new blank sheet for a person to fill (title, body excerpt, empty `label`;
default `data/ticker_tagging_manual_new.csv`, git-ignored) and refuses to overwrite an existing file
without `--force`. `manual-apply` merges a filled sheet back and prints the agreement with the
assistant's labels; it reads the committed sheet by default, so apply a new one explicitly:
`ticker_tagging_sample.py manual-export` then, after labeling,
`ticker_tagging_sample.py manual-apply --manual data/ticker_tagging_manual_new.csv`. The committed
`data/ticker_tagging_manual.csv` is the **filled** sheet (the maintainer's labels and notes, with the
excerpt column removed). `scripts/score_ticker_rules.py` scores the prototype matching rules on the
labeled sample (`--exclude-ids` to leave rows out). `scripts/ticker_validation_sample.py` draws a fresh
validation sample (strata `accept`/`review`/`reject` by source, excluding already-labeled pairs) in the same CSV format (`ticker_tagging_sample.py report --sample data/ticker_validation_sample.csv` reproduces the first-draft out-of-sample result, since the CSV's `match_mode` is the class the first draft assigned when it was drawn; `score_ticker_rules.py --sample` scores the current rule (`--sizes` prints its exact class sizes over the whole corpus, `--diff` the rows moved by the host fix, the abbreviation list and the alias map, `--check` the figures of both committed samples in both modes against the PLAN table (exit 1 on a difference; needs the local corpus, not run by pytest), `--names [--top N]` the rows accepted on a one-word company name alone (the `N` largest companies, default 25); both are slow, read-only scans); the committed `data/ticker_validation_manual.csv` is the filled sheet without excerpts).

## Testing

```bash
.venv\Scripts\python.exe -m pytest tests/news_collector -q
```

99/100 pass; `test_enqueue_inserts_at_most_len_input` is a pre-existing Windows-only flake
(a hypothesis-generated property test races a temp-SQLite-file cleanup against an open
connection — `PermissionError: file in use`, not a logic bug in this codebase).
