"""DB-touching functions for `data/universe.db` (override via
$UNIVERSE_DB_PATH), the `universe_membership` SCD-2 table that backs
`data_mining.universe_history`'s point-in-time queries.

Every function here takes a `portfolio_common.db.Database` (or opens one)
and returns plain data; this is the only place SQL text for this table
lives.

Query functions:
- `_connect()` -- open (creating the schema if absent) the universe.db `Database`.
- `count_membership_rows(db)` -- row count of `universe_membership`.
- `clear_membership(db)` -- delete every row (used by a forced backfill).
- `write_intervals(db, intervals)` -- bulk-insert reconstructed/snapshot intervals.
- `fetch_open_symbols(db)` -- symbols with an open (`valid_to IS NULL`) interval.
- `close_intervals(db, symbols, valid_to)` -- close the open interval for each symbol.
- `earliest_valid_from(db)` -- `MIN(valid_from)` across all rows, or None if empty.
- `query_membership_as_of(db, as_of_s)` -- membership rows valid on a given
  date, mapped to the public dict shape callers already get from the
  as_of=None path.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from portfolio_common.db import Database

# portfolio_common (data_mining).portfolio.COLUMNS -> universe_membership's
# lower_snake_case column names. Kept as an explicit map (not a mechanical
# .lower()) so it survives a COLUMNS rename without silently breaking, and
# so query results can be mapped straight back to the same dict shape
# non-as_of callers already get.
_COLUMN_MAP = {
    "Symbol": "symbol",
    "Security": "security",
    "GICS Sector": "gics_sector",
    "GICS Sub-Industry": "gics_sub_industry",
    "Headquarters Location": "hq_location",
    "Date added": "date_added",
    "CIK": "cik",
    "Founded": "founded",
}
_DB_COLUMNS = [*_COLUMN_MAP.values(), "valid_from", "valid_to", "source"]
_INVERSE_COLUMN_MAP = {db_col: col for col, db_col in _COLUMN_MAP.items()}

_DDL = """
CREATE TABLE IF NOT EXISTS universe_membership (
    symbol            TEXT NOT NULL,
    security          TEXT NOT NULL,
    gics_sector       TEXT,
    gics_sub_industry TEXT,
    hq_location       TEXT,
    date_added        TEXT,
    cik               TEXT,
    founded           TEXT,
    valid_from        TEXT NOT NULL,
    valid_to          TEXT,
    source            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_membership_symbol ON universe_membership (symbol);
CREATE INDEX IF NOT EXISTS idx_membership_valid   ON universe_membership (valid_from, valid_to);
"""


def _db_path() -> str:
    """Read fresh on every call (not cached at import time) so tests can
    point $UNIVERSE_DB_PATH at a tmp_path file per-test."""
    return os.environ.get("UNIVERSE_DB_PATH") or "data/universe.db"


def _connect() -> Database:
    """Open (creating the schema if absent) the universe.db `Database`.

    Delegates to `portfolio_common.db.Database.connect()` -- row_factory,
    busy_timeout, parent-directory creation, all handled by the shared
    engine. This is a deliberate behavior fix over the module's previous
    ad hoc `sqlite3.connect()` recipe, which set none of that (no
    busy_timeout in particular -- a concurrent reader/writer could hit an
    immediate "database is locked" instead of the engine's default 30s
    retry).
    """
    path = Path(_db_path())
    db = Database.connect(path)
    db.executescript(_DDL)
    return db


def count_membership_rows(db: Database) -> int:
    (count,) = db.execute("SELECT COUNT(*) FROM universe_membership").fetchone()
    return int(count)


def clear_membership(db: Database) -> None:
    db.execute("DELETE FROM universe_membership")


def write_intervals(db: Database, intervals: list[dict]) -> None:
    if not intervals:
        return
    # _DB_COLUMNS is a fixed internal constant (not user input, never
    # derived from a caller-supplied name) -- the column list is
    # interpolated, every value is still bound via `?`. Matches the
    # "fixed internal constant" exception documented in
    # portfolio_common.db.safety's module docstring; Allowlist/in_clause are
    # for caller-supplied identifiers and values respectively, neither of
    # which applies here.
    db.executemany(
        f"INSERT INTO universe_membership ({', '.join(_DB_COLUMNS)}) "  # noqa: S608
        f"VALUES ({', '.join('?' for _ in _DB_COLUMNS)})",
        [tuple(interval.get(col) for col in _DB_COLUMNS) for interval in intervals],
    )
    db.commit()


def fetch_open_symbols(db: Database) -> set[str]:
    return {
        r["symbol"]
        for r in db.execute("SELECT symbol FROM universe_membership WHERE valid_to IS NULL")
    }


def close_intervals(db: Database, symbols: list[str], valid_to: str) -> None:
    if not symbols:
        return
    db.executemany(
        "UPDATE universe_membership SET valid_to = ? WHERE symbol = ? AND valid_to IS NULL",
        [(valid_to, symbol) for symbol in symbols],
    )
    db.commit()


def earliest_valid_from(db: Database) -> str | None:
    row = db.execute("SELECT MIN(valid_from) AS earliest FROM universe_membership").fetchone()
    return row["earliest"] if row else None


def _row_to_public_dict(row: sqlite3.Row) -> dict:
    result = {_INVERSE_COLUMN_MAP[db_col]: row[db_col] for db_col in _COLUMN_MAP.values()}
    result["valid_from"] = row["valid_from"]
    result["valid_to"] = row["valid_to"]
    return result


def query_membership_as_of(db: Database, as_of_s: str) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM universe_membership WHERE valid_from <= ? "
        "AND (valid_to IS NULL OR valid_to >= ?) ORDER BY symbol",
        (as_of_s, as_of_s),
    ).fetchall()
    return [_row_to_public_dict(r) for r in rows]
