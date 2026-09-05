# Coordination note: `portfolio-common` v1.2.1 — engine-agnostic seam

## Context

`portfolio-common` v1.2.0 / v1.2.1 make the database engine a single-repo
concern: a `Dialect` seam, `Database.connect_url`, schema-introspection
helpers (`table_columns` / `ensure_columns` / `create_schema` /
`relation_exists` / `relation_ddl` / `schema_version` / `set_schema_version`),
a neutral `Row` type, and `DatabaseError`. See `portfolio-nlp`'s
`docs/engine-agnostic-rollout.md` for the full cross-repo plan.

This repo owns `data/urls.db` — `articles` (incl. `body_text`) is the
read-only SOURCE store `portfolio-nlp` reads.

## What changed here (Phase 5)

- **`pyproject.toml`** — `[tool.uv.sources]` `portfolio-common` from the
  interim pre-v1.0.0 `rev = "aa59a7ec…"` straight to **`tag = "v1.2.1"`**.
- **No `import sqlite3` in non-test `src/`** — `sqlite3.Row` type hints in
  `src/extractor/db.py`, `src/data_mining/queries.py`,
  `src/news_collector/storage/queue.py` → `portfolio_common.db.Row`.
- **`src/data_mining/schema.py`:**
  - `db.executescript(_DISCOVERY_DDL)` / `_ARTICLES_DDL` → `db.create_schema(...)`.
  - `db.execute("PRAGMA user_version = {N}")` → `db.set_schema_version(SCHEMA_VERSION)`.
  - `_migrate_legacy_sector_column`: `PRAGMA table_info` → `db.table_columns`;
    the two `ALTER TABLE ADD COLUMN` → one `db.ensure_columns("articles",
    {...})`. The one-time `ALTER TABLE articles DROP COLUMN sector` stays
    (SQLite ≥3.35; a non-SQLite path expresses it differently — marked).
  - `discovered_urls`'s `id INTEGER PRIMARY KEY AUTOINCREMENT` →
    `{autoincrement_pk}` token, filled from `db.dialect.autoincrement_pk` in
    `apply_schema`.
- **`src/data_mining/queries.py`** — `db.executescript(_DDL)` →
  `db.create_schema(_DDL)`.
- **`src/extractor/db.py`** — `save_article`'s `INSERT OR REPLACE INTO
  articles (...)` → `db.dialect.upsert("articles", ARTICLE_COLUMNS,
  conflict=("id",), update=[<non-id cols>])` (portable `ON CONFLICT DO
  UPDATE`; equivalent for this trigger/cascade-free table). `enable_foreign_keys`
  keeps its one `PRAGMA foreign_keys = ON` — the single raw engine pragma
  left, for test fixtures that bypass `connect(foreign_keys=True)`; noted in
  its docstring.
- **`src/news_collector/storage/queue.py`** — `INSERT OR IGNORE INTO
  discovered_urls (...)` → `self._conn.dialect.insert_or_ignore(...)`;
  `discovery_progress`'s `INSERT … ON CONFLICT (…) DO UPDATE SET x=excluded.x`
  → `self._conn.dialect.upsert(…, update=["completed_at", "inserted_count"])`.

## Deliberately left as SQLite-flavoured SQL text

- The `articles` / `discovered_urls` DDL beyond the `AUTOINCREMENT` token
  (`INTEGER PRIMARY KEY` rowid alias, `FOREIGN KEY … REFERENCES`,
  `NOT NULL DEFAULT`) — standard-enough SQL.
- Raw SQL in `apps/news_crawler_api.py` (`SELECT * FROM articles WHERE …`,
  dynamic `discovered_urls` selects) is written in the app layer rather than
  routed through `extractor.db`. Moving it into named `extractor.db` /
  `queries.py` functions (the way `portfolio-nlp`'s API is structured) is a
  follow-up — it is a structural refactor, not engine coupling.
- The `articles` column names/types `portfolio-nlp` reads
  (`id, body_text, title, fetch_status, http_status_code, ticker, company,
  gics_sector, gics_sub_industry, pub_date, fetched_at, author, word_count,
  source_domain`) are unchanged.

## Verification

- `uv sync --frozen --group dev` (lock → `v1.2.1`)
- `uv run ruff check .` — clean (config discovery, as pre-commit runs it)
- `uv run ruff format --check .` — clean
- `uv run mypy --config-file=.code_quality/mypy.ini src apps cli` — clean (67 files)
- `uv run pytest -q` — **213 passed**
- `uv run pre-commit run --all-files` — clean
- `grep -rn "import sqlite3" src` — empty

## Companion PRs

- `portfolio-common#9` (v1.2.0), `#10` (v1.2.1). Merged, tagged.
- `portfolio-nlp#24`, `portfolio-knowledge-graph#9`,
  `portfolio-financial-analysis#32` — Phases 2–4.

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
