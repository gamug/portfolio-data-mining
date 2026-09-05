"""
data_mining/universe_history.py

Point-in-time S&P 500 membership: reconstructs valid_from/valid_to
intervals per ticker by combining today's live roster
(`data_mining.portfolio.load_universe()`) with the "Historical components of
the S&P 500" Wikipedia article's table of every index addition/removal since
1976 (https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500,
table id="changes"), then persists the result to a small dedicated SQLite
file (`data/universe.db` by default, override via $UNIVERSE_DB_PATH) so
`data_mining.portfolio`'s `list_universe()`/`resolve_symbol()` can answer "who
was tracked as of date X", not just "who is tracked today" -- closing the
survivorship-bias gap the live-scrape-only design otherwise has.

Deliberately NOT wired into the default (as_of=None) path of
data_mining.portfolio -- that path stays exactly as it was (in-process cached
live scrape, no DB touch at all), so existing callers are unaffected.
Backfilling history and recording forward snapshots are both explicit,
manually-run operations (see cli/pricing_cli.py's
`universe-backfill`/`universe-snapshot` subcommands), not something wired
into every request -- this repo has no scheduler and isn't meant to run as
production software.

The DB-touching functions (schema, inserts, updates, the as_of read query)
live in the sibling `data_mining.queries` module -- everything below this
point is pure business logic (parsing, reconstruction) plus the public
operations that orchestrate `queries` calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx
from bs4 import BeautifulSoup
from portfolio_common.db import Database

from data_mining import queries
from data_mining.portfolio import WIKIPEDIA_HEADERS, list_universe

log = logging.getLogger(__name__)

CHANGES_URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"

# A data row has [date, added ticker, added security, removed ticker,
# removed security] at minimum; [reason] is a 6th, optional cell (Refs, a
# 7th, is dropped either way -- see _parse_changes_table's docstring).
_MIN_CHANGES_ROW_CELLS = 5
_REASON_CELL_INDEX = 5

# data_mining.portfolio.COLUMNS -> universe_membership's lower_snake_case
# column names. Owned by `queries` (it's paired 1:1 with that module's
# `_DB_COLUMNS`) and imported here rather than duplicated, so a rename can't
# drift between the write side (this module) and the read side (queries).
_COLUMN_MAP = queries._COLUMN_MAP

SOURCE_BACKFILL = "wikipedia_changes_backfill"
SOURCE_SNAPSHOT = "live_snapshot"


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
    as data_mining.portfolio._parse_constituents_table.
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
    db = queries._connect()
    try:
        count = queries.count_membership_rows(db)
        if count and not force:
            log.info(
                "universe_membership already has %d rows; skipping backfill (force=True to redo)",
                count,
            )
            return 0
        if force:
            queries.clear_membership(db)

        today_rows = list_universe()
        events = _parse_changes_table(_fetch_changes_html())
        intervals = _reconstruct_intervals(today_rows, events)
        queries.write_intervals(db, intervals)
        return len(intervals)
    finally:
        db.close()


def record_snapshot() -> dict:
    """Forward path: diff today's live roster against whichever rows are
    currently open (valid_to IS NULL), opening new intervals for newly
    tracked tickers and closing ones for newly untracked tickers. Meant to
    be run occasionally by hand (see cli/pricing_cli.py's
    `universe-snapshot`) -- this repo has no scheduler."""
    db = queries._connect()
    try:
        today = date.today()
        today_rows = {row["Symbol"]: row for row in list_universe()}
        open_symbols = queries.fetch_open_symbols(db)

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
        queries.write_intervals(db, new_intervals)

        if removed:
            # Same valid_to convention as the backfill's "removed" events:
            # the last CONFIRMED day of membership is the day before we
            # detected the drop, so today's own row (whoever replaced them)
            # doesn't overlap with theirs.
            yesterday = (today - timedelta(days=1)).isoformat()
            queries.close_intervals(db, removed, yesterday)

        return {"added": added, "removed": removed}
    finally:
        db.close()


def _assert_within_coverage(db: Database, as_of: date) -> None:
    earliest = queries.earliest_valid_from(db)
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
    db = queries._connect()
    try:
        _assert_within_coverage(db, as_of)
        as_of_s = as_of.isoformat()
        results = queries.query_membership_as_of(db, as_of_s)
        if sector:
            results = [r for r in results if (r.get("GICS Sector") or "").lower() == sector.lower()]
        return results
    finally:
        db.close()


def resolve_as_of(query: str, as_of: date) -> dict | None:
    """Resolve a ticker symbol OR company name to its canonical universe
    row as of `as_of`, or None if nothing matches. Same exact-then-partial
    matching semantics as data_mining.portfolio.resolve_symbol. Propagates
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
