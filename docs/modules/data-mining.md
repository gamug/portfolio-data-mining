# data_mining — shared connection/schema layer + S&P 500 universe

**Source:** `portfolio-common`'s `business_folders/data_mining/` → `src/data_mining/`
(landed here in `portfolio-common`'s clean-break v1.0.0 rewrite, since this repo was
confirmed as its only consumer — `docs/portfolio-common-v1-migration-plan.md`)

Not a service — no `apps/`/`cli/` entrypoint of its own. A shared, in-repo library
imported by `news_collector`, `extractor`, and `pricing` for two unrelated concerns that
happen to both need "a SQLite file plus the tracked S&P 500 list": the `urls.db`
connection/schema layer, and the universe loader (live + point-in-time).

| File | Provides | Used by |
|---|---|---|
| `db.py` | `connect()`, `resolve_db_path(value=None)` — `data/urls.db`'s connection factory. `resolve_db_path` returns the value, else `$DATABASE_URL`, else `data/urls.db`; an empty or blank string is unset (T-065), and `news_collector`'s `URLQueue` refuses a missing path. Thin wrapper over `portfolio_common.db.Database.connect()`: this module only owns the domain defaults (`DEFAULT_DB_PATH`, `BUSY_TIMEOUT_MS`) and which pragma flags a caller may ask for (`wal`, `foreign_keys`, `check_same_thread`, `uri`). | `news_collector` (`wal=True`), `extractor` (`foreign_keys=True`) |
| `env.py` | `load_env(path=None)`, `root_env_file()`, `LOGGED_VALUES` — loads the repo-root `.env` (found by walking up to `pyproject.toml`, not the working directory) so that each non-empty value **wins over the inherited environment** (T-066b, constitution rule 7); a missing file, an absent variable or a blank value leaves the inherited one; one warning per overridden variable, values printed only for the allow-list; the lines are kept (`override_notices()`) because `load_env()` runs before the run's file handler exists, and `startup_check.log_again()` (called by the collector app right after `setup_logging`, which comes after `check_startup`) logs them again, with the start-up report, as `.env override: …` so they are in the `.log/` trace. Called first by every `apps/*.py`, `cli/*.py` and `news_collector/main.py` (`tests/data_mining/test_env_entrypoints.py` checks it with the AST); `tests/conftest.py` points `root_env_file` at a missing file so no test reads the real `.env`. `news_collector/config.py` no longer loads `.env` at import (T-077): a script that imports it without an entry point calls `load_env()`. `RETIRED` (T-077) lists variables the code no longer reads (`DISCOVERY_END_DATE`): a non-blank value in `.env` or the system environment gets one notice by name, once per process (not applied, value never logged); `replay_notices(logger)` logs every kept notice again under its own label (`.env override`, `retired variable`), called by `startup_check.log_again()` and by `news_collector.main discover` after `setup_logging` |
| `startup_check.py` | `check_startup(creates_db=)`, `inspect_database()`, `StartupCheckError`, `apply_allow_new_db_flag()` — the start-up check of the shared database (T-066a): logs the effective `DATABASE_URL` (source `.env`/inherited/default, absolute path, size, `discovered_urls` rows), warns when the environment differs from `.env` (after `load_env()` they agree and the source is `.env`; the warning means `load_env()` did not run in the process), and refuses a database that cannot be the real one. Strict (`creates_db=False`): missing file, missing table or no rows; lenient (`creates_db=True`): missing directory or an unreadable file. The first call per process inspects the file (a `COUNT(*)`, 0.65 s on the real one) and later calls reuse that report. `ALLOW_NEW_DB=1` or `--allow-new-db` lets the strict services start on an empty database. | `apps/gateway.py`, `apps/news_crawler_api.py` (strict), `apps/news_collector_api.py` (lenient), in their lifespans |
| `schema.py` | `apply_schema()`, `run_migrations()`, `SCHEMA_VERSION`, `ARTICLES_SCHEMA`/`ARTICLE_COLUMNS` (`SCHEMA_VERSION` 5: `discovered_urls.match_class`/`match_reason`/`query`, T-056b; `articles.prev_fetch_status`, T-057; the two additive sitemap-cache tables `sitemap_entries` and `sitemap_crawls`, T-069, empty until `T-070a` fills them; version 5, T-073, drops the unused index `ix_sitemap_crawls_latest`; rolled back by `rollback_sitemap_cache()`, which no application path calls) — the canonical `discovered_urls`/`discovery_progress`/`articles` DDL, one source of truth instead of a copy per pipeline stage. Schema DDL goes through `Database.create_schema`; the `discovered_urls.id` autoincrement token is filled from `db.dialect.autoincrement_pk` (portfolio-common v1.2.1 engine-agnostic seam). | `news_collector`, `extractor` (both call `apply_schema`/`run_migrations` before their first query) |
| `portfolio.py` | `load_universe()`, `list_universe()`, `resolve_symbol()`, `is_tracked()` — the tracked S&P 500 universe: a live scrape of Wikipedia's "List of S&P 500 companies," cached in-process for the life of the process. With `as_of=None` (the default), touches no database at all. | `news_collector`/`extractor` discovery, `pricing`'s `/universe` routes |
| `universe_history.py` | `backfill_from_changes()`, `record_snapshot()`, `query_as_of()`, `resolve_as_of()` — point-in-time membership. Reconstructs `valid_from`/`valid_to` intervals per ticker by combining today's live roster with the ["Historical components of the S&P 500"](https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500) Wikipedia article's addition/removal log (back to 1976). | `pricing`'s `as_of=` path only (`list_universe`/`resolve_symbol`, `/universe` routes); never called from the `as_of=None` default path |
| `queries.py` | The only SQL text for `data/universe.db`'s `universe_membership` (SCD-2) table — `write_intervals`, `fetch_open_symbols`, `close_intervals`, `query_membership_as_of`, `fetch_gics_targets`, `update_gics`, etc., and `universe_db_path()` (the file `connect()` opens by default, `$UNIVERSE_DB_PATH` else `data/universe.db`, so a caller can check it exists before a read that must not create it). Pure DB-touching functions; `universe_history.py` holds the parsing/reconstruction logic and orchestrates these. | `universe_history.py`, `universe_gics.py` |
| `universe_gics.py` | `build_gics_db()`, `fill_gics()`, `fetch_revision_list()`/`fetch_revision_content()` — fills `gics_sector`/`gics_sub_industry` plus the revision citation for the S&P 500 leaver stints, from the revision history of Wikipedia's ["List of S&P 500 companies"](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies) page. | `cli/pricing_cli.py`'s `universe-gics` subcommand only |
| `nyse_calendar.py` | `is_trading_day()`, `sessions()`, `nyse_holidays()`, `next_trading_day()`, `add_trading_days()` — the NYSE trading calendar, ported from `portfolio-financial-analysis`'s `src/kg_schema/trading_calendar.py` (commit `87dd9bf`), used for the leaver-prices "expected sessions" counts and the off-calendar guard. | `leaver_prices.py` |
| `leaver_prices.py` | `fetch_leaver_prices()`, `build_series()`, `detect_non_real()`, `split_cum_factors()`, `LINES` — the one-off, **private** Tiingo fetch for the 33 S&P 500 leaver lines (FR-015): a CSV + JSON manifest into `/workspaces/thesis/data/private/tiingo/`. Never a service source. | `cli/pricing_cli.py`'s `leaver-prices` subcommand only |
| `leaver_calibration.py` | `fetch_calibration_phase_a()`, `fetch_calibration_phase_b()`, `verify_cik()`, `CALIBRATION_LINES` — the one-off, **private** Tiingo fetch for the 32 pre-2022 leaver lines (FR-016; reviewer A's GUARD calibration), reusing `leaver_prices`'s trims, split formula, rate limiter and key handling: a CSV (with `split_cum`) + JSON manifest into `…/tiingo/calibration_2019_2021/`. Never a service source. | `cli/pricing_cli.py`'s `leaver-prices-calibration` subcommand only |
| `token_index.py` | `TokenIndex`, `entry_text()`, `Row` — the in-memory index of the entries of a domain's sitemap cache (tokens of the path, query and title; a second one for the title) that preselects, per company, the candidate entries the matcher must class (`T-068`, `T-071`): it never decides a class, and its equality with the full scan is tested; it imports `matching` and nothing else. | `news_collector.sitemap_cache` (`SitemapRun.select`), `scripts/sitemap_preselect_check.py` |
| `matching.py` | `classify()`, `Match`, `MatchClass`, `ALIASES`, `WORD_TICKERS` — the pure company-matching rule (accept / review / reject from the URL path and title; no I/O, no dependency), shared by the news-collector connectors (`BaseConnector.classify_match`) and, later, the extractor's post-fetch re-evaluation (`T-056c`). | `news_collector` connectors; `scripts/audit_ticker_tagging.py`, `scripts/score_ticker_rules.py` |
| `quarantine.py` | `quarantine_articles()`, `release_articles()`, `sync_quarantine()` — the reversible quarantine of fetched articles whose pair is not `accept` (`fetch_status = 'quarantined'`, previous status in `articles.prev_fetch_status`; T-057). | `extractor.db.record_fetch`; `scripts/retag_urls.py` |
| `errors.py` | `UpstreamDataError` — the shared exception type for an upstream provider (Finnhub, yfinance, SEC EDGAR) failing in an *unexpected* way. Most consumer wrappers already return `{"success": False, "error": ...}` instead of raising (constitution: AI behavior #3); this is the safety net for anything that raises instead, paired with a FastAPI exception handler so a route never leaks a raw traceback. | `pricing`, `sec_edgar` |

## Two physically separate databases, on purpose

`data/urls.db` (`db.py`/`schema.py`) and `data/universe.db`
(`universe_history.py`/`queries.py`) are deliberately not the same file.
`pricing`'s default behavior (`as_of` omitted) has zero database dependency —
a live scrape plus an in-process cache, nothing else. Point-in-time
membership needed its own persistent store, but bolting that onto `urls.db`
would have made *every* `pricing` request depend on the pipeline database
existing and being reachable, even when `as_of` is never used. Keeping
`universe.db` separate means that property holds regardless (see
`docs/modules/pricing.md`).

## Backfill and snapshot are explicit, not automatic

`universe_history.backfill_from_changes()` (one-time) and
`record_snapshot()` (periodic) are only ever invoked by
`cli/pricing_cli.py`'s `universe-backfill`/`universe-snapshot` subcommands —
nothing calls them from a request path or a scheduler. This repo has no
scheduler and isn't meant to run as production software (constitution:
Executable cmds #1); see `SPEC.md` §13 for the open question this leaves
(a stale `universe.db` between manual snapshots).

## Leaver GICS backfill (`universe-gics`, FR-013)

The live snapshot classifies only today's members, so
`universe_history.backfill_from_changes()` leaves every leaver's
`gics_sector`/`gics_sub_industry` empty. `universe_gics.fill_gics()` fills
them for the leaver stints (`valid_to >= 2022-01-01`, CIK with no open
stint) from the revision history of Wikipedia's "List of S&P 500 companies"
page: for each stint it takes the last revision dated on or before the
stint's `valid_to` that still lists the company, matched by CIK **and**
symbol (CIK alone is not unique — Discovery's DISCA/DISCK share one, later
reused by WBD), and copies the label verbatim. A pre-2023-03-17 label is
never translated into today's GICS structure. The citation (MediaWiki
revision id and timestamp) goes in the two additive `universe_membership`
columns `gics_revision_id`/`gics_revision_timestamp`, written by a
stint-keyed UPDATE — never `write_intervals` — so every row the fill does
not touch keeps them NULL. Those columns are added to the builder's output
copy only (`queries.migrate_gics_columns`); the read path never migrates, so
the gateway's `/universe` route never alters the sha1-tracked file it opens
(an old-schema file reads with both fields as `None`). A row the parser
cannot map (cell count != header) is counted, and the builder raises rather
than let it read as "company not listed".

It is an explicit build step, run **after** `universe-backfill`:

```bash
uv run cli/pricing_cli.py universe-gics \
  --source /workspaces/thesis/data/universe_history.db \
  --out /workspaces/thesis/data/universe_history_gics.db
```

`--out` must differ from `--source` and must not be named
`universe_history.db`/`universe.db`, so the source and the default universe
DB are never written. The output is built on a temporary sibling and swapped
in only on success, so a failed run leaves no half-built file, and a missing
`--source` is a clean error. The command is idempotent: when `--out` already
exists it is the starting point and `--source` is ignored (the report's `base`
is `"out"`, and a warning is logged if stints were still pending; delete `--out`
to rebuild from a fresh source). A stint that already has a citation is skipped,
so re-running leaves a filled file unchanged (and, once every target is filled,
makes no network call at all — the delivered file has three unfilled targets,
which a re-run re-attempts). The MediaWiki requests send a `User-Agent` built
from the `NAME`/`EMAIL` env vars (see `.env.example`); the command fails fast
with a clear message if either is unset or still a placeholder, rather than
send a fake contact. It also runs a
report-only stability check (each chosen label vs. the same CIK+symbol in
the newest revision ≥30 days older) and lists any differences rather than
silently trusting them.

## Leaver prices fetch (`leaver-prices`, FR-015)

`leaver_prices.fetch_leaver_prices()` fetches Tiingo's daily EOD rows for the
33 S&P 500 leaver lines the gateway cannot serve (`INFO`/`WRK` are
unobtainable), over `2019-01-02 .. latest valid_to` from
`universe_history_gics.db` (read-only). It applies the Task B feasibility
report's trim rule and split formula, then writes a CSV and a JSON manifest
into `/workspaces/thesis/data/private/tiingo/` — outside every git
repository, `0700`/`0600`, with the effective modes and filesystem type
recorded (on this machine `chmod` is a no-op over `v9fs`). It is the one
sanctioned one-off, private build-step source (constitution: Technological
stock #8): no service serves it, nothing is committed or redistributed, and
the key is read only from `$TIINGO_API_KEY` and sent only in the
`Authorization: Token` header.

Every check runs before anything is written — the report §2/§3 reproduction
per line (row count, first/last date, trim set **and reason class**, last real
close), every row's date on the NYSE calendar, the gateway's candle count
(0 for all 33 — a non-zero count stops the run: one vendor per line), the
calendar's expected-session count, the TTD/PVH/KMX gateway controls, the AAPL
dividend formula, and the WBD-vs-production comparison. A mismatch writes
nothing and stops. Every price lives in a private `expectations.json` (never
committed); committed code and tests carry counts, dates and trim reasons
only. A `request_log.json` in the same folder persists the rolling-hour
request timestamps (timestamps only), so a crash and a re-run within the hour
do not start the counter from zero; the manifest reports this run's count, the
total over all runs, and the largest rolling hour. `/tmp/tiingo_feasibility/`
is deleted once the file passes.

```bash
# The key is set in the shell only, never in .env:
read -rs TIINGO_API_KEY && export TIINGO_API_KEY
uv run cli/pricing_cli.py leaver-prices
```

## Leaver calibration fetch (`leaver-prices-calibration`, FR-016)

`leaver_calibration.fetch_calibration_phase_a()` / `_b()` fetch Tiingo's daily
EOD rows for the **32 pre-2022 leaver lines** reviewer A calibrates the GUARD
layer on (`2019-02-01` or the first membership day .. the latest `valid_to`,
inclusive), into a **second** private file at
`/workspaces/thesis/data/private/tiingo/calibration_2019_2021/`. It reuses
`leaver_prices`'s trim rule, split formula, rate limiter and key handling; the
CSV adds `split_cum` per row so `divCash / split_cum` puts a raw dividend on
the adjusted price basis (the 2026-10-15 AAPL control). Reused Tiingo symbols
map to the old security's own series (`FOX→TFCF`, `FOXA→TFCFA`, `ARNC→HWM`,
`ADS→BFH`, `DWDP→DD`, `HFC→DINO`); a line whose ticker was reused by a later
company (APC/STI/NFX) is reported as not delivered with the sessions it
covers, never proxied. The manifest records the EDGAR-verified CIK (submissions
JSON + in-span cover page; no document stored), Tiingo's name and dates, the
rows kept / expected / missing / trimmed, `last_equals_exit` (false only for a
documented short tail: trading ended before the index exit), dividend and split
counts, and every adjusted close-to-close move over 25% with its known cause.

The 64 requests exceed the 50/hour cap, so the run is two phases: **a** spends
49 (32 price series + 17 metadata), **b** spends the remaining 15 after the
rolling hour and writes the outputs. Raw responses are cached in a private
`.raw/` folder inside the calibration directory and removed once the manifest
is written; a failed check keeps the cache, so a re-run spends no request.

```bash
uv run cli/pricing_cli.py leaver-prices-calibration --phase a
# wait until the rolling hour has cleared
uv run cli/pricing_cli.py leaver-prices-calibration --phase b
```

The one procedure covering every copy (this machine, T-100's machine if
different, any `thesis/data` backup, and the scratch folder) is recorded in
the manifest's `deletion_step` — every path rooted (absolute or a
placeholder-rooted absolute), never a relative `rm -rf`.

