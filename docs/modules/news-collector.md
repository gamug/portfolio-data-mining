# news_collector — pipeline stage 1: URL discovery

**Source:** `news-collector/` → `src/news_collector/` + `apps/news_collector_api.py` + `tests/news_collector/`

Hybrid news-link discovery for S&P 500 companies. Given a list of tickers (or none — the
entire S&P 500) and a date range, it discovers article **URLs** (not full article content)
across seven target financial-news domains (CNBC, Yahoo Finance, Financial Times,
Investing.com, Nasdaq, Seeking Alpha, StockTwits) and persists them to a durable SQLite
queue (`data/urls.db` by default — override with `$DATABASE_URL`, see root
`.env.example`; table `discovered_urls`) for [news_crawler](news-crawler.md) to consume.

**Which database the gateway opens.** The collector API and CLI take the path from the request or `--db`, else `$DATABASE_URL`, else `data/urls.db`; an empty value is unset (`data_mining.resolve_db_path`, T-065). On this machine `/etc/environment` sets `DATABASE_URL="../data/urls.db"`, and points at a directory that does not exist; since T-066b the repo-root `.env` wins over it (every entry point calls `data_mining.env.load_env()` first, so `uv run apps/gateway.py` picks the `.env` database from any working directory), and `GET /crawler/status` must still show the real counts before any discovery. A one-off value on the command line (`DATABASE_URL=… uv run …`) now loses to `.env`: edit `.env`, or use a flag applied after the load (`--db`, `--allow-new-db`, a request's `db_path`); a blank `.env` line cannot switch off an inherited variable such as `HTTP_PROXY`, unset it in the system or the shell. At start-up the gateway, the crawler API and the collector API log the effective database (`DATABASE_URL source=… path=… file: … discovered_urls=N rows`) and warn when the environment differs from `.env` (only possible when `load_env()` did not run in the process; `load_env()` itself logs one line per variable it overrides, with the values only for `DATABASE_URL`, `UNIVERSE_DB_PATH`, `DISCOVERY_START_DATE`, `ALLOW_NEW_DB`; a stale `DISCOVERY_END_DATE` is named once and ignored, T-077); the gateway and the crawler refuse to start on a missing file, a missing table or a schema with no rows (`ALLOW_NEW_DB=1` or `--allow-new-db` to start on an empty database on purpose), the collector only on a missing directory or an unreadable file (`data_mining.startup_check`, T-066a). `company_names` is positional (one name per ticker, same length, else 422); when omitted, each ticker's name is looked up by exact symbol in the current S&P 500 (one Wikipedia request, 502 on failure), then, for a ticker that has left the index, in `data/universe.db` as of the request's `start_date` (`query_as_of`, never a partial name match). A ticker found in neither, or a `start_date` the store does not cover, is a 422 that asks for `company_names`. Known limit: a ticker added after `start_date` and removed before today is in neither, so pass `company_names` for it (`T-067`, PR #81). Symbols are matched exactly as Wikipedia writes them (`BRK.B`, not `BRK-B`). A request with `tickers` and no `company_names` now needs the network (Wikipedia) or a populated store; before, it worked offline but stored the ticker as the company. If Wikipedia is down the store alone can still name the tickers it knows (a 502 only for the rest). The lookup never creates `universe.db`: a missing, unreadable or empty store is reported in the 422. The same lookup serves `discover --tickers` of the CLI (`news_collector/companies.py`), which stops with status 1 when a ticker has no name. If Wikipedia is down, a ticker that is also current is named from the store as of `start_date`, so a renamed company gets its name of that date (a warning is logged; the aliases carry the ticker). An empty `company_names` list counts as omitted.

**Work item 15 is closed (2026-10-21, `T-074`): the interim caution about a `cnbc.com` discovery on the real database no longer applies.** `T-071` merged (PR #99) and the first discovery on the real file ran on 2026-10-21 (503 tickers, +8,757 rows, +7.7 MB, no sitemap reject stored: "The real file after Work item 15 (T-074)", below). Before `T-071` the sitemap strategy turned every URL of the cnbc sitemap (about 578,000) into a pair for the ticker and the queue stored the rejected ones (the `KLAC` run of 2026-10-18 added 578,117 rows, +243 MB; a run of all tickers would have added about 61 million rows, roughly 25 GB). Since `T-071` a discovery classes only the token index's candidates from the sitemap cache and stores only the `accept`/`review` pairs.

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
the reason and (for `ddg`) the search `query` (**since `T-071` a sitemap pair read from the sitemap cache that the matcher rejects is
not stored, only counted: see "Matching from the cache (T-071)"; the `ddg`, `rss`, Wayback and `api` rejects are stored as described**). `accept` and `review` rows stay `pending` (a `review` pair
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
`rejected=`: the **newly inserted** rows that are matcher-rejected (`URLQueue.last_inserted_rejected`), and, next to it, the sitemap
rejects that were **not stored** (`total_rejected_not_stored`, T-071). The inserted ones are
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
Expect the same row count before and after, every row `accept`, the index present and the version stamped
(the 481,332-row corpus gave that on 2026-10-15, when the version was 2, and the expected `user_version` was then 2; `initialize()` now also adds
`articles.prev_fetch_status` and, since T-069, the two sitemap-cache tables, and stamps **5** since T-073: the expected
`user_version` is `SCHEMA_VERSION`, 5). The cache migration has its own rehearsal on a copy,
`scripts/rehearse_sitemap_cache_migration.py` (`--source` is opened read-only, `--copy` must not exist).

**After merging the schema-version-4 change (T-069), on the real file** *(historical: done 2026-10-19 at version 4; its expectations, `user_version` 4 and five cache names, are those of that build. For a current build use the schema-version-5 section below, which has its own check)*. Run every command from the repository root; `DB` is the
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
`(domain, started_at)`). It holds no data: the cache tables, the pipeline tables and every row stay as they are.

*Who migrates the file.* The first program that **opens the queue** with the merged code runs the `DROP INDEX IF EXISTS`, whichever it is: the
gateway, the crawler, the extractor, `reclass-sitemap --apply` (the CLI and the API route), the collector CLI. `fill_sitemap_cache.py`,
`reclaim_sitemap_rejects.py evidence`/`reclaim`/`compact` and the migration rehearsal do **not** migrate (the G3 steps leave the file at version 4).
**A restart alone does not migrate:** the gateway opens the queue when a request needs it, not at start-up (on 2026-10-20 the gateway had been up 2.5 minutes after the restart and the file still read `user_version` 4). The check below tells; to apply it at once, make one request that opens the queue, e.g. `GET /discovery/stats`. It is a schema change on the real file (constitution #10), so a backup comes first. Restart the gateway and the crawler on the new code
**together**: a service still running the old code is harmless (it never reads the index), but an old build restarted later re-creates the
index and stamps 4, and the next new-code start drops it again (it looks like a flip-flop in a check, and it is not a fault).

*Which backup.* If the restart follows the G3 steps of "Reclaim the never-fetched sitemap rejects" (the same maintenance window),
`urls_t_073.db` (made before the fill and the reclaim) is the backup of the whole window, G3 and this migration, and the backup
command is not run again (it refuses to overwrite). If the restart is later, or any write must survive a restore, make a new
backup of the file as it is, with another name (`urls_t_073_v5.db`), the same way as step 1 above.

*Rehearse it right before the restart* (read-only on the source, the copy must not exist; it needs the file's size free in `/tmp` and a few minutes for the copy):
`PYTHONPATH=src uv run python scripts/rehearse_sitemap_cache_migration.py --source $BK --copy /tmp/urls_v5_rehearsal.db` with `BK` the
backup (a version-4 file with the index). Every line must read `ok`.
(Rehearsed 2026-10-20 on a copy of a copy of the real file with the cache filled: 0.005 s, pipeline fingerprint identical, version 4 → 5, the
index gone, a second run changes nothing, the rollback restores version 3 with no cache object, `quick_check` ok.)

*Check the real file after the restart* (read-only):
```bash
PYTHONPATH=src uv run python - <<'EOF'
import os, sqlite3
db = sqlite3.connect(f"file:{os.environ['DB']}?mode=ro", uri=True)
print("user_version:", db.execute("PRAGMA user_version").fetchone()[0], "(expected 5)")
print("sitemap objects:", [r[0] for r in db.execute(
    "SELECT name FROM sqlite_master WHERE name LIKE '%sitemap_%' "
    "AND name NOT LIKE 'sqlite_autoindex%' ORDER BY name")])
for t in ("discovered_urls", "articles", "discovery_progress"):
    print(f"{t}: {db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
EOF
```
Expect `user_version: 5`; **four** names (`ix_sitemap_crawls_open`, `ix_sitemap_entries_lastmod`, `sitemap_crawls`, `sitemap_entries`); and the three counts
equal to those of the file just before the restart (after the G3 reclaim: the backup's `discovered_urls` minus the rows deleted, `articles` and
`discovery_progress` unchanged).

*If anything differs:* stop both services, then either restore the backup (copy it over `$DB` with the services stopped, after deleting `$DB-wal` and `$DB-shm` if present),
or run the rollback below and restart on the old code. Keep the backup under the same rule as the `urls.pre-*.db` files (the maintainer deletes it).
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
It refuses a file that is not at version 5, drops the two cache tables and stamps 3: expect `user_version now: 3` and no `sitemap_*` name in the check above.

Every row in `discovered_urls` carries a stable `id` primary key — the crawler holds it as
a foreign key back to this table, rather than re-matching on the `url` string.

## The sitemap cache of a domain (T-070a)

A domain with `sitemap_roots` (today only `cnbc.com`) is crawled **once per domain**, not once per ticker: `news_collector/sitemap_cache.py`
stores the in-range entries in `sitemap_entries` and every ticker reads them. Since `T-071` the matching reads only the token index's
candidates and the `reject` pairs of the cache are not stored ("Matching from the cache (T-071)", below); the first discovery on the
real database ran on 2026-10-21, after a backup ("The real file after Work item 15 (T-074)", below).

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

**The `reject` count drops by design (`T-073`, `T-071`).** The audit counts what is stored. Since `T-073` deleted the 578,113 never-fetched `cnbc.com` sitemap rejects (2026-10-20) and since `T-071` stopped storing a sitemap reject at all, the `reject` rows of `urls.db` went from 903,092 to 324,979 and are 328,075 on 2026-10-21 (323,860 `sitemap` that keep a quarantined article, 4,215 `ddg`). The class counts of `GET /discovery/stats`, of `queue.stats()['by_match_class']` and of this audit's tables drop with them: that is the design (a rejected pair that was never fetched is counted by `rejected_not_stored`, not stored), **not data loss**; `accept` (109,797) and `review` (52,224) did not shrink.

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

**The population this section reclasses shrank by design.** The `reject` rows of the file went from 903,092 to 324,979 at `T-073` (2026-10-20) and are 328,075 on 2026-10-21 (323,860 `sitemap` with a quarantined article, 4,215 `ddg`); the 578,113 deleted rows were never fetched and `reclass-sitemap` re-derives them from the cache without storing them ("Re-class from the sitemap cache (T-072)"). A smaller `reject` count in `GET /discovery/stats` or in this script's dry run is not data loss.

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

## Matching from the cache (T-071)

Before `T-071` every ticker classed **every** entry of a domain's sitemap (the full scan: about 42 µs a pair, 4.6 s a ticker for the
109,493 in-range `cnbc.com` entries) and the queue stored every pair the matcher rejected (one ticker, `KLAC`, added 578,117 rows and 243 MB;
503 tickers would have added about 55 million). Now, when the strategy has a cache run (`SitemapRSSStrategy(connector, client, run)`):

1. `SitemapRun.select(company, ticker, range)` asks the **token index** (`data_mining/token_index.py`) for the company's candidate entries:
   the tokens of the path, the query and the title, one anchor token per signal of the matcher (the name, an alias, the ticker, a
   loose-name spelling, the first word) and the ticker in the title index. The cache builds the index once per crawl (about 1.6 s for 109,493
   entries, in memory, never stored) and every run that reads that crawl shares it; a `SitemapRun` made without one builds it on first use.
2. The candidates are classed by the **same matcher** (`explain_match`, unchanged); only the `accept` and `review` pairs are returned and
   stored. Every other in-range entry is a `reject` by the argument in the module docstring, and is **counted, not stored**:
   `SitemapRSSStrategy.rejected_not_stored = entries in range - pairs returned`.
3. The run's end is applied when the candidates are read (`lastmod <= end`, an entry with no `lastmod` is kept), the pair's `pub_date` is the cached
   `lastmod`, and a URL listed twice in a sitemap keeps the first entry (the cache's rule, as before).

**Equal to the full scan, not "close to it".** The `accept`/`review` pairs, their class, reason, `pub_date`, source and title are the full scan's:
`tests/news_collector/test_match_from_cache.py` (a synthetic corpus with titles and all the matcher's signals, an end date in the past, the
`news:title` fixture, a 578,000-entry cache), `scripts/sitemap_preselect_check.py check` (the real corpus: 503 tickers, 109,493 entries, 38,055
pairs, no pair missing, none extra, none changed) and its mutation tests (the check fails when an anchor family is removed); `check` is `T-068`'s design proof, on its own crawl (2026-10-19) and with the script's own copy of the logic, and `scripts/sitemap_preselect_check.py production` ("Equality on the whole universe", below) is the proof of the code that ships, on the real cache (the crawl of 2026-10-20: 109,550 entries, 57 more than `check`'s 109,493, and 38,104 pairs against 38,055; in each run the two paths agree with each other, and the two runs differ because their crawls differ (57 more entries a day later); the 49 extra pairs were not traced pair by pair). **When the matcher
changes, the index may have to change with it:** a new signal needs an anchor in `TokenIndex.candidates`, and the equality tests fail until it has
one; an alias or a word list that the matcher already reads (`ALIASES`, `COMMON_TICKER_WORDS`, ...) needs nothing. To find what a change that the
index does not know about promotes, class every cached entry: `reclass-sitemap --full-scan` (CLI) or `"full_scan": true` (API).

**What did not change** (guardrail 4): a run with no cache (`cache=None`, the live crawl) stores every class as before; the `ddg`, `rss`, Wayback
and `api` rejects are stored; the first URL wins: a live (RSS, Wayback) entry whose URL the cache holds in the run's range is not read, because the
cached entry came first in the old list (a cached entry outside the range does not block it); the `--resume` checkpoint; `status = 'pending'` for
the fetch; what `portfolio-nlp` reads.

**Where the count is reported.** The strategy's `rejected_not_stored`; `PartialStats.rejected_not_stored` and `DiscoveryStats.total_rejected_not_stored`
(`total_rejected` stays the rejected rows that **were** stored: `ddg`, `rss`, Wayback, a live sitemap, and the rows that already existed); the orchestrator's
per-pair and final log lines; the CLI summary line `Sitemap rejects not stored`; `RunAllResponse.total_rejected_not_stored` (`POST /discovery/run-all`)
and `DiscoverResponse.rejected_not_stored` (the per-domain routes, `/cnbc/`, `/ft/` and the others). `total_discovered` and `sitemap_count` count the
pairs the run **returned**, so they no longer include the sitemap rejects.

**Evidence: a 10-ticker `cnbc.com` run on a copy of the real file, 2026-10-21** (the copy is the file after `T-073`, `urls.db` 481,339 rows, 2,289,635,328 bytes, schema 5, the cache filled
by the G3 fill; tickers `KLAC AAPL ON A IT ARE TTD MSFT NVDA CRM`; `--no-resume`, concurrency 5, DDG off in both runs so the network does not decide the time; the baseline is
`master` before `T-071` (the full scan, with the fixed sitemap date parser of PR #75, reading the same cache), run on its own copy; the real file was only read):

| | baseline (full scan) | `T-071` | `T-071` with `--refresh-sitemaps` |
|---|---|---|---|
| time | **266.2 s** | **4.0 s** | 17.1 s (the crawl of 14 requests is in it) |
| rows added | **1,060,682** (1,060,484 rejects) | **198** (0 rejects) | 198 |
| `accept` / `review` added | +181 / +17 | +181 / +17 | +181 / +17 |
| file | 2,289,635,328 → 2,721,648,640 bytes (+432 MB) | → 2,289,778,688 (+143 KB) | → 2,290,290,688 |
| `sitemap_crawls` | 1 row | 1 row (the cache was fresh) | **2 rows**: the discovery wrote a crawl row (`2026-10-21T00:12:57`), the first run evidence of the `T-070b` guard on a copy |
| sitemap rejects not stored | n/a | 1,090,441 | 1,090,441 |

**The same runs per unit, to compare them.** The workload is the same in all three: 10 tickers × 109,550 cached entries = **1,095,500 pairs evaluated**, and
all three add the same 198 `accept`/`review` rows.

| per … | full scan (before) | `T-071` | `T-071` with `--refresh-sitemaps` |
|---|---|---|---|
| time per pair evaluated | 243 µs | **3.65 µs** | 15.6 µs |
| time per ticker | 26.6 s | **0.40 s** | 1.71 s |
| pairs evaluated per second | 4,115 | **273,875** | 64,064 |
| time per `accept`/`review` row added | 1,344 ms | **20 ms** | 86 ms |
| rows stored per 1,000 pairs | 968.2 | **0.18** | 0.18 |
| rejects stored per 1,000 pairs | 968.0 | **0** | 0 |
| file growth per pair evaluated | 394 B | **0.13 B** | 0.60 B |
| file growth per ticker | 43.2 MB | **14 KB** | 66 KB |

Per pair it is **66 times faster** (15.6 times with the forced crawl, whose 14 requests and index build are in that figure), stores **5,357 times fewer rows** and
grows the file **3,013 times less**; before, 96.8% of the pairs evaluated became stored rows, almost all rejects, and now 0.018% do, none a reject. Part of the old time
is the matcher and part the queue writing 1.06 million rows: the matcher alone costs about 42 µs a pair (`T-068`), 46 s of the 266 s, so about 220 s were the queue
(an inference from that measurement, not a timing taken in this run). The file growth per stored row is not compared: 407 B a row before (the 420 B estimated in
2026-10), and for 198 rows the 143 KB is the granularity of the pages and the WAL, not a rate.

**The `accept`/`review` pairs are the same**: the 17,235 stored `cnbc.com` sitemap pairs of the 10 tickers (`accept`/`name` 16,598, `accept`/`upper_ticker` 515,
`review`/`weak_first_word` 61, `review`/`weak_ticker` 61) are identical in the two runs by URL, ticker, class, reason and `pub_date`. The 1,090,441 counted rejects
reconcile with the baseline: 1,060,484 it stored, 29,941 that were already stored as rejects (29,932 `sitemap`, 9 `ddg`), and 16 stored `accept`/`review` rows that the
matcher now rejects and the queue never demotes: 13 of source `sitemap` (12 `accept`, 1 `review`; `ON` 10, `A` 3: the family accepted at G3) and 3 `accept` of source `ddg`. The
reconciliation is exact: 1,060,484 + 29,957 = 1,090,441 (29,957 = the 34,818 pairs of the 10 tickers already stored that the cache holds, read-only from the real file, that the
matcher now classes `reject`; the other 4,861 stay `accept`/`review`). The run on the real file, the first runtime evidence of the
guard there, is the maintainer's after the merge and a backup.

**Equality on the whole universe (2026-10-21).** Run read-only on the real `cnbc.com` cache (the crawl of 2026-10-20 19:15, 109,550 entries, range from 2022-01-01): the 504
tickers of `data/sitemap_preselect_per_ticker.csv` (plus `TTD`) through the production path (`SitemapRun.select` and the matcher on the candidates) against the matcher on every
entry in range, with the real `CNBCConnector`: **38,104 `accept`/`review` pairs by the index, 38,104 by the full scan, 0 mismatching tickers** (class, reason and `pub_date` per
URL), 644 s on 8 workers. To repeat it (it reads `sitemap_crawls` and `sitemap_entries` with `mode=ro` and writes nothing to the database; memory, measured with `resource.getrusage` on one process that loads the 109,550 entries, builds the index and compares one ticker: 164 MB after the load, **234 MB at the peak**; a pool has one such process per worker plus the parent):

```bash
PYTHONPATH=src uv run python scripts/sitemap_preselect_check.py production --db /path/to/urls.db \
    --tickers-file data/sitemap_preselect_per_ticker.csv --workers 8 --out eq_result.txt
```

The last line is `RESULT tickers N | pairs via index X | pairs via full scan X | mismatching tickers 0`; the exit status is 1 if a ticker differs. Run it after any change to
`TokenIndex.candidates` or to `data_mining.matching`.

**Progress.** `discover` shows a `tqdm` bar over the (company, domain) pairs (the orchestrator calls its `progress` callback once per finished pair;
a callback that raises is logged and never stops the run; `on_start` gives it the number of pairs that will run, so under `--resume` the total is the pending pairs, not the whole grid). The bar is off when the output is not a terminal, so it never writes into a log or a pipe.

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
#   the route needs `tickers` (at most `MAX_RECLASS_TICKERS`, 50 today, counted as sent: the request has no progress and cannot be stopped, and an `apply` writes from the gateway process (about 0.04 s a ticker for cnbc.com, 2 s for 50; with `full_scan` about 4.6 s a ticker));
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
- **Cost.** Since `T-071` the token index preselects the candidates and only those are classed: about **0.04 s a ticker** for `cnbc.com`
  (`select` + classify, measured on the real cache: 0.036 s on average and 0.079 s at most over 20 calls), so 503 tickers take about 20 s of CPU, plus
  the index build (1.6 s, once) and the inserts of the pairs that are new; a `tqdm` bar shows the progress. (A *discovery* takes about 0.4 s a ticker, the
  10-ticker run of "Matching from the cache (T-071)", because it also writes to the queue: do not use that figure for the re-class.) `--full-scan`
  classes every entry instead (the old full scan, about 4.6 s a ticker: 503 tickers take about 40 minutes).
- **Reject pairs** are counted in the output (`reject`) and never stored.
- **Database path.** A path that is not an existing file is an error line and nothing is created, in a dry run and with
  `--apply`. `--apply` opens the queue like a collector does, so on a file below schema version 5 it runs the migration (version 5 drops
  the unused cache index, see "After merging the schema-version-5 change"): on
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
| `scripts/reclass_rejects.py` | the stored rejects that remain: `ddg` (1,115 at G3, 4,215 after the discovery of 2026-10-21), the 323,864 (323,860 on 2026-10-21) sitemap rejects that keep a quarantined article, domains with no cache, the hand-labeled sample rows `T-073` keeps, and the rows stored before `T-071`. Not retired. |
| `scripts/retag_urls.py` | legacy rows with no `match_reason` (unaffected by `T-071`/`T-073`); its `--hand-check` sheet draws its 40 `reject` pairs from the stored rows, so the sitemap ones come from the kept rows (fetched). |
| `scripts/score_ticker_rules.py` | reads the committed labeled sample files, so the kept rows matter (`T-073` never deletes a labeled id); its `--diff` scans the corpus that remains. |
| `scripts/ticker_validation_sample.py` | draws from stored rows by `(class, source)`. After `T-073` the `(reject, sitemap)` stratum is the kept rows, all fetched before the matcher existed, **not** the population that is no longer stored. A tiny or empty stratum draws what exists and prints its size in the sheet (`stratum_size`), and a sheet short of exactly 40 `reject` rows is refused as the T-057 check by `check_sheet` (tests). To look at the pairs the matcher rejects from the cache, the `reject` count of `reclass-sitemap`'s dry run is the number; a hand-check sheet of them has no `discovered_urls` id to key on, so the labeling tools cannot take it (a follow-up if wanted). |

**The 438 known-gap pairs** (`TASKS.md`, "Known gaps"; `PLAN.md` Work item 12), measured read-only on the real `urls.db`
2026-10-20 with the reconstructed rule (a non-first word of the normalized name, 5+ characters, not a stopword, a whole word of
the URL path): the rule still finds **438 pairs on 438 URLs**; **0 are in the set `T-073` deletes** (never fetched, no article,
`sitemap`, `cnbc.com`) and **all 438 are in the kept 323,864** (every one has a quarantined article). So the gap keeps its
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

Three scripts: `scripts/fill_sitemap_cache.py` fills the cache of one domain with no discovery; `scripts/reclaim_sitemap_rejects.py`
(`news_collector/reclaim_sitemap.py`) has **`evidence`** (read-only on any file; prints what G3 judges), **`reclaim`** (deletes, compacts
and swaps) and **`compact`** (finishes a `reclaim` that printed INCOMPLETE). On the real `urls.db` the writes run **only at G3**.

**What qualifies** (one WHERE clause, `candidate_where`): `match_class='reject'`, `status='skipped'`, no `articles` row, source `sitemap`, a
domain whose **latest complete crawl holds entries** (a complete crawl of an empty urlset is not a filled cache), and an id that no committed hand-labeled sample names (`data/ticker_*_sample.csv`, `ticker_*_manual.csv`:
593 ids; **`reclaim` refuses if a kind of sample file is missing or names no id**, prints the number it protected, and `--expect-kept 593` pins it).
Source `rss` is **not** in the set: the proof re-derives stored pairs from the sitemap cache, which no `rss` pair can be (0 `rss` rows qualified on
the rehearsal copy; add it back only with a proof that covers it). Nothing else is ever deleted: the 323,864 sitemap rejects that keep a quarantined
article, the `ddg` rejects (1,115 then; 4,215 since the discovery of 2026-10-21) and every `accept`/`review` row stay.

**The two guards are never inferred from the environment.** `reclaim`, `compact` and `fill_sitemap_cache.py` take exactly one of
`--i-have-the-g3-approval` (the real file: the process and open-file checks run, whatever the file is called) and `--rehearsal-copy` (a copy: a file
called `urls.db`, or the one `DATABASE_URL` names, is refused as a copy). Typing the real path without either flag does nothing. The three commands read the pair the same way (one shared gate), and a mistyped `--db` (a missing file or a folder) is `REFUSED, nothing changed: no database at ...` (exit 1) in `reclaim`, `compact`, `evidence` and the fill, never a traceback.

**What `reclaim` does.** Before it writes anything it refuses unless: the sample ids are found; `--backup` is another file that passes `quick_check`
and has the live file's fingerprint (counts, class counts, orphan articles, `user_version`, the sum and the highest `discovered_urls.id`, a hash of every
table and index definition); `--expect N` names the approved count (a different count deletes nothing); the disk has the file size plus
`WAL_HEADROOM` free; no `<file>.compact` is left; no process holds the file (a `pgrep` by name and a scan of `/proc/*/fd`); and it can take the file
**exclusively**: in WAL mode that fails while any other connection on this host has the file open, even an idle one, whoever owns it, and while it holds
the lock no other program can read or write the file. Then one transaction deletes the rows and checks the fingerprint (`discovered_urls` down by exactly
the deleted count, `articles` unchanged, no orphan, `accept`/`review` unchanged; any failure **rolls back**), the commit, `VACUUM INTO` (the path is a bound
parameter) writes the compacted file, which is checked against the same fingerprint, put back in the original journal mode (`VACUUM INTO` writes a
rollback-journal file, the queue's is WAL) and given the original's mode bits and owner, and it is swapped in. **The lock is held through all of that
checking** (a `quick_check` of the 2.3 GB compacted file takes about 13 s, and a program that opened the file in those seconds would have its writes thrown away
by the rename); only the unlink of `-wal`/`-shm` and the rename follow the close, and a last cheap read of the file in place (`user_version` and the row counts of
`discovered_urls` and `articles`, equal to the checked ones) ends the step (`Final check` in the output; if it fails the step is `INCOMPLETE`: restore the
backup). The old file is the backup, kept until the maintainer deletes it.

**Two kinds of failure, two exit codes.** Exit 1, `REFUSED, nothing changed`: nothing was committed, the file is as it was. Exit 2, `INCOMPLETE: the
delete of N rows is COMMITTED ...`: the commit succeeded but `VACUUM INTO` or the swap failed (a full disk, a compacted file that differs); the file has
lost the rows and is not compacted, the partial `.compact` is removed, and the backup is intact. A second `reclaim` cannot finish it (0 rows qualify now):
run `compact --db ... --backup ... --i-have-the-g3-approval`, which compacts and swaps with no delete (the backup must exist and pass `quick_check`; it is not compared, the
file has lost the rows it still has) and leaves the file as it was if it fails too (`REFUSED, nothing changed: the file is as it was (...)`), or restore the backup. Any SQLite or OS
error before the commit is also a `REFUSED` (exit 1, "rolled back"), never a traceback; and the backup is compared to the file **again under the lock**: a program
that wrote between the check and the lock makes `reclaim` refuse ("make the backup again"). The CLI prints a WARNING when the new file's owner is not the original's (the user running it could not `chown`): fix it before restarting a
service that runs as another user.

```bash
PYTHONPATH=src uv run python scripts/fill_sitemap_cache.py --db COPY.db --domain cnbc.com --rehearsal-copy
PYTHONPATH=src uv run python scripts/reclaim_sitemap_rejects.py evidence --db COPY.db          # read-only
PYTHONPATH=src uv run python scripts/reclaim_sitemap_rejects.py reclaim --db COPY.db --backup COPY.pre.db --expect N --rehearsal-copy
```

**The real file, step by step (G3).** The maintainer runs 1-3 and 5-6 or tells the developer to; nothing below runs before the G3 approval. The code
cannot see another machine: **the maintainer confirms that no program on any machine opens `urls.db`** (a notebook, a `sqlite3` shell, the thesis
machine's gateway). `DB` is the real file, `BK` its backup (`urls_t_073.db`, next to it). `fill`, `evidence` and `reclaim` do not migrate the file,
so it stays at schema version 4 until a service starts on the merged code (the version-5 migration of PR #96 then drops the unused index).

1. **Stop** the gateway and the crawler (and anything else that opens `DB`). Check: `pgrep -af "gateway|news_collector|crawler"` prints nothing.
2. **Backup** with SQLite's backup API (never `cp` on a WAL file), refusing to overwrite:
   ```bash
   export DB=/workspaces/thesis/data/urls.db BK=/workspaces/thesis/data/urls_t_073.db
   PYTHONPATH=src uv run python - <<'EOF'
   import os, sqlite3, sys
   db_path, backup_path = os.environ["DB"], os.environ["BK"]
   if os.path.exists(backup_path):
       sys.exit(f"{backup_path} exists: not overwriting")
   src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
   dst = sqlite3.connect(backup_path)
   src.backup(dst)
   print("backup quick_check:", dst.execute("PRAGMA quick_check").fetchone()[0], "->", backup_path)
   src.close(); dst.close()
   EOF
   ```
3. **Fill the cache** (writes only `sitemap_entries` and `sitemap_crawls`; the backup is verified against the file first):
   `PYTHONPATH=src uv run python scripts/fill_sitemap_cache.py --db $DB --domain cnbc.com --backup $BK --i-have-the-g3-approval`. Takes about 18 minutes on the thesis disk (the backup is verified first: two `quick_check` passes, then the crawl and the fingerprints; 19:02 to about 19:20 UTC on 2026-10-20, and `urls.db` is first written about 14 minutes in, so no change in the file does not mean it is stuck). Expect
   `Crawl: made now` (about 14 requests to cnbc.com: the `sitemapAll.xml` index and its 13 children on 2026-10-20), about 109,000 entries, `Pipeline tables: unchanged`, exit 0. An INCOMPLETE crawl exits 1 and leaves the cache empty (the same
   command may be run again; a complete, fresh crawl is reused).
4. **Evidence on the real file** (read-only): `... reclaim_sitemap_rejects.py evidence --db $DB --expect-now-rejected 17`. G3 judges **these** numbers, not the
   rehearsal's: qualifying rows (`N`), the split, the proof, the protected ids. The 17 stored pairs the matcher now rejects are the ones the maintainer accepted
   (2026-10-20): `--expect-now-rejected 17` makes the exit status 0 for exactly 17 and 1 for any other count, so a different set is never read as the accepted one.
5. **Reclaim**, only with the maintainer's approval of `N`: `... reclaim_sitemap_rejects.py reclaim --db $DB --backup $BK --expect N --expect-kept 593
   --i-have-the-g3-approval`. **About 25 minutes on the thesis disk** (five `quick_check` passes; 1,497 s on 2026-10-20; about a minute on a copy in `/tmp`).
6. **Check by hand**, read-only: `discovered_urls` is the old count minus `N`, `articles` unchanged, `PRAGMA quick_check` ok, `user_version` still 4, the
   file's header bytes 18-19 are `02 02` (WAL). On the thesis machine `urls.db` is owned by `root` (mode 0777, in a folder everyone can write) and the tool runs as
   `vscode`, so `reclaim` prints the owner WARNING even on success: that is expected there and needs no fix, the services run as `vscode`. Then **restart** the
   services on the merged code and run the check of the section "After merging the schema-version-5 change (T-073)" above (`user_version` 5, the four names, the
   three counts).
7. **If something is wrong:** `REFUSED` (exit 1): nothing changed. `INCOMPLETE` (exit 2): `compact --backup $BK` as above, or restore. Anything else: stop every
   service, delete `$DB-wal` and `$DB-shm` if present, copy `$BK` over `$DB`, restart. `$BK` is kept until the maintainer deletes it (the
   `urls.pre-*.db` rule: after a complete collector run and a complete extractor run at the new state, or 14 days, whichever is later).

If `DB` is a single-file bind mount (a container that mounts `urls.db` itself and not its folder), the final rename fails with "device or resource busy":
mount the folder.

**Rehearsal on a copy of the real file, 2026-10-20** (cnbc cache filled on the copy: 109,550 entries, range 2022-01-01 to 2026-10-20; the real file
was only read through SQLite's backup API):

| step | result |
|---|---|
| qualifying rows | **578,113**, all `cnbc.com`; the hand-labeled ids remove 0 (as measured 2026-10-18) |
| split | in the cache (in range): 109,452; **before the range: 468,661** (2006 to 2021); **left the sitemap: 0** |
| `reclaim` | deleted 578,113 in 65 s; `discovered_urls` 1,059,452 → 481,339; `articles` 481,332 unchanged, 0 orphans; `accept` 104,614 and `review` 51,746 unchanged; rejects 903,092 → 324,979 (323,864 with an article + 1,115 `ddg`); `quick_check` ok; `user_version` 4; the cache tables (109,550 entries) intact |
| file | 2,570,055,680 → 2,289,635,328 bytes: **280 MB reclaimed** (the estimate was 243 MB) |

**Second rehearsal after the review of PR #95, 2026-10-20** (a fresh copy of the real file, the new tools only: `fill_sitemap_cache.py --rehearsal-copy`, then
`evidence`, then `reclaim --expect 578113 --expect-kept 593 --rehearsal-copy` with a backup made before the fill): the fill made the crawl (109,550 entries,
`Pipeline tables: unchanged`, 1,059,452 and 481,332 before and after); `evidence` gave the same split (578,113; 109,452 / 468,661 / 0) and the same 17
pairs; `reclaim` deleted 578,113 in 61.6 s, 593 ids protected, `discovered_urls` 1,059,452 → 481,339, `articles` 481,332, 0 orphans, `accept`/`review`
unchanged, the file 2,570,055,680 → 2,289,635,328 bytes (280 MB). **The WAL peaked at 299 MB** during the delete, which is why the disk check asks for the
file size plus 512 MiB (`WAL_HEADROOM`). The ordinary copy of the 2.5 GB file took about 5 minutes on this machine when the disk was busy.

**The real run (G3), 2026-10-20.** Maintainer approval "APPROVE N=578113" after the real `evidence`; the gateway and the crawler stopped, no open handle on the file.

| step | result |
|---|---|
| backup `urls_t_073.db` (SQLite backup API) | 207 s, `quick_check` ok; 1,059,452 / 481,332 / 3,534 rows, `user_version` 4 |
| `fill_sitemap_cache.py` on the real file | 109,550 entries (complete, 0 failures, range from 2022-01-01); the pipeline tables unchanged |
| `evidence` on the real file | qualifying **578,113** (`cnbc.com`), 593 protected ids remove 0, in the cache 109,452, before the range 468,661, left the sitemap 0; the proof gave the same 5,254 / 14,595 / 17 as the rehearsals (`--expect-now-rejected 17`: exit 0) |
| `reclaim --expect 578113 --expect-kept 593 --i-have-the-g3-approval` | deleted 578,113; `discovered_urls` 1,059,452 → **481,339**; `articles` 481,332 unchanged, 0 orphans; `accept` 104,614 and `review` 51,746 unchanged; rejects 903,092 → 324,979; file 2,570,055,680 → **2,289,635,328** bytes (280 MB); WAL peak 299,037,872 bytes; `Final check` ok; exit 0 |
| the `--revert` list of the `T-062` run | `data/retag/reclass_promoted.csv`, 448 ids: all 448 rows are still present as `review` after the reclaim (and were in the backup): a `--revert` still finds every row it needs (checked 2026-10-20) |
| after | `quick_check` ok on the file in place (238 s); WAL mode; the backup untouched; the cache (109,550 entries) intact; the owner is now `vscode` (it was `root`, mode 0777 kept): the expected WARNING |

The space freed, 280 MB, is above the 243 MB the task expected (the growth of the `KLAC` run, 2.27 → 2.51 GB, which is what the rows cost when they were added); the 37 MB difference was not measured, and compaction also releasing free pages is the likely cause. It took **25 minutes (1,497 s)** on the thesis disk, against 38 s on the copies in `/tmp`: `reclaim` makes five `quick_check` passes (the live file, the backup, the file under the lock, after the delete, the compacted file) and each one reads 2.5 GB at the speed of that disk. The lock is held through all of it, nothing is written before the delete, and the first sign of progress is the `-wal` growing to about 299 MB (the delete) and then `urls.db.compact` appearing. Plan for half an hour.

**The proof that the cache re-derives the stored `accept`/`review` pairs** (20 tickers: `KLAC AAPL ON A IT ARE TTD MSFT NVDA CRM` and 10 seeded at
random): 19,866 stored `sitemap` `accept`/`review` pairs; **5,254 are in the cache and re-derived**; 14,595 are not in the cache (13,686 before the range,
909 whose URL left the sitemap): they stay stored and are never part of the delete; **17 are in range, in the cache and classed `reject` by the
current matcher** (`ON`, `A`, `GL`, `ALL`: market-wrap slugs such as `stocks-making-the-biggest-moves-midday-…-on-…`, where the ticker is an English word;
stored `accept`/`review` on 2026-08-10 to 12 by the matcher of that day). The plan's pass condition says "0 in-range pairs missing". Read literally it is **not
met by 17 pairs**; it is a change of the matcher, not a loss of the cache (the URL is in the cache and `reclass-sitemap` sees it), and `reclaim` never
touches an `accept`/`review` row. **Decided by the maintainer 2026-10-20: the 17 pairs count as passing the proof** (a matcher change, not a cache loss); the 17 rows stay stored as they are.

## The real file after Work item 15 (T-074)

Recorded 2026-10-21, read-only on `/workspaces/thesis/data/urls.db`, after the first real discovery.

- **Schema.** `user_version` 5. The sitemap objects are `sitemap_entries`, `sitemap_crawls`, `ix_sitemap_crawls_open` and `ix_sitemap_entries_lastmod`; `ix_sitemap_crawls_latest` is gone (the index decision of `T-073`: dropped, version 5). `quick_check` is `ok`.
- **First discovery on the real file** (2026-10-21 16:49 to 19:11 UTC, backup `urls_t_071.db` taken first and verified): `discover --domains cnbc.com`, 503 tickers, range 2022-01-01 to 2026-10-21, the cache of 2026-10-20 (fresh, so **no crawl row was written: the `T-070b` guard was not exercised on the real file**; it first runs when the cache is older than 7 days or with `--refresh-sitemaps`). `discovered_urls` 481,339 → **490,096** (+8,757: 5,272 from the sitemap, all `accept`/`review`, and 3,485 from `ddg`, of which 3,100 `reject`), `articles` unchanged (481,332), 503 checkpoints, 108 existing rows promoted, file +7.7 MB, **55,065,566 sitemap rejects counted and not stored** (the old path stored about a thousand per ticker-pair). The search providers answered 403/429 to about 1,300 DDG queries; each was a warning and the pair was still processed, so the `ddg` count is lower than a normal run's.
- **Idempotent.** On a copy of the real file the same 10-ticker discovery run twice with `--no-resume` inserted 198 rows and then 0; the fingerprints of `discovered_urls`, `sitemap_entries` and `sitemap_crawls` were identical after the second run (`discovery_progress` rewrites `completed_at` and `inserted_count`: a checkpoint, not a row).
- **Class counts today.** `accept` 109,797, `review` 52,224, `reject` 328,075 (323,860 `sitemap` that keep a quarantined article, 4,215 `ddg`).
- **Backups** (the maintainer deletes them): `urls_t_073.db` (before `T-073`) until a complete collector run and a complete extractor run at the new state, or 2026-11-03, whichever is later; `urls_t_071.db` (before the first discovery) stays until the maintainer deletes it.
