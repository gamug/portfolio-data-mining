# news_collector — pipeline stage 1: URL discovery

**Source:** `news-collector/` → `src/news_collector/` + `apps/news_collector_api.py` + `tests/news_collector/`

Hybrid news-link discovery for S&P 500 companies. Given a list of tickers (or none — the
entire S&P 500) and a date range, it discovers article **URLs** (not full article content)
across seven target financial-news domains (CNBC, Yahoo Finance, Financial Times,
Investing.com, Nasdaq, Seeking Alpha, StockTwits) and persists them to a durable SQLite
queue (`data/urls.db` by default — override with `$DATABASE_URL`, see root
`.env.example`; table `discovered_urls`) for [news_crawler](news-crawler.md) to consume.

**Which database the gateway opens.** On this machine `/etc/environment` sets `DATABASE_URL="../data/urls.db"`, which beats `.env` (`load_dotenv()` does not
override) and points at a directory that does not exist; the collector API's request defaults are the literal `data/urls.db`, an empty file in the repo.
Start the gateway with the real path explicit: `DATABASE_URL=/workspaces/thesis/data/urls.db uv run apps/gateway.py`, and check `GET /crawler/status` (it must
show the real counts) before any discovery; for the collector pass `db_path` in the request. The fixes are `T-065` and `T-066` (`TASKS.md`); pass `company_names` (a list, one per ticker) too, or the ticker is stored as the company (`T-067`).

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
non-company hits are `NOISE` (by article id). The rows are quarantined, not deleted. Since the T-062 run (2026-10-17) all 433 are `review`/`loose_name`; the file stays as the historical scan and `--check` still passes (433 rows). About 438 other `reject` pairs have a distinctive non-first word of the name in the URL; they are not fixed, see `TASKS.md` "Known gaps".

`--verdict` prints the `unclear` count per class: an `unclear` label is neither wrong nor correct, so read it
before trusting a pass.

## Re-class the rejected pairs (T-062)

