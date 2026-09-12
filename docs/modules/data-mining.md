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
| `db.py` | `connect()`, `resolve_db_path()` — `data/urls.db`'s connection factory (`$DATABASE_URL`). Thin wrapper over `portfolio_common.db.Database.connect()`: this module only owns the domain defaults (`DEFAULT_DB_PATH`, `BUSY_TIMEOUT_MS`) and which pragma flags a caller may ask for (`wal`, `foreign_keys`, `check_same_thread`, `uri`). | `news_collector` (`wal=True`), `extractor` (`foreign_keys=True`) |
| `schema.py` | `apply_schema()`, `run_migrations()`, `SCHEMA_VERSION`, `ARTICLES_SCHEMA`/`ARTICLE_COLUMNS` — the canonical `discovered_urls`/`discovery_progress`/`articles` DDL, one source of truth instead of a copy per pipeline stage. Schema DDL goes through `Database.create_schema`; the `discovered_urls.id` autoincrement token is filled from `db.dialect.autoincrement_pk` (portfolio-common v1.2.1 engine-agnostic seam). | `news_collector`, `extractor` (both call `apply_schema`/`run_migrations` before their first query) |
| `portfolio.py` | `load_universe()`, `list_universe()`, `resolve_symbol()`, `is_tracked()` — the tracked S&P 500 universe: a live scrape of Wikipedia's "List of S&P 500 companies," cached in-process for the life of the process. With `as_of=None` (the default), touches no database at all. | `news_collector`/`extractor` discovery, `pricing`'s `/universe` routes |
| `universe_history.py` | `backfill_from_changes()`, `record_snapshot()`, `query_as_of()`, `resolve_as_of()` — point-in-time membership. Reconstructs `valid_from`/`valid_to` intervals per ticker by combining today's live roster with the ["Historical components of the S&P 500"](https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500) Wikipedia article's addition/removal log (back to 1976). | `pricing`'s `as_of=` path only (`list_universe`/`resolve_symbol`, `/universe` routes); never called from the `as_of=None` default path |
| `queries.py` | The only SQL text for `data/universe.db`'s `universe_membership` (SCD-2) table — `write_intervals`, `fetch_open_symbols`, `close_intervals`, `query_membership_as_of`, etc. Pure DB-touching functions; `universe_history.py` holds the parsing/reconstruction logic and orchestrates these. | `universe_history.py` only |
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

Hermetic — the Wikipedia scrape (`portfolio.py`/`universe_history.py`) is
mocked at the HTTP boundary, and `db.py`/`schema.py`/`queries.py` run
against a temporary SQLite file, not the real `data/urls.db`/`data/universe.db`.
