# news_collector — pipeline stage 1: URL discovery

**Source:** `news-collector/` → `src/news_collector/` + `apps/news_collector_api.py` + `tests/news_collector/`

Hybrid news-link discovery for S&P 500 companies. Given a list of tickers (or none — the
entire S&P 500) and a date range, it discovers article **URLs** (not full article content)
across seven target financial-news domains (CNBC, Yahoo Finance, Financial Times,
Investing.com, Nasdaq, Seeking Alpha, StockTwits) and persists them to a durable SQLite
queue (`data/urls.db` by default — override with `$DATABASE_URL`, see root
`.env.example`; table `discovered_urls`) for [news_crawler](news-crawler.md) to consume.

**Which database the gateway opens.** The collector API and CLI take the path from the request or `--db`, else `$DATABASE_URL`, else `data/urls.db`; an empty value is unset (`data_mining.resolve_db_path`, T-065). On this machine `/etc/environment` sets `DATABASE_URL="../data/urls.db"`, and points at a directory that does not exist; since T-066b the repo-root `.env` wins over it (every entry point calls `data_mining.env.load_env()` first, so `uv run apps/gateway.py` picks the `.env` database from any working directory), and `GET /crawler/status` must still show the real counts before any discovery. A one-off value on the command line (`DATABASE_URL=… uv run …`) now loses to `.env`: edit `.env`, or use a flag applied after the load (`--db`, `--allow-new-db`, a request's `db_path`); a blank `.env` line cannot switch off an inherited variable such as `HTTP_PROXY`, unset it in the system or the shell. At start-up the gateway, the crawler API and the collector API log the effective database (`DATABASE_URL source=… path=… file: … discovered_urls=N rows`) and warn when the environment differs from `.env` (only possible when `load_env()` did not run in the process; `load_env()` itself logs one line per variable it overrides, with the values only for `DATABASE_URL`, `UNIVERSE_DB_PATH`, `DISCOVERY_START_DATE`, `ALLOW_NEW_DB`; a stale `DISCOVERY_END_DATE` is named once and ignored, T-077); the gateway and the crawler refuse to start on a missing file, a missing table or a schema with no rows (`ALLOW_NEW_DB=1` or `--allow-new-db` to start on an empty database on purpose), the collector only on a missing directory or an unreadable file (`data_mining.startup_check`, T-066a). `company_names` is positional (one name per ticker, same length, else 422); when omitted, each ticker's name is looked up by exact symbol in the current S&P 500 (one Wikipedia request, 502 on failure), then, for a ticker that has left the index, in `data/universe.db` as of the request's `start_date` (`query_as_of`, never a partial name match). A ticker found in neither, or a `start_date` the store does not cover, is a 422 that asks for `company_names`. Known limit: a ticker added after `start_date` and removed before today is in neither, so pass `company_names` for it (`T-067`, PR #81). Symbols are matched exactly as Wikipedia writes them (`BRK.B`, not `BRK-B`). A request with `tickers` and no `company_names` now needs the network (Wikipedia) or a populated store; before, it worked offline but stored the ticker as the company. If Wikipedia is down the store alone can still name the tickers it knows (a 502 only for the rest). The lookup never creates `universe.db`: a missing, unreadable or empty store is reported in the 422. The same lookup serves `discover --tickers` of the CLI (`news_collector/companies.py`), which stops with status 1 when a ticker has no name. If Wikipedia is down, a ticker that is also current is named from the store as of `start_date`, so a renamed company gets its name of that date (a warning is logged; the aliases carry the ticker). An empty `company_names` list counts as omitted.

**Caution until `PLAN.md` Work item 15 (`T-071`) is merged: do not run a `cnbc.com` discovery for more tickers on the real database.** The sitemap strategy turns every URL of the cnbc sitemap (about 578,000) into a pair for the ticker, and the queue stores the rejected ones: the `KLAC` run on 2026-10-18 added 578,117 rows (+243 MB); a run of all tickers would have added about 290 million. Since the sitemap date range is applied (PR #75, if merged) a ticker adds about 121,000 rows and a full run about 61 million (a projection, roughly 25 GB): smaller, still not acceptable. Work item 15 keeps the sitemap once per domain and stores only `accept`/`review` pairs.

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
Expect the same row count before and after, every row `accept`, the index present and user_version 4
(the 481,332-row corpus gave that on 2026-10-15, when the version was 2; `initialize()` now also adds
`articles.prev_fetch_status` and, since T-069, the two empty sitemap-cache tables, and stamps **4**: the expected
`user_version` is 4). The cache migration has its own rehearsal on a copy,
`scripts/rehearse_sitemap_cache_migration.py` (`--source` is opened read-only, `--copy` must not exist).

**After merging the schema-version-4 change (T-069), on the real file.** Run every command from the repository root; `DB` is the
file the gateway and the crawler open (`$DATABASE_URL`, else `data/urls.db`; the thesis machine uses `/workspaces/thesis/data/urls.db`).
The `sqlite3` command-line tool is not installed there, so each step is a short Python command. The two services create the cache
tables when they start, in either order or together (the second waits for the first's write lock).

1. **Before restarting: a backup and the reference counts** (read-only on `DB`; needs free disk the size of the file, about 2.5 GB, and a
   few minutes; it refuses to overwrite an existing backup):
   ```bash
   export DB=/workspaces/thesis/data/urls.db
   PYTHONPATH=src uv run python - <<'EOF'
   import os, sqlite3, sys
   db_path = os.environ["DB"]
   backup_path = db_path.removesuffix(".db") + ".pre-t069.db"
   if os.path.exists(backup_path):
       sys.exit(f"{backup_path} exists: not overwriting")
   src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
   print("user_version:", src.execute("PRAGMA user_version").fetchone()[0])
   for t in ("discovered_urls", "articles", "discovery_progress"):
       print(f"{t}: {src.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
   dst = sqlite3.connect(backup_path)
   src.backup(dst)
   print("backup quick_check:", dst.execute("PRAGMA quick_check").fetchone()[0], "->", backup_path)
   src.close(); dst.close()
   EOF
   ```
   Expect `user_version: 3` and `backup quick_check: ok`. Write the three counts down (2026-10-19: 1,059,452, 481,332 and 3,534;
   they will have grown).
2. **Restart the gateway and the crawler** the way you normally do. Do not start a discovery or a crawl yet.
3. **Check the real file, read-only, right after the restart:**
   ```bash
   PYTHONPATH=src uv run python - <<'EOF'
   import os, sqlite3
   db = sqlite3.connect(f"file:{os.environ['DB']}?mode=ro", uri=True)
   print("user_version:", db.execute("PRAGMA user_version").fetchone()[0], "(expected 4)")
   print("sitemap objects:", [r[0] for r in db.execute(
       "SELECT name FROM sqlite_master WHERE name LIKE '%sitemap_%' "
       "AND name NOT LIKE 'sqlite_autoindex%' ORDER BY name")])
   for t in ("discovered_urls", "articles", "discovery_progress"):
       print(f"{t}: {db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
   EOF
   ```
   Expect `user_version: 4`; the five names `ix_sitemap_crawls_latest`, `ix_sitemap_crawls_open`, `ix_sitemap_entries_lastmod`,
   `sitemap_crawls`, `sitemap_entries`; and **the three counts equal to step 1** (the migration adds no row; a count that is higher only
   means a job wrote between the two steps).
4. **If anything differs: stop both services**, then run the rollback (it refuses a file that is not at the current schema version, 5 since T-073) and restart on the
   old code, or restore the backup of step 1:
   ```bash
   PYTHONPATH=src uv run python - <<'EOF'
   import os, sqlite3
   from portfolio_common.db import Database
   from data_mining.schema import rollback_sitemap_cache
   db = Database(sqlite3.connect(os.environ["DB"]))
   rollback_sitemap_cache(db)
   db.commit()
   print("user_version now:", db.schema_version)
   db.close()
   EOF
   ```
   Expect `user_version now: 3` and no `sitemap_*` name in the step 3 query.
5. **Keep the backup until it is proven, then delete it.** A `urls.pre-*.db` file (here `urls.pre-t069.db`, about 2.5 GB) is a plain copy; it is
   not a substitute for the rollback of step 4. Delete it once a complete collector run and a complete extractor run have finished at
   version 4 and step 3 still shows `user_version: 4` with the same objects, or 14 days after the migration was applied (for
   `urls.pre-t069.db`: 2026-10-19, so 2026-11-02 at the earliest), whichever is later. The maintainer deletes backups; nobody else does.
   The older `urls.pre-retag.db`, `urls.pre-t064.db`, `urls.pre-wi14-labels.db`, `url_cat_backup.db` and `urls_.db` follow the same rule.

**After merging the schema-version-5 change (T-073), on the real file.** Version 5 only drops `ix_sitemap_crawls_latest`, the
index version 4 created and no query has used since T-070b (the latest complete crawl is found by the primary key
`(domain, started_at)`). It holds no data: the cache tables, the pipeline tables and every row stay as they are, and a
version-4 build that starts on a version-5 file re-creates the index and stamps 4. The first service that starts on the new code
runs the `DROP INDEX IF EXISTS`; it is a schema change on the real file (constitution #10), so make a backup first, the same way
as step 1 above but with the file name you choose (`T-073` used `urls_t_073.db`, next to `urls.db`; it refuses to overwrite).
After the restart, step 3 above applies with two changes: expect `user_version: 5` and **four** names (`ix_sitemap_crawls_open`,
`ix_sitemap_entries_lastmod`, `sitemap_crawls`, `sitemap_entries`), and the three counts equal to the backup's. Rehearsed on a copy
of the real file (2026-10-20, `scripts/rehearse_sitemap_cache_migration.py`): 0.005 s, pipeline fingerprint identical, version 4 → 5,
the index gone, a second run changes nothing, the rollback restores version 3 with no cache object, `quick_check` ok.

Every row in `discovered_urls` carries a stable `id` primary key — the crawler holds it as
a foreign key back to this table, rather than re-matching on the `url` string.

## The sitemap cache of a domain (T-070a)

A domain with `sitemap_roots` (today only `cnbc.com`) is crawled **once per domain**, not once per ticker: `news_collector/sitemap_cache.py`
stores the in-range entries in `sitemap_entries` and every ticker reads them. The matching is still the full scan of today and the
`reject` pairs are still stored (T-071 changes both), so **the rule above stands: no `cnbc.com` discovery on the real database until
T-071 is merged**.

- **Fresh enough:** the latest complete crawl is used while it started at most **7 days** ago, whatever the run's end date. The age is
  judged once, when the run starts. A run that needs today's sitemap passes **`--refresh-sitemaps`** (CLI) or `"refresh_sitemaps": true`
  (`/discovery/run-all` and the per-domain routes); two forced requests at the same time make one crawl.
- **It refreshes** when there is no complete crawl, the sitemap roots or the filter (`CACHE_VERSION`) changed, the run starts before the
  crawl's `range_start`, the crawl is older than 7 days, or it is forced. A refresh never narrows the cache and never replaces it: entries
  are upserted with `last_seen` = the crawl's `started_at`, an entry missing from a complete crawl stays in the table but is no longer
  current, and a complete refresh deletes entries not seen for 90 days (and crawl rows older than that, but the latest complete one).
- **Complete** means every root and child was fetched and parsed. One failed child makes the crawl incomplete: only its `sitemap_crawls`
  row is written. A domain whose child always fails (`ft`) is unsupported and must keep its `sitemap_roots` empty.
- **A failed refresh:** a cache that covers the run and is at most 7 days old is used; an older one is used with a warning that carries its
  age; one that does not cover the run (or none) fails every pair of the domain with the reason, and the pairs are not checkpointed.
- **An outage is tried once.** After a failed refresh a process does not crawl again for **5 minutes** (`SITEMAP_RETRY_MINUTES` in `.env`, a number
  >= 0, `0` = retry on every run; a bad value is ignored with a warning) unless the call is forced; the runs of those minutes use the cache
  as in the failed-refresh cases above. The failed attempt is a `sitemap_crawls` row with `complete = 0`; every refresh, complete or not,
  deletes crawl rows older than 90 days except the latest complete one. A gateway whose source recovers inside the window keeps failing
  that domain until the window ends or the call is forced; a separate CLI run is a new process and is not affected.
  The setting is read once, when the process makes the domain's cache, so **a running gateway needs a restart to see a new
  `SITEMAP_RETRY_MINUTES`**; each CLI run reads it afresh.
- **The run summary names an unusable domain once**, with the number of pairs it left undone (`cnbc.com: sitemap cache of ... does not cover ...
  (503 pairs not checkpointed)`), not once per ticker; every pair of the domain is still not checkpointed.
- **Warnings:** when the refresh failed and the run went on with the cache (cases a and b), the run says so: `Warnings` in the CLI
  summary, `warnings` on `RunAllResponse` and on each `DiscoverResponse`, with the age of the crawl used. It is not an error: the pairs are
  checkpointed and the exit status is 0. A cache the run cannot use (case c) is an error, as above.
- **Memory:** a process holds the rows of the crawl it last read for as long as it lives: about 26.5 MB for the 109,493 `cnbc.com`
  entries (`tracemalloc`), so the gateway's resident size grows by that much after its first discovery request and stays.
- **Errors:** a database error while the cache is read or refreshed is not an exception of the run: it is a failure of every pair of that
  domain (not checkpointed), and the other domains run.
- **Two processes, one crawl (T-070b).** The gateway and the crawler are two processes on one file. A process that must refresh first
  claims the domain: one `BEGIN IMMEDIATE` transaction looks for an unfinished `sitemap_crawls` row younger than the stale timeout and,
  finding none, inserts its own (`finished_at` empty); the other process finds it, **waits** (it looks again every 5 s) and then reads what
  the winner wrote. If the winner **failed** the waiter crawls nothing (one outage, one crawl) and applies the failed-refresh cases above;
  a winner whose row is older than **30 minutes** (`SITEMAP_STALE_MINUTES` in `.env`, a number > 0) is a crashed crawl and is **taken
  over**: its row is ignored (it stays, as history) and the waiter crawls. A crawl that raises or is cancelled closes its row as failed at
  once. A slow domain needs the setting raised (`ft` took 837 s): a crawl longer than the timeout is crawled twice. Like the retry
  setting, it is read once per process, **and the gateway and the crawler must use the same value**: each judges the other's unfinished
  row with its own (30 and 60 would let the first take over a live 40-minute crawl); both read the one repo-root `.env`, so they agree
  unless one is overridden in its environment. A waiting process whose range is wider than the winner's crawls after it.
  Three consequences: (1) **a request waits for the crawl it finds:** an API request for a domain that the crawler is crawling waits
  for that crawl, up to the stale timeout (about 12 s for `cnbc.com`), because the waiter holds the process's lock for the domain;
  (2) **the gateway and the crawler are assumed to share a clock**, since the row's `started_at` is written by one process and judged
  by the other (a skew larger than the timeout would make a live crawl look dead); (3) **two crawls can overlap after a takeover**
  (the first was only slow): the newest crawl's data wins whichever finishes last (the latest complete crawl is the one with the latest
  `started_at`, and an older crawl never moves an entry's `last_seen` back).

- **Usage:**
  ```bash
  # the cache is on by default for a domain with sitemap roots; a cache at most 7 days old is used
  uv run python cli/news_collector_cli.py discover --tickers AAPL --domains cnbc.com
  # crawl the sitemap now, whatever the age of the cache (10 s); the cache is never narrowed
  uv run python cli/news_collector_cli.py discover --tickers AAPL --domains cnbc.com --refresh-sitemaps
  # the same on the API: {"refresh_sitemaps": true} in the body of /discovery/run-all or /cnbc/
  ```
  A run that used an old cache because the refresh failed prints `Warnings` in its summary and returns `warnings` in the API response.
- **A crawl in progress** shows as a `sitemap_crawls` row with no `finished_at` (the query below lists it); a row of a dead process stays
  until the 90-day purge and does no harm.
- **Look at it** (read-only, from the repository root; `DB` as above):
  ```bash
  PYTHONPATH=src uv run python - <<'EOF'
  import os, sqlite3
  db = sqlite3.connect(f"file:{os.environ['DB']}?mode=ro", uri=True)
  for row in db.execute("SELECT domain, started_at, complete, range_start, entries, failures FROM sitemap_crawls ORDER BY started_at DESC LIMIT 5"):
      print(row)
  print("entries:", db.execute("SELECT domain, COUNT(*) FROM sitemap_entries GROUP BY domain").fetchall())
  EOF
  ```

## New S&P 500 members backfill automatically (no special-casing needed)

The universe is mutable (see `common.universe_history`, [pricing.md](pricing.md)): a ticker
can join the tracked S&P 500 at any time, and until `discover` is re-run for it, it simply
has zero rows in `discovered_urls`/`articles` — a real gap, not a bug, if nobody re-runs the
pipeline.

The fix needs no discovery code, because of how `discover` already behaves by default: with neither
`--tickers` nor `--sp500` given, it fetches the **live, current** Wikipedia S&P 500 list
every time (`news_collector.sp500.fetch_sp500_companies`) and requests the **full**
`$DISCOVERY_START_DATE`-to-today range (the end date is `--end-date` / `end_date`, default today UTC, T-077) for every ticker in it — never an
incremental tail. `--resume` (on by default) only skips a `(ticker, domain)` pair that
already has a `discovery_progress` row for that *exact* date range (`orchestrator.py`); a
ticker that just joined the universe has no such row, so it gets the full historical range
on the very next run, exactly like every other ticker did on its own first run — same
treatment, no "is this ticker new?" branch anywhere. The end date defaults to today, so
**on a new day every pair re-runs from the start date** (its range is new); no row is
duplicated (`UNIQUE(url, ticker)`), the cost is time and network. To continue a batch on a
later day, pass the same `--end-date` / `end_date`. A pair whose sitemap root or child
failed to fetch or parse (T-076) is **not** recorded as completed: the run reports it in its
errors (the CLI exits non-zero, the API route lists it in `errors`) and the next run retries
it. RSS and Wayback failures stay warnings. `ft.com` and `nasdaq.com` have no sitemap roots
(403 on 2026-10-18), so they are not affected.

This mirrors the pattern `portfolio-financial-analysis`'s `pricing_agent`/`fundamental_agent`
use for the same problem (mutable universe, per-agent price/filing data): *"One `run` makes
a request per ticker for the whole date range... windows already stored are skipped, so
re-runs resume"* — full universe × full range × idempotent skip, every invocation, so a new
member is never silently under-covered. Operationally here: after `pricing_cli.py
universe-snapshot` reports an `added` ticker, just re-run `news_collector discover` (default
args) — it will pick up that ticker's full backlog on its own.

## Sitemap dates: `pub_date` is the sitemap's `lastmod` (T-075)

For a sitemap or RSS row, `discovered_urls.pub_date` is the entry's `lastmod` (last modified), not the publication date. The
run's date range is applied to that same `lastmod`, and to a child sitemap's `lastmod` in an index (a child older than the range
start is not fetched). So an article published inside the range but modified after the range end is not discovered, and an old
article modified recently is. On the live cnbc sitemap (618,898 entries, measured 2026-10-18): `lastmod` in range for 121,233, the date in the URL in range for 120,351, both for 120,179. So 99.86% of the URL-dated rows in range have a `lastmod` in range, and 99.1% of the `lastmod`-in-range rows have a URL date in range (1,054 do not). The rows the filter wrongly drops are the 172 whose URL date is in range and `lastmod` is not. An entry whose `lastmod` cannot be read is kept, and a warning counts it. `articles.pub_date` is a different
column: it comes from the extractor, and it is the one to filter on for what an article says it was published. The `pub_date_from`
and `pub_date_to` filters of the queue read `discovered_urls.pub_date`. **The rows stored before this fix have a `NULL` `pub_date`** (all 1,040,939 sitemap rows on 2026-10-18), and a `NULL` fails the filter, so for them a date filter returns nothing: an empty result is not "no articles in that window". Rediscovery does not fill it in (the insert ignores an existing row); backfilling is the maintainer's decision.

## Running

```bash
# CLI (direct — no server; see src/news_collector/main.py for the full flag list)
.venv\Scripts\python.exe cli\news_collector_cli.py discover --tickers AAPL MSFT
# --tickers are named by exact symbol (current S&P 500, then the index as of --start-date), never by the
# ticker itself; a ticker with no name stops the run with status 1 before any discovery (T-067)
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
   The 2026-10-18 read of the real rows is in `CHANGELOG.md` `T-064` step 5.
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

## Re-class from the sitemap cache (T-072)

`T-071` stops storing the sitemap pairs the matcher rejects, so a later matcher change cannot promote what was never
stored. `reclass-sitemap` is the way back: it classes the cached entries again with the current matcher, **with no network
and no crawl**, and enqueues the pairs that are now `accept`/`review` through the same promote-only `enqueue_batch`.

```bash
# dry run (the default): opens the database read-only and says what it would insert and promote
uv run python cli/news_collector_cli.py reclass-sitemap --tickers AAPL KLAC
uv run python cli/news_collector_cli.py reclass-sitemap --sp500 SP500.csv                 # every ticker of a CSV (ticker, name, sector): no network at all
# write: enqueue the pairs that are now accept/review (promote-only, through the discovery's own write path)
uv run python cli/news_collector_cli.py reclass-sitemap --tickers AAPL --apply
# the same on the API: POST /discovery/reclass-sitemap {"tickers": ["AAPL"], "apply": false}
#   the route needs `tickers` (at most `MAX_RECLASS_TICKERS`, 50 today, counted as sent: about 4.6 s a ticker for cnbc.com, no progress, cannot be stopped);
#   the whole S&P 500 is the CLI's job, which shows a progress bar and logs one line per ticker
```

- **Current entries only.** It reads the entries the latest complete crawl saw: an entry that left the sitemap (expired) is not
  re-classed, and a stored row is never derived from the cache and never deleted or demoted.
- **No crawl.** A domain with no complete crawl of the current roots and cache version that covers the range is an error line
  (exit status 1, `errors` in the API response), not a refresh: use a discovery with `--refresh-sitemaps` for that. An old cache
  is used whatever its age.
- **Idempotent.** A second run inserts and promotes nothing; a matcher change that moves a pair from nothing to `review` (or
  `review` to `accept`) shows up as one insert (or one promotion), and a worse class later changes nothing. A promoted
  `skipped` row goes back to `pending`.
- **Names.** `--tickers` are named as in `discover` (the S&P 500 list and `universe.db`: a network request for the list unless
  `--sp500 CSV` is given); the sitemap itself is never fetched.
- **Cost.** The classification is the full scan of today (about 4.6 s a ticker for `cnbc.com`: 503 tickers take about 40 minutes
  and a `tqdm` bar shows the progress); `T-071`'s token index will make it seconds.
- **Reject pairs** are counted in the output (`reject`) and never stored.
- **Database path.** A path that is not an existing file is an error line and nothing is created, in a dry run and with
  `--apply`. `--apply` opens the queue like a collector does, so on a file below schema version 4 it runs the migration: on
  the real `urls.db` that is behind the gate and backup rules of `T-073`, and a collector or the gateway may be writing at the
  same time (the writes are batches of 5,000 pairs, promote-only).
- **Errors.** A failed ticker (a lock, a file without the queue tables, a bad class, the disk) is one error line plus a
  WARNING in the log; the other tickers go on. The CLI prints "Tickers failed: N" and exits 1; the API answers **200** with
  `errors` filled, so a client must read `errors`, not only the status code. For a ticker that failed with `--apply`,
  `inserted`/`promoted` are **at least** what the completed batches wrote: the failed batch may be partly stored (the queue
  writes it without ids or promotion when the error comes after the insert), and a second run is safe and finishes it.

**What each script reads after `T-071` and `T-073`** (stored rows unless said otherwise):

| script | what it still covers |
|---|---|
| `scripts/reclass_rejects.py` | the stored rejects that remain: `ddg` (1,115), the 323,864 sitemap rejects that keep a quarantined article, domains with no cache, the hand-labeled sample rows `T-073` keeps, and the rows stored before `T-071`. Not retired. |
| `scripts/retag_urls.py` | legacy rows with no `match_reason` (unaffected by `T-071`/`T-073`); its `--hand-check` sheet draws its 40 `reject` pairs from the stored rows, so the sitemap ones come from the kept rows (fetched). |
| `scripts/score_ticker_rules.py` | reads the committed labeled sample files, so the kept rows matter (`T-073` never deletes a labeled id); its `--diff` scans the corpus that remains. |
| `scripts/ticker_validation_sample.py` | draws from stored rows by `(class, source)`. After `T-073` the `(reject, sitemap)` stratum is the kept rows, all fetched before the matcher existed, **not** the population that is no longer stored. A tiny or empty stratum draws what exists and prints its size in the sheet (`stratum_size`), and a sheet short of exactly 40 `reject` rows is refused as the T-057 check by `check_sheet` (tests). To look at the pairs the matcher rejects from the cache, the `reject` count of `reclass-sitemap`'s dry run is the number; a hand-check sheet of them has no `discovered_urls` id to key on, so the labeling tools cannot take it (a follow-up if wanted). |

**The 438 known-gap pairs** (`TASKS.md`, "Known gaps"; `PLAN.md` Work item 12), measured read-only on the real `urls.db`
2026-10-20 with the reconstructed rule (a non-first word of the normalized name, 5+ characters, not a stopword, a whole word of
the URL path): the rule still finds **438 pairs on 438 URLs**; **0 are in the set `T-073` deletes** (never fetched, no article,
`sitemap`/`rss`, `cnbc.com`) and **all 438 are in the kept 323,864** (every one has a quarantined article). So the gap keeps its
whole population and `reclass_rejects.py` still sees it; nothing in it depends on being re-derived from the cache.

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

## Reclaim the never-fetched sitemap rejects (T-073, gate G3)

`scripts/reclaim_sitemap_rejects.py` (`news_collector/reclaim_sitemap.py`) has two commands. **`evidence`** is read-only on any file and prints what
G3 judges; **`reclaim`** deletes, compacts and swaps, and on the real `urls.db` runs **only at G3**.

**What qualifies** (one WHERE clause, `candidate_where`): `match_class='reject'`, `status='skipped'`, no `articles` row, source `sitemap`/`rss`, a
domain whose cache holds a complete crawl, and an id that no committed hand-labeled sample names (`data/ticker_*_sample.csv`, `ticker_*_manual.csv`:
593 ids). Nothing else is ever deleted: the 323,864 sitemap rejects that keep a quarantined article, the 1,115 `ddg` rejects and every
`accept`/`review` row stay.

**What `reclaim` does:** refuses unless `--backup` is another file that passes `quick_check` and has the live file's fingerprint (counts,
class counts, orphan articles, `user_version`, a hash of every table and index definition), `--expect N` names the approved count (a different count
deletes nothing), the disk has the file size free, no `<file>.compact` is left, and, on the real file, `--i-have-the-g3-approval` is given and no
gateway/crawler process runs. Then one transaction deletes the rows and checks the fingerprint (`discovered_urls` down by exactly the deleted count,
`articles` unchanged, no orphan, `accept`/`review` unchanged; any failure rolls back), `VACUUM INTO` writes the compacted file, which is checked against
the same fingerprint and put back in the original journal mode (`VACUUM INTO` writes a rollback-journal file, the queue's is WAL), and it is swapped in.
The old file is the backup, kept until the maintainer deletes it.

```bash
PYTHONPATH=src uv run python scripts/reclaim_sitemap_rejects.py evidence --db COPY.db          # read-only; fill the cache of the copy first
PYTHONPATH=src uv run python scripts/reclaim_sitemap_rejects.py reclaim --db COPY.db --backup COPY.pre.db --expect N
```

**Rehearsal on a copy of the real file, 2026-10-20** (cnbc cache filled on the copy: 109,550 entries, range 2022-01-01 to 2026-10-20; the real file
was only read through SQLite's backup API):

| step | result |
|---|---|
| qualifying rows | **578,113**, all `cnbc.com`; the hand-labeled ids remove 0 (as measured 2026-10-18) |
| split | in the cache (in range): 109,452; **before the range: 468,661** (2006 to 2021); **left the sitemap: 0** |
| `reclaim` | deleted 578,113 in 65 s; `discovered_urls` 1,059,452 → 481,339; `articles` 481,332 unchanged, 0 orphans; `accept` 104,614 and `review` 51,746 unchanged; rejects 903,092 → 324,979 (323,864 with an article + 1,115 `ddg`); `quick_check` ok; `user_version` 4; the cache tables (109,550 entries) intact |
| file | 2,570,055,680 → 2,289,635,328 bytes: **280 MB reclaimed** (the estimate was 243 MB) |

**The proof that the cache re-derives the stored `accept`/`review` pairs** (20 tickers: `KLAC AAPL ON A IT ARE TTD MSFT NVDA CRM` and 10 seeded at
random): 19,866 stored `sitemap` `accept`/`review` pairs; **5,254 are in the cache and re-derived**; 14,595 are not in the cache (13,686 before the range,
909 whose URL left the sitemap): they stay stored and are never part of the delete; **17 are in range, in the cache and classed `reject` by the
current matcher** (`ON`, `A`, `GL`, `ALL`: market-wrap slugs such as `stocks-making-the-biggest-moves-midday-…-on-…`, where the ticker is an English word;
stored `accept`/`review` on 2026-08-10 to 12 by the matcher of that day). The plan's pass condition says "0 in-range pairs missing". Read literally it is **not
met by 17 pairs**; it is a change of the matcher, not a loss of the cache (the URL is in the cache and `reclass-sitemap` sees it), and `reclaim` never
touches an `accept`/`review` row. Whether that satisfies G3 is the maintainer's call.
