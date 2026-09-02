"""
src/common/universe_history.py

Point-in-time S&P 500 membership: reconstructs valid_from/valid_to
intervals per ticker by combining today's live roster
(`common.portfolio.load_universe()`) with the "Historical components of the
S&P 500" Wikipedia article's table of every index addition/removal since
1976 (https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500,
table id="changes"), then persists the result to a small dedicated SQLite
file (`data/universe.db` by default, override via $UNIVERSE_DB_PATH) so
`common.portfolio`'s `list_universe()`/`resolve_symbol()` can answer "who
was tracked as of date X", not just "who is tracked today" -- closing the
survivorship-bias gap the live-scrape-only design otherwise has.

Deliberately NOT wired into the default (as_of=None) path of
common.portfolio -- that path stays exactly as it was (in-process cached
live scrape, no DB touch at all), so existing callers are unaffected.
Backfilling history and recording forward snapshots are both explicit,
manually-run operations (see cli/pricing_cli.py's
`universe-backfill`/`universe-snapshot` subcommands), not something wired
into every request -- this repo has no scheduler and isn't meant to run as
production software.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from common.portfolio import WIKIPEDIA_HEADERS, list_universe

log = logging.getLogger(__name__)

CHANGES_URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"

# A data row has [date, added ticker, added security, removed ticker,
# removed security] at minimum; [reason] is a 6th, optional cell (Refs, a
# 7th, is dropped either way -- see _parse_changes_table's docstring).
_MIN_CHANGES_ROW_CELLS = 5
_REASON_CELL_INDEX = 5

# common.portfolio.COLUMNS -> universe_membership's lower_snake_case column
# names. Kept as an explicit map (not a mechanical .lower()) so it survives
# a COLUMNS rename without silently breaking, and so query results can be
# mapped straight back to the same dict shape non-as_of callers already get.
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

SOURCE_BACKFILL = "wikipedia_changes_backfill"
SOURCE_SNAPSHOT = "live_snapshot"

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


@dataclass
class ChangeEvent:
    """One row of the Historical-components 'changes' table. Ticker/security
    fields are "" (not None) when a row only adds or only removes -- e.g. an
    index reshuffle with no direct replacement."""

    effective_date: date
    added_ticker: str
    added_security: str
    removed_ticker: str
    removed_security: str
    reason: str


# ---------------------------------------------------------------------
# DB access
# ---------------------------------------------------------------------


def _db_path() -> str:
    """Read fresh on every call (not cached at import time) so tests can
    point $UNIVERSE_DB_PATH at a tmp_path file per-test."""
    return os.environ.get("UNIVERSE_DB_PATH") or "data/universe.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    return conn


def _row_to_public_dict(row: sqlite3.Row) -> dict:
    result = {_INVERSE_COLUMN_MAP[db_col]: row[db_col] for db_col in _COLUMN_MAP.values()}
    result["valid_from"] = row["valid_from"]
    result["valid_to"] = row["valid_to"]
    return result


def _write_intervals(conn: sqlite3.Connection, intervals: list[dict]) -> None:
    if not intervals:
        return
    # _DB_COLUMNS is a fixed internal constant (not user input) -- the
    # column list is interpolated, every value is still bound via `?`.
    conn.executemany(
        f"INSERT INTO universe_membership ({', '.join(_DB_COLUMNS)}) "  # noqa: S608
        f"VALUES ({', '.join('?' for _ in _DB_COLUMNS)})",
        [tuple(interval.get(col) for col in _DB_COLUMNS) for interval in intervals],
    )
    conn.commit()


# ---------------------------------------------------------------------
# Fetch + parse the "Historical components of the S&P 500" changes table
# ---------------------------------------------------------------------


def _fetch_changes_html(client: httpx.Client | None = None) -> str:
    """Blocking GET. Accepts an optional injected httpx.Client so tests can
    supply one wired to an httpx.MockTransport, matching the convention
    already used by news_collector.sp500/extractor.reference."""
    owns_client = client is None
    client = client or httpx.Client()
    try:
        response = client.get(
            CHANGES_URL, headers=WIKIPEDIA_HEADERS, follow_redirects=True, timeout=30.0
        )
        response.raise_for_status()
        return str(response.text)
    finally:
        if owns_client:
            client.close()


def _parse_changes_table(page_html: str) -> list[ChangeEvent]:
    """Pure parse: Historical-components page HTML -> change events, in the
    table's own most-recent-first order.

    Verified live structure (id="changes", class="wikitable sortable"):
    two header rows -- a top row with rowspan=2 "Effective Date"/"Reason"/
    "Refs" cells and colspan=2 "Added"/"Removed" cells, then a sub-header
    row of "Ticker"/"Security" -- followed by data rows of 6-7 <td> cells:
    [0] Effective Date (e.g. "August 18, 2026"), [1] Added Ticker,
    [2] Added Security, [3] Removed Ticker, [4] Removed Security,
    [5] Reason, [6] Refs (Refs is occasionally omitted entirely, not just
    blank). Either the Added or the Removed side (but not both, in
    practice) can be blank for a pure addition/removal with no direct
    replacement. Position-based, not header-text-based, for the same reason
    as common.portfolio._parse_constituents_table.
    """
    soup = BeautifulSoup(page_html, "html.parser")
    table = soup.find("table", {"id": "changes"})
    if table is None:
        raise ValueError("'changes' table not found on the Historical components page")

    events: list[ChangeEvent] = []
    for row in table.find_all("tr")[2:]:  # skip both header rows
        cells = row.find_all("td")
        if len(cells) < _MIN_CHANGES_ROW_CELLS:
            continue
        date_text = cells[0].get_text(strip=True)
        try:
            effective_date = datetime.strptime(date_text, "%B %d, %Y").date()
        except ValueError:
            log.warning("Skipping changes-table row with unparseable date %r", date_text)
            continue
        added_ticker = cells[1].get_text(strip=True).upper()
        added_security = cells[2].get_text(strip=True)
        removed_ticker = cells[3].get_text(strip=True).upper()
        removed_security = cells[4].get_text(strip=True)
        reason = (
            cells[_REASON_CELL_INDEX].get_text(strip=True)
            if len(cells) > _REASON_CELL_INDEX
            else ""
        )
        if not added_ticker and not removed_ticker:
            continue  # shouldn't happen in practice; skip defensively
        events.append(
            ChangeEvent(
                effective_date,
                added_ticker,
                added_security,
                removed_ticker,
                removed_security,
                reason,
            )
        )

    if not events:
        raise ValueError(
            "Parsed 0 change events from the Historical components page at "
            f"{CHANGES_URL} -- Wikipedia's page markup may have changed"
        )
    return events


# ---------------------------------------------------------------------
# Backward reconstruction
# ---------------------------------------------------------------------


def _reconstruct_intervals(today_rows: list[dict], events: list[ChangeEvent]) -> list[dict]:
    """Replay change events (most-recent-first, the table's own order)
    backward from today's live roster to build valid_from/valid_to
    intervals per ticker.

    A "removed" event always opens a fresh interval for that ticker
    (valid_to = the day before the effective date, valid_from unknown yet).
    An "added" event closes the most recent open interval for that ticker
    (valid_from = the effective date). Whatever is still open once every
    event has been replayed predates the table's coverage -- its valid_from
    is set to the earliest date the table covers (a true, if imprecise,
    lower bound), clamped to never land after that interval's own valid_to.

    Best-effort: an event that doesn't line up with the in-progress state
    (e.g. two removals for the same ticker with no intervening addition --
    a rename or a markup quirk) is logged and skipped rather than raising --
    this reconstructs a research dataset, not a system of record.
    """
    open_intervals: dict[str, dict] = {}
    for row in today_rows:
        symbol = row["Symbol"]
        open_intervals[symbol] = {
            **{db_col: row.get(col) for col, db_col in _COLUMN_MAP.items()},
            "valid_from": None,
            "valid_to": None,
            "source": SOURCE_SNAPSHOT,
        }

    completed: list[dict] = []
    earliest_date = events[-1].effective_date  # table is most-recent-first

    for event in events:
        d = event.effective_date

        if event.removed_ticker:
            r = event.removed_ticker
            if r in open_intervals:
                log.warning(
                    "changes-table row removes %r on %s but it already has an "
                    "unresolved interval open (likely a rename/markup anomaly) "
                    "-- skipping",
                    r,
                    d,
                )
            else:
                open_intervals[r] = {
                    "symbol": r,
                    "security": event.removed_security,
                    "gics_sector": None,
                    "gics_sub_industry": None,
                    "hq_location": None,
                    "date_added": None,
                    "cik": None,
                    "founded": None,
                    "valid_from": None,
                    "valid_to": (d - timedelta(days=1)).isoformat(),
                    "source": SOURCE_BACKFILL,
                }

        if event.added_ticker:
            a = event.added_ticker
            interval = open_intervals.get(a)
            if interval is not None and interval["valid_from"] is None:
                interval["valid_from"] = d.isoformat()
                completed.append(open_intervals.pop(a))
            else:
                log.warning(
                    "changes-table row adds %r on %s but no matching open "
                    "interval was found (likely a rename/markup anomaly) "
                    "-- skipping",
                    a,
                    d,
                )

    for interval in open_intervals.values():
        if interval["valid_from"] is None:
            fallback = earliest_date
            if interval["valid_to"] is not None:
                valid_to_date = date.fromisoformat(interval["valid_to"])
                fallback = min(fallback, valid_to_date)
            interval["valid_from"] = fallback.isoformat()
        completed.append(interval)

    return completed


# ---------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------


def backfill_from_changes(force: bool = False) -> int:
    """One-time reconstruction of point-in-time membership, replaying the
    Historical-components change log backward from today's live roster.
    Returns the number of intervals written; no-ops (returns 0) if
    universe_membership already has rows, unless force=True (which clears
    it first)."""
    conn = _connect()
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM universe_membership").fetchone()
        if count and not force:
            log.info(
                "universe_membership already has %d rows; skipping backfill (force=True to redo)",
                count,
            )
            return 0
        if force:
            conn.execute("DELETE FROM universe_membership")

        today_rows = list_universe()
        events = _parse_changes_table(_fetch_changes_html())
        intervals = _reconstruct_intervals(today_rows, events)
        _write_intervals(conn, intervals)
        return len(intervals)
    finally:
        conn.close()


def record_snapshot() -> dict:
    """Forward path: diff today's live roster against whichever rows are
    currently open (valid_to IS NULL), opening new intervals for newly
    tracked tickers and closing ones for newly untracked tickers. Meant to
    be run occasionally by hand (see cli/pricing_cli.py's
    `universe-snapshot`) -- this repo has no scheduler."""
    conn = _connect()
    try:
        today = date.today()
        today_rows = {row["Symbol"]: row for row in list_universe()}
        open_symbols = {
            r["symbol"]
            for r in conn.execute("SELECT symbol FROM universe_membership WHERE valid_to IS NULL")
        }

        added = sorted(set(today_rows) - open_symbols)
        removed = sorted(open_symbols - set(today_rows))

        new_intervals = [
            {
                **{db_col: today_rows[symbol].get(col) for col, db_col in _COLUMN_MAP.items()},
                "valid_from": today.isoformat(),
                "valid_to": None,
                "source": SOURCE_SNAPSHOT,
            }
            for symbol in added
        ]
        _write_intervals(conn, new_intervals)

        if removed:
            # Same valid_to convention as the backfill's "removed" events:
            # the last CONFIRMED day of membership is the day before we
            # detected the drop, so today's own row (whoever replaced them)
            # doesn't overlap with theirs.
            yesterday = (today - timedelta(days=1)).isoformat()
            conn.executemany(
                "UPDATE universe_membership SET valid_to = ? WHERE symbol = ? AND valid_to IS NULL",
                [(yesterday, symbol) for symbol in removed],
            )
            conn.commit()

        return {"added": added, "removed": removed}
    finally:
        conn.close()


def _assert_within_coverage(conn: sqlite3.Connection, as_of: date) -> None:
    row = conn.execute("SELECT MIN(valid_from) AS earliest FROM universe_membership").fetchone()
    earliest = row["earliest"] if row else None
    if earliest is None:
        raise ValueError(
            "universe_membership has no data yet -- run "
            "`cli/pricing_cli.py universe-backfill` first"
        )
    if as_of.isoformat() < earliest:
        raise ValueError(
            f"as_of={as_of.isoformat()} predates the earliest supported date "
            f"({earliest}) -- point-in-time data only goes back that far"
        )


def query_as_of(as_of: date, sector: str | None = None) -> list[dict]:
    """Point-in-time membership as of `as_of` (inclusive), optionally
    filtered by GICS Sector. Raises ValueError if no backfill has been run
    yet, or if `as_of` predates the earliest backfilled date."""
    conn = _connect()
    try:
        _assert_within_coverage(conn, as_of)
        as_of_s = as_of.isoformat()
        rows = conn.execute(
            "SELECT * FROM universe_membership WHERE valid_from <= ? "
            "AND (valid_to IS NULL OR valid_to >= ?) ORDER BY symbol",
            (as_of_s, as_of_s),
        ).fetchall()
        results = [_row_to_public_dict(r) for r in rows]
        if sector:
            results = [r for r in results if (r.get("GICS Sector") or "").lower() == sector.lower()]
        return results
    finally:
        conn.close()


def resolve_as_of(query: str, as_of: date) -> dict | None:
    """Resolve a ticker symbol OR company name to its canonical universe
    row as of `as_of`, or None if nothing matches. Same exact-then-partial
    matching semantics as common.portfolio.resolve_symbol. Propagates
    query_as_of's ValueError for an out-of-coverage as_of."""
    q = query.strip()
    if not q:
        return None

    rows = query_as_of(as_of)
    for row in rows:
        if row["Symbol"].upper() == q.upper():
            return row
    ql = q.lower()
    for row in rows:
        if ql in (row.get("Security") or "").lower():
            return row
    return None


__all__ = [
    "backfill_from_changes",
    "query_as_of",
    "record_snapshot",
    "resolve_as_of",
]
