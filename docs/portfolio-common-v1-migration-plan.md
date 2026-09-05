# Migration plan: portfolio-common v1.0.0 (DB engine + business_folders split)

## What changed upstream

`portfolio-common` v1.0.0 is a clean-break rewrite: the shared library used to
mix two concerns — a generic SQLite connection engine, and business/domain
code owned by specific downstream repos. This repo (`portfolio-data-mining`)
is confirmed (by grep, across the whole ecosystem) as the **only** consumer
of `portfolio_common.db`/`.schema`/`.portfolio`/`.universe_history`/`.errors`
— none of the other three sibling repos import these symbols. As of v1.0.0:

- `portfolio-common` is **DB-engine-only**: `portfolio_common.db.Database` (a
  single connection class subsuming what `portfolio_common.db.connect` did),
  plus `portfolio_common.db.in_clause` / `portfolio_common.db.Allowlist` —
  reusable injection-prevention primitives.
- Everything this repo used to import — the urls.db pipeline connection
  factory and DDL (`db.py`, `schema.py`), the S&P 500 universe helpers
  (`portfolio.py`, `universe_history.py`), and `errors.py`
  (`UpstreamDataError`) — has been extracted into
  `business_folders/data_mining/data_mining/` in the `portfolio-common` repo,
  staged for this repo to adopt directly. It is **not** part of the installed
  `portfolio-common` package anymore.
- One deliberate behavior fix landed during extraction:
  `universe_history`'s connection (previously a 4th ad hoc `sqlite3.connect`
  recipe with no `busy_timeout`, no WAL policy, no FK enforcement) now goes
  through `portfolio_common.db.Database.connect()` like everything else —
  it gains a 30s busy_timeout it didn't have before. Verify this doesn't
  surface a previously-masked lock-contention issue when you adopt it.
- There is **no backward-compatible shim** — pinning our `portfolio-common`
  git tag to `v1.0.0` breaks every `from portfolio_common import portfolio`
  / `from portfolio_common.universe_history import X` /
  `from portfolio_common.errors import UpstreamDataError` /
  `from portfolio_common.db import connect` import in this repo until this
  plan is executed.

See `portfolio-common`'s `business_folders/data_mining/README.md` for the
exact file inventory and `CHANGELOG.md` for the full rationale.

## What to pull in

1. Copy `portfolio-common/business_folders/data_mining/data_mining/` into
   this repo — suggested location `src/data_mining/` alongside the existing
   `src/news_collector/` and `src/extractor/` packages (confirm the exact
   sub-path with whatever this repo's `src/` layout looks like when you
   execute this).
2. Copy its `tests/` into this repo's own test suite location.
3. Delete `business_folders/data_mining/` from `portfolio-common` once the
   copy is verified working here (a follow-up PR against `portfolio-common`,
   not part of this repo's change).

## Import updates

Grep this repo for `portfolio_common.portfolio`, `portfolio_common
.universe_history`, `portfolio_common.errors`, and any direct
`portfolio_common.db` usage, and replace with the new local package. Known
current call sites (confirmed by search when this plan was written — re-grep
before executing, this list may be stale):

- `cli/pricing_cli.py:40-41` — `from portfolio_common.portfolio import
  list_universe, resolve_symbol`, `from portfolio_common.universe_history
  import backfill_from_changes, record_snapshot`
- `apps/pricing_api.py:30-31` — `from portfolio_common.errors import
  UpstreamDataError`, `from portfolio_common.portfolio import list_universe,
  resolve_symbol`
- `src/extractor/db.py`, `src/news_collector/storage/queue.py` — these use
  `portfolio_common.db`'s connection factory today (via whatever this repo's
  own wrapper does); confirm whether they call `portfolio_common.db.connect`
  directly or through a local wrapper, and repoint at the vendored
  `data_mining.db` (or the still-available `portfolio_common.db.Database`
  directly, if you'd rather call the engine straight rather than through the
  vendored thin wrapper — both work, `data_mining.db.connect` is just a
  domain-flavored convenience over it)

## Adopt the injection-safety helpers for our own dynamic SQL

This repo has two files with hand-built `IN (...)` clauses and an allowlisted
`ORDER BY` today — replace with `portfolio_common.db.in_clause` /
`portfolio_common.db.Allowlist` (keep `portfolio-common` as a runtime
dependency for these two primitives even after vendoring `data_mining`):

- `src/news_collector/storage/queue.py:141` —
  `f"SELECT id, url, ticker FROM discovered_urls WHERE (url, ticker) IN ({placeholders})"`
  → `in_clause(...)` (this one binds pairs — check `in_clause`'s doc, you may
  need to flatten and adjust the SQL to a different pattern, or extend the
  call site's own tuple-flattening logic around it)
- `src/news_collector/storage/queue.py:234` —
  `f"SELECT ticker, domain FROM discovery_progress WHERE domain IN ({placeholders}) AND ..."`
  → `in_clause(domains)`
- `src/news_collector/storage/queue.py:320-366` — `order_by`/`direction`
  already validated against `_QUERY_ORDER_COLUMNS` and a fixed
  `"DESC"/"ASC"` check — replace `_QUERY_ORDER_COLUMNS` with
  `portfolio_common.db.Allowlist("discovered_at", ...)` for a shared,
  tested primitive instead of a local set
- `src/extractor/db.py:69-70` —
  `f"SELECT ... FROM discovered_urls WHERE status IN ({placeholders}) ORDER BY id"`
  → `in_clause(statuses)`
- `src/extractor/db.py:97` —
  `f"INSERT OR REPLACE INTO articles ({columns}) VALUES ({placeholders})"` —
  confirm `columns` comes from a fixed constant (per the existing code
  comment) and document/guard it with `Allowlist` if it's ever
  caller-influenced

## `queries.py` convention

Apply the same convention the vendored `data_mining` package uses: every
query function lives in one ordered, documented place per domain, separate
from orchestration/pure business logic. Consider applying it to
`news_collector/storage/queue.py` and `extractor/db.py` too as you touch them
for the `in_clause`/`Allowlist` migration above.

## Version pin

Once this repo's own tests pass against the vendored `data_mining` package,
bump `pyproject.toml`'s `[tool.uv.sources]` pin:

```toml
portfolio-common = { git = "https://github.com/gamug/portfolio-common", tag = "v1.0.0" }
```
(currently pinned to `v0.1.2` here — this is a larger version jump than the
other sibling repos, worth a careful full-suite run.)

## Verification

- `uv sync`, `uv run pytest`, `uv run ruff check .`, `uv run mypy` (or
  whatever this repo's equivalents are — confirm from `pyproject.toml`) all
  pass with the vendored package and updated imports.
- Specifically re-run whatever exercises `universe_history.backfill_from_changes`
  / `record_snapshot` against a real or scratch `universe.db` to confirm the
  busy_timeout behavior change (see above) doesn't change observed behavior
  under this repo's actual concurrency pattern.
- Smoke-test `cli/pricing_cli.py` and `apps/pricing_api.py` end to end.

---
🤖 Generated with [Claude Code](https://claude.com/claude-code) as part of the
portfolio-common v1.0.0 DB-engine/business_folders split.
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