## The schema version, for the programs that read `urls.db`

`PRAGMA user_version` is stamped by `apply_schema()` (`SCHEMA_VERSION`, 5 since T-073). Every change to `discovered_urls` and `articles` so far is additive and
forward-only (a column or a table is added; nothing is renamed or removed; version 5 removed only an unused index of the private cache), and a change to `discovered_urls` or `articles` is a
shared-schema change that needs the maintainer's approval (constitution rule 10). A reader should therefore **require a minimum
and accept anything higher** (an exact match would break on a rollback), check the version at every open, and check the columns it
selects. The version is **not monotonic**: `rollback_sitemap_cache()` sets the current version back to 3, and an older build started on a newer file
re-stamps its own version. The contract is `discovered_urls` and `articles`; `discovery_progress`, `sitemap_entries` and `sitemap_crawls` are
private to this repository.

## Engine-agnostic since `portfolio-common` v1.2.1

As of `docs/portfolio-common-v1.2-engine-agnostic.md` (PR #26), no file in
this package imports `sqlite3` directly — `schema.py`'s DDL execution,
`PRAGMA user_version` read/write, and `PRAGMA table_info` migrations go
through `Database.create_schema`/`set_schema_version`/`table_columns`/
`ensure_columns`; `queries.py`'s writes go through `conn.dialect.upsert`.
A database-engine change is a `portfolio-common` `Dialect` implementation
plus a re-pin in `pyproject.toml`, not a change to this package
(constitution: Technological stock #4).

## Testing

```bash
uv run pytest tests/data_mining -q
```

Hermetic — the Wikipedia scrape (`portfolio.py`/`universe_history.py`) and
the MediaWiki revision API (`universe_gics.py`) are mocked at the HTTP
boundary, and `db.py`/`schema.py`/`queries.py` run against a temporary
SQLite file, not the real `data/urls.db`/`data/universe.db`. The leaver-prices
fetch (`test_leaver_prices.py`) mocks Tiingo and the gateway with an
`httpx.MockTransport` and hand-made synthetic responses (never a real Tiingo
value), and `test_nyse_calendar.py` is pure date arithmetic.
