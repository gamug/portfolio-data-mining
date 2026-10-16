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
  alone is never `accept`. The class is stored on `discovered_urls` (`T-056b`, below) and the strategies set it (`T-056c`):
`ddg` and `sitemap`/`rss` no longer drop a pair, they keep `accept`, `review` and `reject` with the class,
the reason and (for `ddg`) the search `query`. `accept` and `review` rows stay `pending` (a `review` pair
needs its body for `portfolio-nlp`'s decision); a `reject` row is stored `skipped` (never fetched).
**Fetch policy:** the class is computed at discovery from the URL and the title (the `ddg` hit title or an
RSS entry title; a plain sitemap entry has none), then **re-evaluated after the fetch** by the extractor
(`extractor.db.reevaluate_match`, called by `extractor.db.record_fetch` in the crawler CLI and API) with the stored
and the fetched title. It is promote-only (`review`→`accept`), guarded in SQL on the class that was read,
and never touches a `reject` row. A sitemap `reject` row without a title stays `reject` (about 5,800
pairs would be `review`/`accept` with a title and are never fetched; the T-057 check measures it).
Rows from the `api` feeds (`stocktwits`, `yahoo_finance`) are `accept` with `match_reason = 'feed'`. The
orchestrator's merge of the strategies keeps the higher class of a pair found twice (and its `query`). The
discovery logs, `DiscoveryStats.total_rejected`, `/discovery/run` (`total_rejected`) and the CLI summary report
`rejected=`: the **newly inserted** rows that are matcher-rejected (`URLQueue.last_inserted_rejected`). They are
part of `inserted` but never fetched, so `inserted - rejected` sizes the new fetch queue (a re-run inserts
0 and reports 0). The run's `by_domain` and `by_company` (and the CLI summary tables) count **inserted** rows, matcher-rejected ones
included, like `queue.stats()['by_domain']`; use `by_match_class` or `total_rejected` to tell how many of them will be fetched.
`multi_company` is derived, not stored: a URL with two or more `accept` pairs. **Rows discovered before
`T-056c` carry no class** (stored `accept`): do not feed such a run to `portfolio-nlp` until the T-057
re-tag. Design and evidence: `PLAN.md` Work item 10.

**Matching columns on `discovered_urls`** (schema version 2, `data_mining/schema.py`; the current version is 3, which adds `articles.prev_fetch_status`, see "Re-tag and quarantine"; constitution AI
behavior #10 confirmed 2026-10-15): `match_class` (`accept`/`review`/`reject`, `NOT NULL DEFAULT
'accept'`, indexed by `idx_match_class`), `match_reason` (the deciding signal) and `query` (the `ddg`
search query that returned the row; provenance only, it never changes the class). A matcher-rejected
pair is stored **`status = 'skipped' AND match_class = 'reject'`** at insert, its `title` untouched, so
the extractor's and the queue's `status = 'pending'` selection never returns it; `mark_skipped` (paywall,
irrelevant after inspection) keeps its meaning and leaves `match_class` alone. `enqueue_batch` no longer
just ignores a duplicate `(url, ticker)`: a rediscovery **promotes** the stored row (it keeps the higher
class `reject` < `review` < `accept`, takes the new `match_reason` with a higher class, fills a null
`match_reason`/`query`, and a row stored `reject` goes back to `pending` when its class rises). It never
demotes. The promotion UPDATE is conditional in SQL because the extractor writes `status` concurrently: `status` is written only to lift a row that is still `skipped` and `reject` in the database (never copied from the earlier read), and a write is skipped when the stored class is no longer the one that was read. `URLQueue.last_promotion` (and `promoted=` in the discovery log lines, `DiscoveryStats.total_promoted`) report how many rows were promoted and lifted per call (writes that happened: a write the class guard skipped is not counted, and a row counts as lifted only if its status really is `pending` afterwards). Existing databases are upgraded by `run_migrations` (their rows default to `accept`; the
`T-057` re-tag reclassifies them). Both read APIs expose the class: the collector's
`/discovery/query` and `/discovery/pending` return `match_class`/`match_reason` and `/discovery/query`
filters on `match_class`; the crawler's `/discovered` does the same. The `status` filter's `skipped`
includes matcher-rejected rows, so use `by_match_class` (in `queue.stats()`, the collector's
`/discovery/stats` and the CLI `stats`) to tell them from paywall skips. `match_class` has a `CHECK`
constraint and `enqueue_batch` raises `ValueError` before writing for any other value. The extractor's
`get_status_counts` and the crawler's `/status` leave rejected rows out (`/status` adds `by_match_class`; the CLI start-up line prints the rejected count separately). The extractor's
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
Expect the same row count before and after, every row `accept`, the index present and user_version 3
(the 481,332-row corpus gave that on 2026-10-15, when the version was 2; `initialize()` now also adds
`articles.prev_fetch_status` and stamps 3).

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

## Re-tag and quarantine (T-057)

`scripts/retag_urls.py` classes every legacy `discovered_urls` row (`match_reason IS NULL`) with
`data_mining.matching.classify` from the URL and the best known title (discovery title, else the fetched
article's), keeps rows that already carry a reason (T-056c strategies, `feed`), sets a `pending` pair that comes
out `reject` to `skipped`, then quarantines every fetched `review`/`reject` article. Quarantine
(`data_mining/quarantine.py`) is `articles.fetch_status = 'quarantined'` with the previous status in the new
nullable `articles.prev_fetch_status` (schema version 3; constitution AI behavior #10 confirmed 2026-10-15,
recorded in `portfolio-nlp` PR #102 T-132/T-128): `portfolio-nlp` reads `fetch_status = 'ok'`, so it never sees
these rows; `release_articles` restores a promoted row exactly; nothing is deleted and `discovered_urls` is never
touched by it. A rediscovery that promotes a fetched pair to `accept` (`URLQueue._promote_duplicates`) releases its
article too, since nothing re-fetches a fetched row. `extractor.db.record_fetch` runs `sync_quarantine` after every fetch, so a `review` pair fetched
from now on is quarantined too, and a promotion releases it. `prev_fetch_status` is not in `ARTICLE_COLUMNS`,
so a re-fetch's upsert never resets it.

```bash
# 1. dry run on a COPY: read-only, prints before/after counts per class, source and ticker,
#    the review/reject URLs also accepted for another ticker, and writes the hand-off lists
PYTHONPATH=src uv run python scripts/retag_urls.py --db COPY.db --out data/retag
# 2. the fresh acceptance check (rule frozen; excludes both earlier samples): 60 accept + 40 reject
PYTHONPATH=src:scripts uv run python scripts/ticker_validation_sample.py --final
PYTHONPATH=src:scripts uv run python scripts/ticker_tagging_sample.py manual-export \
    --sample data/ticker_retag_check_sample.csv --out data/ticker_retag_check_manual.csv   # blind sheet
#    ...label the `label` column (yes / no / unclear) of the blind sheet, then merge it into the NEW sample.
#    Both paths are explicit: `manual-apply` defaults `--sample` to data/ticker_tagging_sample.csv, the
#    first committed sample, which must not receive these labels.
PYTHONPATH=src:scripts uv run python scripts/ticker_tagging_sample.py manual-apply \
    --sample data/ticker_retag_check_sample.csv --manual data/ticker_retag_check_manual.csv
#    (--db adds the sheet checks `--apply` will run: shape, freshness, stale classes; without it only the labels are scored)
PYTHONPATH=src:scripts uv run python scripts/ticker_validation_sample.py --verdict data/ticker_retag_check_sample.csv --db urls.db
# 3. STOP the collector and the crawler, then (the labeled sample is what --hand-check scores again,
#    and a FAIL refuses the run; at most 5 of 60 accept wrong, at most 1 of 40 reject correct)
PYTHONPATH=src uv run python scripts/retag_urls.py --db urls.db --apply \
    --backup urls.pre-retag.db --hand-check data/ticker_retag_check_sample.csv
```

`--apply` refuses to run without a backup path (written through the SQLite backup API, integrity-checked, never
overwriting a file) and `--hand-check SHEET`, which it scores with the same verdict and validates as *the* T-057 sheet before writing anything (a
failing, unlabeled or missing sheet refuses the run, and so does a sheet that is not exactly 60 `accept` + 40 `reject` rows with unique
ids that exist, whose classes are what the current matcher gives now (a stale sheet), or whose ids are in either earlier sample;
`ticker_validation_sample.py --verdict SHEET --db urls.db` runs the same checks). The verdict line and the sheet path go to `retag_summary.json` for T-058. The
collector and the crawler must be stopped: the run aborts before writing if the row counts, largest ids or rows per status moved
since the plan was computed (the dry run on a copy has no such need). It migrates, re-tags in one transaction, quarantines in
a second, then compares the database with the plan (class counts, quarantined counts per class, no `accept`
article quarantined, nothing non-`accept` readable or pending) and exits 1 without writing the lists on a mismatch
(summary `verified: false`); a re-run changes nothing. Outputs: `removed_articles.csv` (fetched `reject` pairs, T-131's list),
`review_quarantine.csv` (T-132's selection with the evidence), both with `match_class`, `match_reason` and `prev_fetch_status`
columns (`ok` = a readable body; `failed`/`thin_content` articles cannot be judged by `portfolio-nlp` T-132, so T-058 reports the
decidable share; `retag_summary.json` has the counts per previous status), and `retag_summary.json`; `data/retag/` is git-ignored (regenerated, hundreds of thousands of rows).
**Known false rejects.** The frozen rule rejects some pairs whose URL spells the company out (a name that is short or an
ordinary word once its suffix is removed, a possessive `s`, an `&` the URL drops; PLAN Work item 10).
`PYTHONPATH=src uv run python scripts/known_false_rejects.py` scans `data/retag/removed_articles.csv` for the name as whole words of
the URL path and writes `data/retag_known_false_rejects.csv` (committed: 433 pairs on 234 URLs, a lower bound; the hand check
estimates 2.5% of the `reject` class, interval 0.4%-12.9%); `--check` exits 1 when the file differs from the scan. The 19 reviewed
non-company hits are `NOISE` (by article id). The rows are quarantined, not deleted; `T-062` fixes the matcher.

`--verdict` prints the `unclear` count per class: an `unclear` label is neither wrong nor correct, so read it
before trusting a pass.

## Content-quality gate (T-060)

`extractor.parse.wall_kind` classifies a login wall, bot challenge or subscribe prompt that passes the length
check from the first 300 characters of the body: a challenge is `failed`, a login/subscribe prompt is
`paywalled`, neither is ever `ok` (see `news-crawler.md`). Audit the stored `ok` rows (read-only, per domain:
walls, under the word floor, duplicate bodies; exit 1 on a wall or an under-floor row). Rows quarantined from
`ok` are audited too (`held`, `held walls` columns), because releasing a quarantined pair restores `ok`:

```bash
PYTHONPATH=src uv run python scripts/audit_article_quality.py --db /path/to/urls.db
PYTHONPATH=src uv run python scripts/audit_article_quality.py --db /path/to/urls.db --list  # ids of ok / held walls
PYTHONPATH=src uv run python scripts/audit_article_quality.py --db /path/to/urls.db --by-status  # detector hits per fetch_status
```

To re-fetch the listed ids, set their `discovered_urls.status` back to `pending` and run
`cli/news_crawler_cli.py`.

## Testing

```bash
.venv\Scripts\python.exe -m pytest tests/news_collector -q
```

99/100 pass; `test_enqueue_inserts_at_most_len_input` is a pre-existing Windows-only flake
(a hypothesis-generated property test races a temp-SQLite-file cleanup against an open
connection — `PermissionError: file in use`, not a logic bug in this codebase).