**Done on 2026-10-17** on the real `urls.db`: 448 promotions (`reject` 325,423 → 324,975, `review` 51,295 → 51,743, `ok` articles unchanged at 93,224), backup `url_cat_backup.db`, audit list `data/retag/reclass_promoted.csv`, summary `reclass_apply_summary_20261017T232931362685Z.json`. The steps below are kept as the record and the rollback guide; a second `--apply` writes nothing. If `--apply` refuses because the audit list exists, move the old file or pass another `--list` (a leftover test file caused the first attempt's refusal).

The matcher reads a name the URL spells loosely as `review` with reason `loose_name`, never `reject`: the
name with its corporate suffix kept (`kla-corporation`, `news-corp`), with the `and` of an `&` dropped,
the brand of a camel-case name (`pepsi` for `PepsiCo`; only a capital `Co`/`Corp` tail is cut, so `Costco`
is not), and a slug's possessive `s` (`bank-of-americas`) only for a name of two words or more outside
`matching.PLURAL_PHRASES` (`best-buys` and `on-semiconductors` are plurals). A single word must be
distinctive. The re-tag only touched `match_reason IS NULL` rows, so the pairs rejected earlier keep
`reject`; `scripts/reclass_rejects.py` classes them again and promotes the ones the matcher no longer
rejects (a class is only ever promoted; a `skipped` row goes back to `pending`; `sync_quarantine` runs after).

```
PYTHONPATH=src uv run python scripts/reclass_rejects.py --db urls.db                      # dry run
PYTHONPATH=src uv run python scripts/reclass_rejects.py --db urls.db --apply --backup NEW.db
PYTHONPATH=src uv run python scripts/reclass_rejects.py --db urls.db --revert data/retag/reclass_promoted.csv --backup NEW2.db
```

The dry run is read-only and prints `promotions`, `by_class`, `by_reason`,
`by_status` (the discovery status of the promoted rows), `skipped_back_to_pending`,
`promoted_with_a_quarantined_article`, `top_tickers`, `predicted_sync` (`quarantined`, `released`: what the
sync will print) and `api_rejects_left_alone`. The first dry run on the real file is slow (3 to 6 minutes, 5.3 measured on 2026-10-17: it
sits on the mounted drive and is read cold); a second one is fast because the pages are cached, so do not
conclude it hung. `--apply` needs a backup path that does not exist and refuses a
busy database, a disk that cannot hold the copy, an existing or unwritable audit list, and a database whose
fingerprint (row counts, highest ids, rows per status and per class) moved since the plan; it checks the
copy (fingerprint, `quick_check`) and prints its SHA-256, and warns when `-wal` is not empty. Stop the
collector and the crawler first. It writes the audit list as `reclass_promoted.csv.partial` and renames it to
`data/retag/reclass_promoted.csv` (git-ignored; `--list` to move it) only once the promotions are committed, so a
run that aborts leaves no list and a crash after the commit leaves it; `--revert LIST` puts back the old class,
reason and status of the rows that still hold what the run wrote, then syncs. `--revert` validates its list before it
touches anything (the columns of this script's list, integer and unique ids, each row a promotion, every id present in
the database **as the same pair** (ticker and url equal; the `UPDATE` repeats that condition); a `.partial` list is
refused) and prints how many of the listed rows its `UPDATE`s changed; `--apply` and `--revert`
are mutually exclusive, and a mistyped `--db`, a file that is not a database, an unusable backup directory or a missing list is a one-line error.
After the update and the sync, `--apply` and `--revert` read the database back and exit 1, with
`POST-CONDITION FAILED` lines, if it is not as planned: the rows per class moved by exactly the promotions, none of
them still `reject`, the `UPDATE`s changed exactly the planned rows, the sync did what the dry run predicted, no `accept`
pair keeps a quarantined article, and the articles `portfolio-nlp` reads (`fetch_status = 'ok'`) changed by exactly the
change the dry run predicted (`predicted_ok_articles_delta`: 0 when no pair becomes `accept`, which is the claim that a
run of `review` promotions changes nothing a consumer reads). A revert: no listed row still holds what the run wrote and the
`ok` articles changed by what the revert implies. Both modes then truncate the
WAL and finish with a **reader check**: the file is attached read-only through `Database.attach(read_only=True)` of the
`portfolio-common` installed there, the pairs are counted against the fingerprint, and the line
`reader check: attach(read_only=True) ok, N rows, M readable articles (portfolio-common X)` is printed (it counts the
`ok` articles through the attach and compares them with a direct count). That is the failure that made `urls.db`
unreachable for `portfolio-nlp` while it was in WAL mode, so a run that leaves it in a state a reader refuses ends with
`POST-CONDITION FAILED` and exit 1. The remedy printed depends on the kind of failure: a data problem says how to undo
(`--revert` with the real list and backup paths; for a revert run, to restore the backup only if nothing has run since), a
reader problem says the data is as planned and to checkpoint with every reader stopped (`python3 -c "import sqlite3; print(sqlite3.connect('FILE').execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())"`, which prints `(0, n, n)` when done; the script prints this command with the real path, and the `sqlite3` CLI is not installed on this machine) or `uv sync` to `portfolio-common` >= 1.2.2, with no revert needed. Both modes also write
`reclass_apply_summary_<time>.json` / `reclass_revert_summary_<time>.json` next to the list (`--summary` to move it; it
never overwrites): database, `portfolio-common` version, the state before and after (pairs, articles, rows per class,
`ok` articles), the rows changed, the predicted and actual sync, the backup path and SHA-256, the list path and rows,
the post-conditions with their problems, the reader check and the duration of each stage; the backup, list and summary
paths are printed absolute. If the process dies between the promotions and the
sync, `--apply` again (no backup is asked for when nothing is left to promote) runs the sync.

**What the idle check can see.** `--apply` opens a write transaction before it copies anything and refuses when
another process holds the lock. That sees a writer on the machine that runs the script. A collector or crawler on
another host (for example Windows, with the script in a container over the mounted drive) may not be visible
through the mount, so the fingerprint is the protection and **you confirm the processes are stopped**: the collector
API (`apps/news_collector_api.py`, port 8001), any `discover` run of the collector CLI, and the crawler/extractor.

### Real run checklist (T-062)

The order matters: `matching.classify` runs in the collector at discovery, and the queue upsert only promotes a
class. A collector or crawler restarted after the apply with the **old** code writes the same pairs as `reject` and
`skipped` again and undoes the fix. Time to allow: **12 to 17 minutes** for `--apply` on the real file, plan for 20 (measured on copies on the same
mounted drive, cold: 697 s and 1,030 s; fingerprint 46 s, plan 61 s, report 73 s, backup with `quick_check` and SHA-256
5.5 minutes, update and sync 2 minutes, verify 1 minute in the first), about 10 to 12 minutes for `--revert`
(backup 6.5 minutes), and up to 8 minutes for an `--apply` that finds nothing (it still plans, syncs and verifies). The
spread is the shared drive. Do not stop a run because it is quiet: each stage prints when it ends, and a run killed
inside the backup leaves a partial file that the next run refuses until it is moved. Both modes end with
`PRAGMA wal_checkpoint(TRUNCATE)`: the last connection to close is the read-only verification, which cannot fold the log
into the file, and a reader on `portfolio-common` 1.2.1 refuses a database whose `-wal` is not empty (1.2.2 refuses
only a non-empty `-wal` with no `-shm`). If a reader holds the log the run says so and the log clears when it closes.

The rehearsal script that exercised this sequence on a copy (`scripts/rehearse_reclass.py`) was removed after the real run of
2026-10-17; it is in the git history (added in `417af01`, last present at `4bfa812^`: `git show 4bfa812^:scripts/rehearse_reclass.py`).

1. T-062 (PR #66), the docs PR #67 and the remedy fix PR #69 are merged (step 6 below prints a `python3 -c` command, not
   `sqlite3`, and the reader-problem remedy printed by the script is a `python3 -c` command too) and the code is updated
   on the machine that runs the collector and the crawler (`git pull`, then `uv sync`: master pins `portfolio-common`
   v1.2.2, which the database access needs, and `uv pip list` must show `portfolio-common 1.2.2`; same environment as
   the script; `--apply` and `--revert` refuse before any backup if it is older, the dry run warns).
2. Stop the collector (the API on port 8001, any `discover` run) and the crawler/extractor, and confirm nothing runs
   (port 8001 closed, no collector or extractor process). The script cannot prove this across hosts (see above).
3. Dry run on the real database and check the counts (the plan line must show the expected number of promotions; on 2026-10-17 it
   showed 448): `PYTHONPATH=src uv run python
   scripts/reclass_rejects.py --db /workspaces/thesis/data/urls.db`.
4. Apply with a new backup name:
   `PYTHONPATH=src uv run python scripts/reclass_rejects.py --db /workspaces/thesis/data/urls.db --apply --backup
   /workspaces/thesis/data/url_cat_backup.db --list data/retag/reclass_promoted.csv`. On screen: the stage lines
   (stderr), `backup … sha256 …`, `audit list … (N rows)`, `promoted N rows; quarantined 0, released 0`, `checkpoint: WAL
   truncated …` (stderr), `reader check: attach(read_only=True) ok, N rows, M readable articles (portfolio-common 1.2.2)`, `run summary …` and exit
   0 with no `POST-CONDITION FAILED`. On anything else, stop and read it (a failed check says the changes are
   committed and how to revert).
5. Keep `reclass_apply_summary_<time>.json` (next to the list; it holds the backup's SHA-256, the counts, the reader check
   and the stage times) with the backup and `data/retag/reclass_promoted.csv`, and say who keeps them. Expected figures,
   as of the dry run with the collector stopped (they move if rows were added since): `reject` 325,423 → 324,975 (the real file reads 325,423 on 2026-10-17),
   `review` 51,295 → 51,743, `accept` 104,614 unchanged, `match_reason = 'loose_name'` 448, `articles.fetch_status`
   unchanged.
6. Restart the collector and the crawler with the merged code and check the deployed matcher. At once, read-only:
   `PYTHONPATH=src uv run python -c "from data_mining.matching import classify; print(classify('https://x.com/kla-corporation-q4',
   '', 'KLA Corporation', 'KLAC'))"` must print `review` / `loose_name` (it proves the code, not the data). Once the
   collector has discovered new rows (the `sqlite3` CLI is not installed on this machine, so Python):
   `python3 -c "import sqlite3; c = sqlite3.connect('file:/workspaces/thesis/data/urls.db?mode=ro', uri=True);
   print(*c.execute('SELECT id, ticker, match_class, match_reason, url FROM discovered_urls ORDER BY id DESC LIMIT 20'),
   sep='\\n')"`; a new row whose URL spells the full company name is `review` / `loose_name`, not `reject`.
7. Rollback: `--revert data/retag/reclass_promoted.csv --backup NEW.db` puts back the class, reason and status of the
   rows that still hold what the run wrote. Restoring `url_cat_backup.db` instead also discards every row the collector and
   crawler wrote after the backup, so use it only if nothing has run since.

A promoted pair is `review`, so its article stays quarantined and `portfolio-nlp` reads exactly what it read
before; the counts that change are `reject` and `review`. `scripts/loose_name_precision.py sample` draws 30
promoted pairs by distinct URL (News Corp 10, the rest 20) for a person to label; `summary` scores them with a
lower bound and `unclear` counts (`--what-if ID=LABEL` shows what a contested label changes), and `refresh`
replaces only the rows a later matcher change un-promoted. Releasing a
subset to `accept` is decided on a measured share, not assumed. A form of the name needs words of two
characters or more (`AT&T`'s `at t` is not a spelling); `matching.PLURAL_PHRASES` holds the plural phrases read by hand
(`best buy`, `on semiconductor`, `target corporation`). Each stage of `reclass_rejects.py` prints its duration to stderr. A labeled check sheet goes stale when the
matcher changes: `ticker_validation_sample.py --refresh SHEET --db urls.db` keeps every row whose class
still holds (with its label) and replaces only the others, which a person then labels.

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
