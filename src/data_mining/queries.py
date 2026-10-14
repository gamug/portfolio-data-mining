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
- `fetch_gics_targets(db, cutoff)` -- leaver stints needing a GICS label.
- `update_gics(db, updates)` -- write a stint's GICS label + revision citation.
- `migrate_gics_columns(db)` -- add the two GICS citation columns (builder only).
"""

from __future__ import annotations

import os
from pathlib import Path

from portfolio_common.db import Database, Row

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
    source            TEXT NOT NULL,
    gics_revision_id        TEXT,
    gics_revision_timestamp TEXT
);
CREATE INDEX IF NOT EXISTS idx_membership_symbol ON universe_membership (symbol);
CREATE INDEX IF NOT EXISTS idx_membership_valid   ON universe_membership (valid_from, valid_to);
"""

# Additive, idempotent migration for databases created before the GICS
# citation columns existed (same pattern as data_mining.schema's
# _migrate_legacy_sector_column). Applied on every `_connect` so every code
# path -- the backfill writer included -- sees the columns. They are
# deliberately NOT in `_DB_COLUMNS`: the leaver-GICS fill writes them with a
# standalone, stint-keyed UPDATE (`update_gics`), so `write_intervals` never
# rewrites them and every row the fill does not touch keeps them NULL.
_GICS_CITATION_COLUMNS = {
    "gics_revision_id": "TEXT",
    "gics_revision_timestamp": "TEXT",
}


def _db_path() -> str:
    """Read fresh on every call (not cached at import time) so tests can
    point $UNIVERSE_DB_PATH at a tmp_path file per-test."""
    return os.environ.get("UNIVERSE_DB_PATH") or "data/universe.db"


def _connect(path: str | Path | None = None) -> Database:
    """Open (creating the schema if absent) the universe.db `Database`.

    Delegates to `portfolio_common.db.Database.connect()` -- row_factory,
    busy_timeout, parent-directory creation, all handled by the shared
    engine. This is a deliberate behavior fix over the module's previous
    ad hoc `sqlite3.connect()` recipe (pre-portfolio_common), which set none of that (no
    busy_timeout in particular -- a concurrent reader/writer could hit an
    immediate "database is locked" instead of the engine's default 30s
    retry).

    `path` overrides `$UNIVERSE_DB_PATH` (used by the leaver-GICS builder,
    which operates on an explicit copy so it never touches the default
    file). Deliberately does NOT migrate the schema: `create_schema`'s
    `CREATE TABLE IF NOT EXISTS` is a no-op on an existing table, and adding
    the GICS citation columns here would make a read path (the gateway's
    `/universe` route) rewrite whatever file `$UNIVERSE_DB_PATH` names --
    those files are sha1-tracked and must never change. The builder migrates
    its own output copy instead (`migrate_gics_columns`).
    """
    target = Path(path) if path is not None else Path(_db_path())
    db = Database.connect(target)
    db.create_schema(_DDL)
    return db


def migrate_gics_columns(db: Database) -> None:
    """Add the two GICS citation columns to `universe_membership` if absent.

    Called only by the leaver-GICS builder, on its own output copy -- never
    from `_connect`, so no read path ever ALTERs the file it opens (the
    gateway's `/universe` route opens `$UNIVERSE_DB_PATH`, which is
    sha1-tracked and must not change). Idempotent."""
    db.ensure_columns("universe_membership", _GICS_CITATION_COLUMNS)


def count_membership_rows(db: Database) -> int:
    (count,) = db.execute("SELECT COUNT(*) FROM universe_membership").fetchone()
    return int(count)


def count_backfill_rows(db: Database) -> int:
    (count,) = db.execute(
        "SELECT COUNT(*) FROM universe_membership WHERE source = 'wikipedia_changes_backfill'"
    ).fetchone()
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


def _row_to_public_dict(row: Row) -> dict:
    result = {_INVERSE_COLUMN_MAP[db_col]: row[db_col] for db_col in _COLUMN_MAP.values()}
    result["valid_from"] = row["valid_from"]
    result["valid_to"] = row["valid_to"]
    # Tolerate a database created before the GICS citation columns existed
    # (the read path never migrates): report them as None rather than
    # raising on the missing columns.
    keys = row.keys()
    result["gics_revision_id"] = row["gics_revision_id"] if "gics_revision_id" in keys else None
    result["gics_revision_timestamp"] = (
        row["gics_revision_timestamp"] if "gics_revision_timestamp" in keys else None
    )
    return result


def fetch_gics_targets(db: Database, cutoff: str = "2022-01-01") -> list[dict]:
    """The leaver stints the GICS backfill targets: every `valid_to` on or
    after `cutoff` whose CIK has no open (`valid_to IS NULL`) stint today --
    i.e. companies that are no longer index members, which the live snapshot
    cannot classify. Rows already carrying a citation are returned too, so
    the caller can report "already filled" and skip them (idempotent
    re-runs)."""
    rows = db.execute(
        "SELECT m.symbol, m.cik, m.security, m.valid_from, m.valid_to, "
        "       m.gics_sector, m.gics_sub_industry, m.gics_revision_id "
        "FROM universe_membership AS m "
        "WHERE m.valid_to >= ? AND m.cik IS NOT NULL AND NOT EXISTS ("
        "    SELECT 1 FROM universe_membership AS o "
        "    WHERE o.cik = m.cik AND o.valid_to IS NULL"
        ") ORDER BY m.valid_to, m.symbol",
        (cutoff,),
    ).fetchall()
    return [dict(row) for row in rows]


def update_gics(db: Database, updates: list[dict]) -> None:
    """Write the GICS label + citation for each target stint, keyed on the
    natural key `(symbol, cik, valid_from, valid_to)` -- unique in this
    table (verified: no duplicate key across all rows). Only the four GICS
    columns are touched; every other column, including the `live_snapshot`
    rows, is left untouched."""
    if not updates:
        return
    db.executemany(
        "UPDATE universe_membership SET "
        "    gics_sector = ?, gics_sub_industry = ?, "
        "    gics_revision_id = ?, gics_revision_timestamp = ? "
        "WHERE symbol = ? AND cik = ? AND valid_from = ? AND valid_to = ?",
        [
            (
                u["gics_sector"],
                u["gics_sub_industry"],
                u["gics_revision_id"],
                u["gics_revision_timestamp"],
                u["symbol"],
                u["cik"],
                u["valid_from"],
                u["valid_to"],
            )
            for u in updates
        ],
    )
    db.commit()


def query_membership_as_of(db: Database, as_of_s: str) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM universe_membership WHERE valid_from <= ? "
        "AND (valid_to IS NULL OR valid_to >= ?) ORDER BY symbol",
        (as_of_s, as_of_s),
    ).fetchall()
    return [_row_to_public_dict(r) for r in rows]
