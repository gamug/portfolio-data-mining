#!/usr/bin/env python
"""One-off migration: copy the local pipeline SQLite DB (data/urls.db) into
a remote Turso/libSQL database.

Usage:
    uv run scripts/migrate_to_turso.py                  # migrate everything, resuming
    uv run scripts/migrate_to_turso.py --status          # just report progress, no writes
    uv run scripts/migrate_to_turso.py --tables articles,article_sentiment
    uv run scripts/migrate_to_turso.py --batch-size 200  # smaller batches (big rows / slow link)

By default, reads the source from $DATABASE_URL-independent `data/urls.db`
(the local file always -- this script's whole job is to read that file, so
it can't source from $DATABASE_URL the way the pipeline's own connect()
functions do once that's been repointed at Turso) and writes to
$DATABASE_URL/$TURSO_AUTH_TOKEN (i.e. wherever the app itself is now
pointed -- see .env / src/common/db_backend.py). Override either with
--source / --target-url / --auth-token.

## Why this is chunked and resumable, not a single bulk load

data/urls.db is ~4.5GB / ~18.6M rows across 8 real tables (article_entities
alone is ~17.6M rows). Turso's free plan caps storage at 5GB and writes at
10M rows/month (https://turso.tech/pricing, checked 2026-08-26) -- a single
run cannot finish this migration on that plan. So every table is migrated
in --batch-size chunks (default 2000 rows/round-trip -- each batch is one
multi-row INSERT statement, not executemany(); see _insert_batch for why),
ordered by primary key, and each table's starting point is `SELECT MAX(pk)
FROM table` against the *target* -- not a separate checkpoint file -- so:
  - interrupting the script (Ctrl-C, a network blip, hitting a monthly
    quota) never loses or double-writes a row: whatever's actually
    committed on Turso is the resume point, full stop.
  - re-running the script later (e.g. after next month's quota reset, or
    after upgrading the plan) picks up exactly where it left off with no
    flags needed.

## Table order

Migrated in FK-dependency order (parents before children) so target-side FK
enforcement (PRAGMA foreign_keys=ON, set by the pipeline's own connect()
functions) never rejects a row: discovered_urls, discovery_progress,
articles, article_sentiment, article_entities, article_summary,
article_category, sector_summary. `sqlite_sequence` is SQLite's own
AUTOINCREMENT bookkeeping table -- never copied directly; every AUTOINCREMENT
table here is migrated with its *explicit* source id, and SQLite maintains
sqlite_sequence off the max id it's actually seen on insert (even an
explicit one), so the target's bookkeeping ends up correct without needing
its own row-copy step.

## What "everything" costs

For all 8 tables, at ~18.6M total rows: on a free-tier 10M-rows/month
write cap, this needs at least 2 monthly runs even in the best case. Rows
this script *reads* from the batches it fetches locally don't count against
Turso's monthly *read* quota (500M rows/month) at all -- only what it
writes to the target does.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
from tqdm import tqdm  # noqa: E402

from common.db_backend import open_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Table plan: FK-dependency order, each with its resume/order-by column(s).
# `pk` is None for discovery_progress -- it has a composite key and no
# single monotonic column to resume against, but at ~3.5K rows it's cheap
# enough to just re-send in full every run via INSERT OR IGNORE (already-
# present rows are no-ops on the target, not re-written).
# ---------------------------------------------------------------------------
TABLES: list[tuple[str, str | None]] = [
    ("discovered_urls", "id"),
    ("discovery_progress", None),
    ("articles", "id"),
    ("article_sentiment", "article_id"),
    ("article_entities", "id"),
    ("article_summary", "article_id"),
    ("article_category", "article_id"),
    ("sector_summary", "id"),
]
_TABLE_NAMES = [name for name, _ in TABLES]


def _schema_statements(source: sqlite3.Connection) -> list[str]:
    """Pull every CREATE TABLE / CREATE INDEX statement for the tables in
    TABLES out of the source DB's own sqlite_master, made idempotent (`IF
    NOT EXISTS` isn't preserved in sqlite_master's stored SQL text even
    when the original CREATE used it) so replaying this against a target
    that already has some or all of the schema is always safe."""
    # S608: the `{}` placeholders are just a run of literal "?" -- table
    # names are bound as query params below, never interpolated into the
    # SQL text.
    rows = source.execute(
        "SELECT type, sql FROM sqlite_master "  # noqa: S608
        "WHERE type IN ('table', 'index') AND sql IS NOT NULL AND name != 'sqlite_sequence' "
        "AND (name IN ({}) OR tbl_name IN ({})) "
        "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END".format(
            ",".join("?" * len(_TABLE_NAMES)), ",".join("?" * len(_TABLE_NAMES))
        ),
        [*_TABLE_NAMES, *_TABLE_NAMES],
    ).fetchall()
    statements = []
    for kind, sql in rows:
        stmt = sql
        if kind == "table" and not stmt.upper().startswith("CREATE TABLE IF NOT EXISTS"):
            stmt = stmt.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)
        elif kind == "index" and not stmt.upper().startswith("CREATE INDEX IF NOT EXISTS"):
            stmt = stmt.replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS", 1)
        statements.append(stmt)
    return statements


def ensure_schema(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    print("Ensuring schema on target (idempotent -- safe if already present)...")
    for sql in _schema_statements(source):
        target.execute(sql)
    target.commit()


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _remote_max_pk(target: sqlite3.Connection, table: str, pk: str) -> int:
    row = target.execute(f"SELECT MAX({pk}) FROM {table}").fetchone()  # noqa: S608 -- table/pk from TABLES, not user input
    value = row[0] if row else None
    return int(value) if value is not None else 0


# SQLite/libSQL's default max bound-parameter count per statement --
# empirically confirmed 2026-08-26 against this same Turso database: a
# 20,000-row x 2-column batch (40,000 params) failed with "too many SQL
# variables"; a 10,000-row x 2-column batch (20,000 params) succeeded. Kept
# well under the real ceiling (SQLite's own default, 32766) as a margin.
_SAFE_PARAM_LIMIT = 30_000


def _effective_batch_size(batch_size: int, num_columns: int) -> int:
    """Cap a row-batch size so `batch_size` rows x `num_columns` columns
    never exceeds _SAFE_PARAM_LIMIT bound parameters in one statement --
    a wide table (e.g. articles' 16 columns) needs a smaller row-batch than
    a narrow one (e.g. article_sentiment's 8) to stay under the same limit.
    """
    return max(1, min(batch_size, _SAFE_PARAM_LIMIT // num_columns))


def _insert_batch(
    target: sqlite3.Connection, table: str, columns: list[str], rows: list[tuple]
) -> None:
    """INSERT a batch as one multi-row VALUES statement rather than
    target.executemany().

    libsql's Python executemany() was empirically measured (2026-08-26,
    against this same database) at ~4.4 rows/sec -- ~225ms of real network
    round-trip per row, i.e. it does not batch at the wire-protocol level
    despite the name (sqlite3's executemany, which it's meant to mirror,
    has no such cost since there's no network involved). A single
    statement carrying N rows' worth of VALUES tuples is one round trip
    regardless of N (up to _SAFE_PARAM_LIMIT): the same measurement showed
    5,000 rows in 1.3s (~3,800 rows/sec, ~850x faster) this way.
    """
    col_list = ", ".join(columns)
    row_placeholder = "(" + ", ".join("?" for _ in columns) + ")"
    values_sql = ", ".join(row_placeholder for _ in rows)
    params = [v for row in rows for v in row]
    target.execute(
        f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES {values_sql}",  # noqa: S608
        params,
    )
    target.commit()


def migrate_table(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    table: str,
    pk: str | None,
    batch_size: int,
) -> None:
    columns = _table_columns(source, table)
    col_list = ", ".join(columns)
    effective_batch_size = _effective_batch_size(batch_size, len(columns))

    total_local = source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    if total_local == 0:
        print(f"[{table}] source is empty, skipping")
        return

    if pk is None:
        # No monotonic resume column (discovery_progress) -- small table,
        # just re-send everything; INSERT OR IGNORE makes already-present
        # rows a no-op on the target.
        rows = source.execute(f"SELECT {col_list} FROM {table}").fetchall()  # noqa: S608
        with tqdm(total=len(rows), desc=table, unit="row") as pbar:
            for i in range(0, len(rows), effective_batch_size):
                batch = [tuple(r) for r in rows[i : i + effective_batch_size]]
                _insert_batch(target, table, columns, batch)
                pbar.update(len(batch))
        return

    start_after = _remote_max_pk(target, table, pk)
    remaining = source.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {pk} > ?",  # noqa: S608 -- table/pk from TABLES, not user input
        (start_after,),
    ).fetchone()[0]
    if remaining == 0:
        print(f"[{table}] already fully migrated ({total_local}/{total_local} rows)")
        return

    print(
        f"[{table}] resuming after {pk}={start_after} "
        f"({total_local - remaining}/{total_local} already on target)"
    )
    with tqdm(total=remaining, desc=table, unit="row") as pbar:
        cursor = source.execute(
            f"SELECT {col_list} FROM {table} WHERE {pk} > ? ORDER BY {pk}",  # noqa: S608
            (start_after,),
        )
        while True:
            batch = cursor.fetchmany(effective_batch_size)
            if not batch:
                break
            _insert_batch(target, table, columns, [tuple(r) for r in batch])
            pbar.update(len(batch))


def print_status(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    print(f"{'table':<20} {'local':>10} {'remote':>10} {'remaining':>10}")
    for table, _pk in TABLES:
        local = source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        try:
            remote = target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        except Exception:
            remote = 0
        print(f"{table:<20} {local:>10} {remote:>10} {max(local - remote, 0):>10}")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--source",
        default=str(_REPO_ROOT / "data" / "urls.db"),
        help="Local source SQLite file (default: data/urls.db)",
    )
    parser.add_argument(
        "--target-url",
        default=os.environ.get("DATABASE_URL"),
        help="Turso/libSQL target URL (default: $DATABASE_URL)",
    )
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("TURSO_AUTH_TOKEN"),
        help="Turso auth token (default: $TURSO_AUTH_TOKEN)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2000,
        help="Rows per INSERT round-trip to the target (default: 2000; each batch is one "
        "multi-row INSERT statement, not executemany() -- see _insert_batch). "
        "Auto-capped per table so rows x columns never exceeds a safe SQL-variable count "
        "(see _SAFE_PARAM_LIMIT) -- lower this yourself only if you hit a payload-size "
        "error on a wide/large-text table (e.g. articles.body_text).",
    )
    parser.add_argument(
        "--tables",
        default=None,
        help="Comma-separated subset of tables to migrate (default: all, in FK order). "
        f"Choices: {', '.join(_TABLE_NAMES)}",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print local-vs-target row counts per table and exit -- no writes.",
    )
    args = parser.parse_args()

    if not args.target_url:
        parser.error("--target-url (or $DATABASE_URL) is required")
    if not args.target_url.startswith(("libsql://", "http://", "https://")):
        parser.error(
            f"--target-url ({args.target_url!r}) doesn't look like a Turso/libSQL URL "
            "-- refusing to run against what looks like a local path."
        )
    if not args.auth_token:
        parser.error("--auth-token (or $TURSO_AUTH_TOKEN) is required")
    if not Path(args.source).exists():
        parser.error(f"--source file not found: {args.source}")

    tables = TABLES
    if args.tables:
        wanted = set(args.tables.split(","))
        unknown = wanted - set(_TABLE_NAMES)
        if unknown:
            parser.error(f"unknown table(s): {', '.join(sorted(unknown))}")
        tables = [(name, pk) for name, pk in TABLES if name in wanted]

    source = sqlite3.connect(args.source)
    source.row_factory = sqlite3.Row
    target = open_connection(args.target_url, auth_token=args.auth_token)

    if args.status:
        print_status(source, target)
        return

    ensure_schema(source, target)

    start = time.monotonic()
    for table, pk in tables:
        try:
            migrate_table(source, target, table, pk, args.batch_size)
        except Exception as exc:  # deliberately broad: see message below
            print(
                f"\n[{table}] migration stopped by an error: {exc}\n"
                "If this looks like a Turso plan/quota limit (storage or monthly "
                "rows-written cap), wait for the next reset or upgrade your plan, then "
                "re-run this script -- it resumes automatically from the last "
                "successfully committed row (see the module docstring)."
            )
            sys.exit(1)
    elapsed = time.monotonic() - start
    print(f"\nDone in {elapsed:.1f}s.")
    print_status(source, target)


if __name__ == "__main__":
    main()
