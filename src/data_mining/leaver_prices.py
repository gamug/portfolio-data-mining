"""data_mining/leaver_prices.py

Fetch the daily EOD prices for the 33 delivered S&P 500 leaver lines from
Tiingo into one **private, local** file plus a manifest.

Follow-up of Task B (``docs/data_mining_leaver_prices.md`` in
``portfolio-financial-analysis``; the feasibility report of 2026-10-14). The
33 lines are the 35 requested lines minus ``INFO`` and ``WRK`` (both
unobtainable on Tiingo). This module fetches; ``portfolio-financial-analysis``
imports the file with its own step. Nothing is served by any service, and no
Tiingo value is redistributed or committed.

Why Tiingo (constitution Technological stock #6/#7, AI behavior #10): the
gateway serves no candles at all for these delisted tickers (Finnhub free
tier then yfinance both keep no history for them), so a private one-off
build-step source is used instead. The key is read only from
``$TIINGO_API_KEY`` and sent only in the ``Authorization: Token`` header --
never in a URL, a file, a log or a commit.

What it reuses from the feasibility report
------------------------------------------
- **Trims** (report §3): a row is real only if it is an exchange trade. The
  rule is applied here -- a carried close with ~0 volume
  (``close == prev_close`` and ``volume < CARRIED_VOLUME_RATIO * median``), or
  an OTC symbol after the exchange delisting -- and the *detected set* is
  checked against the report's committed ``(symbol, date, reason)`` table. A
  difference is a stop-and-ask (nothing is written).
- **Split formula** (report §4): ``split_cum(d) = product of splitFactor(s)
  for split ex-dates s > d``; ``adj_price = raw / split_cum``;
  ``adj_volume = raw * split_cum``. Never Tiingo's own ``adjClose`` (it also
  removes dividends).
- **Identity checks** (report §5): the symbol mapping (``SIVB->SIVBQ``,
  ``FRC->FRCB``, ``DAY->DAY``, ``WBD->WBD``; ``CDAY``/``DISCA`` are rejected
  stubs), the WBD-is-Series-A proof against production, and the gateway's
  0-candle count that is the reason Tiingo is needed at all. The report's
  SEC-anchor / spread values are private provenance (see below).

Prices are never committed
--------------------------
Row counts, session counts, dates, CIKs, symbols and the trim reasons are
committed. Every *price* -- the report §2 last real closes and the
SEC-anchor / spread values (AVB, CMA, SIVB, ANSS) -- lives in a private
expectations file inside the (never-committed) output directory. The script
reads it at run time, uses it for the report-reproduction check, and copies
it into the manifest as provenance.

Output (brief §3), under ``/workspaces/thesis/data/private/tiingo/``
--------------------------------------------------------------------
- ``leaver_prices.csv`` -- one row per line and session (columns below);
- ``leaver_prices.manifest.json`` -- the fetch record;
- ``request_log.json`` -- the persisted rolling-hour request timestamps
  (timestamps only, never the key or a URL), so a crash and a re-run within
  the hour do not start the counter from zero;
- ``expectations.json`` -- the private *input* (prices), written by the
  operator, not by this module.

The script refuses an output path inside a git work tree, creates the
directory 0700 and the files 0600, then reads the effective modes back (on
this machine ``/workspaces/thesis`` is a Windows drive over ``v9fs`` where
``chmod`` is a no-op -- the modes are recorded, not enforced, and a mismatch
is not a failure).
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import stat
import statistics
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
from portfolio_common.db import Database, in_clause

from data_mining.nyse_calendar import is_trading_day

log = logging.getLogger(__name__)

# -- endpoints and paths ---------------------------------------------------

TIINGO_PRICES_URL = "https://api.tiingo.com/tiingo/daily/{symbol}/prices"
# The shared gateway mounts the pricing service under /pricing, whose own
# route is /pricing/{ticker} -- so the full path is /pricing/pricing/{ticker}.
GATEWAY_BASE = "http://localhost:8000/pricing/pricing"

START = "2019-01-02"

DEFAULT_OUTPUT_DIR = Path("/workspaces/thesis/data/private/tiingo")
DATA_FILENAME = "leaver_prices.csv"
MANIFEST_FILENAME = "leaver_prices.manifest.json"
EXPECTATIONS_FILENAME = "expectations.json"
#: Persisted request timestamps (timestamps only), so a re-run within the hour
#: does not start the rolling-hour counter from zero.
REQUEST_LOG_FILENAME = "request_log.json"

FINANCIAL_DB = Path("/workspaces/thesis/data/financial.db")
FINANCIAL_DB_SHA1 = "5c5c0642619bcc2f80aa72f13599bf824f676675"
UNIVERSE_GICS_DB = Path("/workspaces/thesis/data/universe_history_gics.db")
UNIVERSE_GICS_SHA1 = "db5b16290b03a191b55215e8b98fc893a93f0e5b"
BRIEF_PATH = Path("/workspaces/thesis/handoff/data_mining_tiingo_fetch_task.md")
#: The feasibility scratch directory (brief §6 deletion step). Not a tempfile:
#: a fixed, documented path that must be deleted once the file passes §4.
FEASIBILITY_SCRATCH = "/tmp/tiingo_feasibility"  # noqa: S108
# Every production file whose sha1 is recorded before and after the run.
PRODUCTION_DBS: tuple[Path, ...] = (
    FINANCIAL_DB,
    UNIVERSE_GICS_DB,
    Path("/workspaces/thesis/data/universe_history.db"),
    Path("/workspaces/thesis/data/universe.db"),
)

# -- Tiingo request budget (brief §5) --------------------------------------

MAX_REQUESTS_PER_HOUR = 50
REQUEST_HEADROOM = 49  # stop before the 50th
PACE_SECONDS = 1.2

# -- trim rule (report §3) -------------------------------------------------

#: A carried close sits far below this fraction of the line's median volume
#: (measured: every carried row <= 0.049x, every real same-close row >= 0.225x).
CARRIED_VOLUME_RATIO = 0.10

#: Exchange-delisting date per OTC symbol: every row on or after it is an OTC
#: quote, not an exchange trade.
OTC_DELISTINGS: dict[str, str] = {
    "SIVBQ": "2023-03-10",
    "FRCB": "2023-05-01",
}


@dataclass(frozen=True)
class LineSpec:
    """One delivered leaver line.

    Every field here is committed: CIK, symbols, dates and counts. No price.
    """

    ask: str
    cik: str
    symbol: str  # Tiingo symbol
    valid_to: str  # last membership day (latest valid_to), inclusive
    expected_sessions: int  # report §2 "expected" NYSE sessions
    expected_rows: int  # report §2 rows kept, after trims
    first_date: str
    last_date: str
    trims: tuple[tuple[str, str], ...]  # (date, reason_class) -- carried/halt/otc
    notes: tuple[str, ...] = ()  # committed, non-price notes surfaced in the manifest


LINES: tuple[LineSpec, ...] = (
    LineSpec(
        "XLNX",
        "0000743988",
        "XLNX",
        "2022-02-14",
        787,
        786,
        "2019-01-02",
        "2022-02-11",
        (("2022-02-14", "carried"),),
    ),
    LineSpec("PBCT", "0001378946", "PBCT", "2022-04-03", 820, 820, "2019-01-02", "2022-04-01", ()),
    LineSpec(
        "DISCK", "0001437107", "DISCK", "2022-04-10", 825, 825, "2019-01-02", "2022-04-08", ()
    ),
    LineSpec("CERN", "0000804753", "CERN", "2022-06-07", 865, 865, "2019-01-02", "2022-06-07", ()),
    LineSpec(
        "CTXS",
        "0000877890",
        "CTXS",
        "2022-10-02",
        945,
        944,
        "2019-01-02",
        "2022-09-29",
        (("2022-09-30", "carried"),),
    ),
    LineSpec("DRE", "0000783280", "DRE", "2022-10-02", 945, 945, "2019-01-02", "2022-09-30", ()),
    LineSpec("NLSN", "0001492633", "NLSN", "2022-10-11", 952, 952, "2019-01-02", "2022-10-11", ()),
    LineSpec(
        "TWTR",
        "0001418091",
        "TWTR",
        "2022-10-31",
        966,
        964,
        "2019-01-02",
        "2022-10-27",
        (("2022-10-28", "carried"),),
    ),
    LineSpec(
        "ABMD", "0000815094", "ABMD", "2022-12-21", 1002, 1002, "2019-01-02", "2022-12-21", ()
    ),
    LineSpec(
        "SBNY",
        "0001288784",
        "SBNY",
        "2023-03-14",
        1057,
        1055,
        "2019-01-02",
        "2023-03-10",
        (
            ("2023-03-13", "carried"),
            ("2023-03-14", "carried"),
        ),
    ),
    LineSpec(
        "SIVB",
        "0000719739",
        "SIVBQ",
        "2023-03-14",
        1057,
        1054,
        "2019-01-02",
        "2023-03-09",
        (
            ("2023-03-10", "otc"),
            ("2023-03-13", "otc"),
            ("2023-03-14", "otc"),
        ),
    ),
    LineSpec(
        "FRC",
        "0001132979",
        "FRCB",
        "2023-05-03",
        1092,
        1089,
        "2019-01-02",
        "2023-04-28",
        (
            ("2023-05-01", "halt"),
            ("2023-05-02", "carried"),
            ("2023-05-03", "otc"),
        ),
    ),
    LineSpec(
        "DISH", "0001001082", "DISH", "2023-06-19", 1123, 1123, "2019-01-02", "2023-06-16", ()
    ),
    LineSpec(
        "ATVI",
        "0000718877",
        "ATVI",
        "2023-10-17",
        1207,
        1204,
        "2019-01-02",
        "2023-10-12",
        (("2023-10-13", "carried"),),
    ),
    LineSpec("SEE", "0001012100", "SEE", "2023-12-17", 1249, 1249, "2019-01-02", "2023-12-15", ()),
    LineSpec(
        "PXD",
        "0001038357",
        "PXD",
        "2024-05-07",
        1346,
        1343,
        "2019-01-02",
        "2024-05-02",
        (("2024-05-03", "carried"),),
    ),
    LineSpec("CMA", "0000028412", "CMA", "2024-06-23", 1377, 1377, "2019-01-02", "2024-06-21", ()),
    LineSpec(
        "MRO",
        "0000101778",
        "MRO",
        "2024-11-25",
        1486,
        1484,
        "2019-01-02",
        "2024-11-21",
        (("2024-11-22", "carried"),),
    ),
    LineSpec(
        "CTLT",
        "0001596783",
        "CTLT",
        "2024-12-22",
        1504,
        1501,
        "2019-01-02",
        "2024-12-17",
        (("2024-12-18", "carried"),),
    ),
    LineSpec(
        "QRVO", "0001604778", "QRVO", "2024-12-22", 1504, 1504, "2019-01-02", "2024-12-20", ()
    ),
    LineSpec("DFS", "0001393612", "DFS", "2025-05-18", 1603, 1603, "2019-01-02", "2025-05-16", ()),
    LineSpec(
        "JNPR",
        "0001043604",
        "JNPR",
        "2025-07-08",
        1637,
        1633,
        "2019-01-02",
        "2025-07-01",
        (("2025-07-02", "carried"),),
    ),
    LineSpec(
        "ANSS",
        "0001013462",
        "ANSS",
        "2025-07-17",
        1644,
        1643,
        "2019-01-02",
        "2025-07-16",
        (("2025-07-17", "carried"),),
    ),
    LineSpec(
        "HES",
        "0000004447",
        "HES",
        "2025-07-22",
        1647,
        1644,
        "2019-01-02",
        "2025-07-17",
        (("2025-07-18", "carried"),),
    ),
    LineSpec("WBA", "0001618921", "WBA", "2025-08-27", 1673, 1673, "2019-01-02", "2025-08-27", ()),
    LineSpec("IPG", "0000051644", "IPG", "2025-11-27", 1737, 1737, "2019-01-02", "2025-11-26", ()),
    LineSpec(
        "K",
        "0000055067",
        "K",
        "2025-12-10",
        1746,
        1746,
        "2019-01-02",
        "2025-12-10",
        (),
        (
            "2023-10-02 splitFactor 1.065: the WK Kellogg spin-off, applied as a "
            "split -- the same convention as the gateway, which reports DHR's Veralto "
            "spin-off as a 1.128 split on the same day.",
        ),
    ),
    LineSpec("DAY", "0001725057", "DAY", "2026-02-08", 1785, 1782, "2019-01-02", "2026-02-03", ()),
    LineSpec(
        "HOLX",
        "0000859737",
        "HOLX",
        "2026-04-08",
        1826,
        1824,
        "2019-01-02",
        "2026-04-06",
        (("2026-04-07", "carried"),),
    ),
    LineSpec(
        "CTRA", "0000858470", "CTRA", "2026-05-06", 1846, 1846, "2019-01-02", "2026-05-06", ()
    ),
    LineSpec("EA", "0000712515", "EA", "2026-08-04", 1907, 1907, "2019-01-02", "2026-08-04", ()),
    LineSpec(
        "AVB",
        "0000915912",
        "AVB",
        "2026-08-17",
        1916,
        1915,
        "2019-01-02",
        "2026-08-14",
        (("2026-08-17", "carried"),),
    ),
    LineSpec("WBD", "0001437107", "WBD", "2026-10-05", 1950, 1950, "2019-01-02", "2026-10-05", ()),
)

#: Lines in the request that Tiingo cannot serve (report §10). Reported, never filled.
NOT_DELIVERED: tuple[dict[str, str], ...] = (
    {
        "ask": "INFO",
        "cik": "0001598014",
        "reason": "Tiingo INFO is a reused 2024+ ETF; no IHS Markit history",
    },
    {
        "ask": "WRK",
        "cik": "0001732845",
        "reason": "Tiingo returns a single 2024-07-05 row; no WestRock history",
    },
)

#: Convention controls (brief §4). The first three are split/dividend controls
#: compared against the gateway; AAPL is a dividend payer that also split
#: (4-for-1, 2020-08-31) and drives the dividend-formula check.
CONVENTION_TICKERS: tuple[str, ...] = ("TTD", "PVH", "KMX")
DIVIDEND_CONTROL = "AAPL"

#: Declared exceptions (report §6). Committed metadata only -- the values
#: (last real close, consideration, spread) stay in the private expectations
#: file and are merged into the manifest at run time.
DECLARED_EXCEPTIONS: dict[str, dict[str, str]] = {
    "ANSS": {
        "label": "real final-day trade below deal value, kept as traded",
        "accession": "0001193125-24-008112",
    }
}

#: Control tolerances, matching the report §4 observations (closes essentially
#: exact, KMX worst at $0.02; volumes differ only by vendor reconciliation).
CLOSE_TOLERANCE = 0.05
VOLUME_TOLERANCE = 0.05  # relative
#: Float slack for the report §2 last-close reproduction.
PRICE_EPSILON = 1e-6


class LeaverPricesError(RuntimeError):
    """A check failed, or the environment is not the one this run expects.

    Raised before anything is written, so a failed run leaves no partial file
    (brief §4: "write nothing, and stop and ask")."""


# -- small helpers ---------------------------------------------------------


def sha1_file(path: Path) -> str:
    return hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iso(day: date) -> str:
    return day.isoformat()


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_tiingo_date(value: str) -> str:
    """The session date of a Tiingo row: the date part of its UTC timestamp.

    Tiingo sends ``2019-01-02T00:00:00.000Z``. Only the date part is taken --
    converting to another time zone would move every row to the day before.
    """
    return value[:10]


def split_cum_factors(rows: list[dict[str, Any]]) -> dict[str, float]:
    """``split_cum(d) = product of splitFactor(s) for split ex-dates s > d``.

    A split is recorded on its ex-date and applies to the *earlier* dates, so
    a 10-for-1 split factor of 10 recorded on an ex-date divides every close
    before it (500.00 / 10 = 50.00) and multiplies their volume.
    """
    splits = [
        (_parse_tiingo_date(r["date"]), float(r["splitFactor"]))
        for r in rows
        if r.get("splitFactor") not in (None, 0, 1, 1.0)
    ]
    factors: dict[str, float] = {}
    for r in rows:
        day = _parse_tiingo_date(r["date"])
        factor = 1.0
        for split_day, split_factor in splits:
            if split_day > day:
                factor *= split_factor
        factors[day] = factor
    return factors


def detect_non_real(symbol: str, rows: list[dict[str, Any]]) -> dict[str, str]:
    """The rows of an in-range series that are not exchange trades.

    Returns ``{date: detected_by}`` with ``detected_by`` in
    ``{"carried", "halt", "otc"}`` -- the report §3 rule, applied
    mechanically and in this order:

    - **carried**: the close repeats the previous session's and the volume is
      exactly 0 (a carried-over quote after a deal/halt). This is checked
      first so a zero-volume post-delisting print stays ``carried`` rather
      than ``otc``;
    - **halt** / **otc**: the symbol is an OTC listing (``SIVBQ``/``FRCB``)
      and the row is on or after its exchange-delisting date. The delisting
      day itself is ``halt`` when the line later reprices on the OTC venue
      (a genuine halt, the OTC trade came later), else the delisting day is
      itself the first OTC print and is ``otc``; a row strictly after the
      delisting date is ``otc``;
    - **carried**: otherwise, the close repeats the previous session's and the
      volume is below :data:`CARRIED_VOLUME_RATIO` of the line's median volume.
    """
    volumes = [float(r["volume"]) for r in rows]
    median = statistics.median(volumes) if volumes else 0.0
    delisting = OTC_DELISTINGS.get(symbol)
    out: dict[str, str] = {}
    for index, r in enumerate(rows):
        day = _parse_tiingo_date(r["date"])
        repeats_previous = index > 0 and r["close"] == rows[index - 1]["close"]
        if repeats_previous and float(r["volume"]) == 0:
            out[day] = "carried"
            continue
        if delisting is not None and day >= delisting:
            if day > delisting:
                out[day] = "otc"
                continue
            later_closes = [later["close"] for later in rows[index + 1 :]]
            reprices = repeats_previous and any(close != r["close"] for close in later_closes)
            out[day] = "halt" if reprices else "otc"
            continue
        if repeats_previous and median > 0 and float(r["volume"]) < CARRIED_VOLUME_RATIO * median:
            out[day] = "carried"
    return out


def build_series(
    symbol: str, raw_rows: list[dict[str, Any]], valid_to: str
) -> list[dict[str, Any]]:
    """Split-adjusted, dividend-unadjusted rows in ``[START, valid_to]``.

    ``divCash``/``splitFactor`` are kept exactly as Tiingo returns them; the
    analysis repo decides how to load them.
    """
    factors = split_cum_factors(raw_rows)
    out: list[dict[str, Any]] = []
    for r in raw_rows:
        day = _parse_tiingo_date(r["date"])
        if not (START <= day <= valid_to):
            continue
        factor = factors[day]
        out.append(
            {
                "date": day,
                "adj_open": r["open"] / factor,
                "adj_high": r["high"] / factor,
                "adj_low": r["low"] / factor,
                "adj_close": r["close"] / factor,
                "adj_volume": round(r["volume"] * factor),
                "raw_close": r["close"],
                "raw_volume": r["volume"],
                "divCash": r.get("divCash"),
                "splitFactor": r.get("splitFactor"),
            }
        )
    out.sort(key=lambda row: row["date"])
    return out


def expected_sessions(valid_to: str, start: str = START) -> int:
    """The NYSE session count in ``[start, valid_to]`` (inclusive)."""
    return len([d for d in _days(start, valid_to) if is_trading_day(d)])


def _days(start: str, end: str) -> list[date]:
    first, last = _date(start), _date(end)
    out: list[date] = []
    day = first
    while day <= last:
        out.append(day)
        day = date.fromordinal(day.toordinal() + 1)
    return out


# -- request budget --------------------------------------------------------


@dataclass
class RateLimiter:
    """A rolling-hour request counter that **persists** its request times to a
    file in the private folder, so a crash and a re-run within the hour do not
    start from zero. The file holds timestamps only -- never the key, never a
    URL. Refuses the request that would reach the cap."""

    max_per_hour: int = MAX_REQUESTS_PER_HOUR
    headroom: int = REQUEST_HEADROOM
    path: Path | None = None
    _times: list[datetime] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.path is not None and self.path.exists():
            self._times = [datetime.fromisoformat(t) for t in json.loads(self.path.read_text())]

    def _within_last_hour(self, now: datetime) -> list[datetime]:
        cutoff = now - timedelta(hours=1)
        return [t for t in self._times if t > cutoff]

    def _save(self) -> None:
        if self.path is not None:
            self.path.write_text(json.dumps([t.isoformat() for t in self._times]))

    def check(self) -> None:
        now = datetime.now(UTC)
        recent = self._within_last_hour(now)
        if len(recent) >= self.headroom:
            raise LeaverPricesError(
                f"rolling-hour budget reached ({len(recent)}/{self.max_per_hour}) -- stopping"
            )
        self._times.append(now)
        self._save()

    def rolling_count(self) -> int:
        return len(self._within_last_hour(datetime.now(UTC)))

    def total_all_runs(self) -> int:
        return len(self._times)

    def largest_rolling_hour(self) -> int:
        times = sorted(self._times)
        best = 0
        for moment in times:
            cutoff = moment - timedelta(hours=1)
            best = max(best, sum(1 for other in times if cutoff < other <= moment))
        return best


# -- environment checks (brief §3/§5) --------------------------------------


def machine_check(financial_db: Path = FINANCIAL_DB, brief_path: Path = BRIEF_PATH) -> None:
    """The fetch must run on the machine whose ``thesis/data`` is the shared
    Windows folder: the financial DB's sha1 must match, and the brief must be
    present. Either failing is a stop."""
    if not financial_db.exists():
        raise LeaverPricesError(f"financial DB not found: {financial_db}")
    actual = sha1_file(financial_db)
    if actual != FINANCIAL_DB_SHA1:
        raise LeaverPricesError(
            f"financial DB sha1 is {actual}, expected {FINANCIAL_DB_SHA1} -- wrong machine?"
        )
    if not brief_path.exists():
        raise LeaverPricesError(f"brief not found: {brief_path} -- wrong machine?")


def assert_not_in_git_worktree(path: Path) -> None:
    """Refuse an output path inside a git work tree (brief §3)."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        result = subprocess.run(  # noqa: S603 -- fixed, trusted argv; no shell, no user input
            ["git", "-C", str(probe), "rev-parse", "--is-inside-work-tree"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:  # no git on PATH -> cannot be a work tree
        return
    if result.stdout.strip() == "true":
        raise LeaverPricesError(f"output path is inside a git work tree: {path}")


def _filesystem_type(path: Path) -> str:
    result = subprocess.run(  # noqa: S603 -- fixed, trusted argv; no shell, no user input
        ["stat", "-f", "-c", "%T", str(path)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or "unknown"


def _effective_modes(path: Path) -> str:
    return oct(stat.S_IMODE(path.stat().st_mode))


# -- read-only production reads --------------------------------------------


def read_membership_spans(
    symbols: list[str], universe_db: Path = UNIVERSE_GICS_DB
) -> dict[str, list[dict[str, str]]]:
    """``{symbol: [{"cik", "valid_from", "valid_to"}, ...]}`` -- **every** stint
    of the line, oldest first, from the delivered universe-history DB, read-only
    (never ALTERs or writes it)."""
    db = Database.connect(universe_db, read_only=True)
    try:
        placeholders = in_clause(symbols)
        rows = db.execute(
            f"SELECT symbol, cik, valid_from, valid_to FROM universe_membership "  # noqa: S608
            f"WHERE symbol IN {placeholders}",
            symbols,
        ).fetchall()
    finally:
        db.close()
    spans: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        spans.setdefault(row["symbol"], []).append(
            {
                "cik": row["cik"] or "",
                "valid_from": row["valid_from"] or "",
                "valid_to": row["valid_to"] or "",
            }
        )
    for stints in spans.values():
        stints.sort(key=lambda stint: stint["valid_from"])
    return spans


def production_stored_count(cik: str, ticker: str, financial_db: Path = FINANCIAL_DB) -> int:
    """Rows stored in production's ``price_daily`` for the line's **own** asset,
    matched by CIK *and* ticker -- a CIK can carry several tickers (DISCK and
    WBD share CIK 0001437107, but production stores only the WBD asset, so
    DISCK is 0 and WBD is 1,189)."""
    db = Database.connect(financial_db, read_only=True)
    try:
        assets = db.execute(
            "SELECT id FROM assets WHERE cik = ? AND UPPER(ticker) = UPPER(?)",
            (cik, ticker),
        ).fetchall()
        total = 0
        for asset in assets:
            (count,) = db.execute(
                "SELECT COUNT(*) FROM price_daily WHERE asset_id = ?", (asset["id"],)
            ).fetchone()
            total += int(count)
        return total
    finally:
        db.close()


def production_wbd_closes(financial_db: Path = FINANCIAL_DB) -> dict[str, int]:
    """``{date: close_in_cents}`` for the WBD asset, read-only."""
    db = Database.connect(financial_db, read_only=True)
    try:
        assets = db.execute("SELECT id FROM assets WHERE cik = ?", ("0001437107",)).fetchall()
        closes: dict[str, int] = {}
        for asset in assets:
            for row in db.execute(
                "SELECT date, close FROM price_daily WHERE asset_id = ?", (asset["id"],)
            ):
                closes[str(row["date"])] = round(float(row["close"]) * 100)
        return closes
    finally:
        db.close()


# -- HTTP ------------------------------------------------------------------


def _tiingo_fetch(
    client: httpx.Client, limiter: RateLimiter, symbol: str, start: str, end: str
) -> list[dict[str, Any]]:
    limiter.check()
    response = client.get(
        TIINGO_PRICES_URL.format(symbol=symbol),
        params={"startDate": start, "endDate": end, "format": "json"},
    )
    response.raise_for_status()
    return cast("list[dict[str, Any]]", response.json())


def _gateway_candles(
    client: httpx.Client, ticker: str, start: str, end: str
) -> list[dict[str, Any]]:
    response = client.get(f"{GATEWAY_BASE}/{ticker}", params={"start_date": start, "end_date": end})
    response.raise_for_status()
    return cast("list[dict[str, Any]]", response.json().get("candles", []))


def _gateway_dividends(
    client: httpx.Client, ticker: str, start: str, end: str
) -> list[dict[str, Any]]:
    response = client.get(
        f"{GATEWAY_BASE}/{ticker}/actions", params={"start_date": start, "end_date": end}
    )
    response.raise_for_status()
    return cast("list[dict[str, Any]]", response.json().get("dividends", []))


# -- per-line build + checks ----------------------------------------------


@dataclass
class LineResult:
    spec: LineSpec
    series: list[dict[str, Any]]  # trimmed, adjusted
    detected: dict[str, str]  # date -> detected_by
    gateway_candles: int
    production_rows: int


def _check_line(
    spec: LineSpec,
    series: list[dict[str, Any]],
    detected: dict[str, str],
    expectations: dict[str, Any],
) -> list[str]:
    """Every report §2/§3 comparison for one line. Returns the mismatches
    (empty means it reproduces the report)."""
    mismatches: list[str] = []
    if len(series) != spec.expected_rows:
        mismatches.append(f"rows kept {len(series)} != {spec.expected_rows}")
    if series and series[0]["date"] != spec.first_date:
        mismatches.append(f"first date {series[0]['date']} != {spec.first_date}")
    if series and series[-1]["date"] != spec.last_date:
        mismatches.append(f"last date {series[-1]['date']} != {spec.last_date}")

    detected_set = set(detected)
    expected_set = {day for day, _ in spec.trims}
    if detected_set != expected_set:
        missing = sorted(expected_set - detected_set)
        extra = sorted(detected_set - expected_set)
        mismatches.append(f"trim set differs (not detected: {missing}; extra: {extra})")

    # Compare the reason class too, not only the date (reviewer fix 7).
    expected_reasons = {day: reason for day, reason in spec.trims}
    for day, reason in expected_reasons.items():
        got = detected.get(day)
        if got is not None and got != reason:
            mismatches.append(f"{day}: detected {got!r} but report §3 says {reason!r}")

    last_close = expectations.get("last_real_close", {}).get(spec.ask)
    if last_close is None:
        mismatches.append(f"no expected last close in {EXPECTATIONS_FILENAME}")
    elif series and abs(series[-1]["adj_close"] - float(last_close)) > PRICE_EPSILON:
        mismatches.append(f"last real close {series[-1]['adj_close']} != expected")
    return mismatches


def _compare_convention(
    ticker: str, tiingo_series: list[dict[str, Any]], gateway: list[dict[str, Any]]
) -> dict[str, Any]:
    """Tiingo's split-adjusted series vs the gateway's yfinance series, on
    their shared dates."""
    tiingo_by_date = {row["date"]: row for row in tiingo_series}
    shared = 0
    max_close = 0.0
    max_volume_abs = 0.0
    max_volume_rel = 0.0
    for candle in gateway:
        day = candle["date"][:10]
        row = tiingo_by_date.get(day)
        if row is None:
            continue
        shared += 1
        max_close = max(max_close, abs(row["adj_close"] - float(candle["close"])))
        gateway_volume = float(candle["volume"])
        max_volume_abs = max(max_volume_abs, abs(row["adj_volume"] - gateway_volume))
        if gateway_volume > 0:
            max_volume_rel = max(
                max_volume_rel, abs(row["adj_volume"] - gateway_volume) / gateway_volume
            )
    return {
        "ticker": ticker,
        "shared_dates": shared,
        "max_abs_close_diff": round(max_close, 6),
        "max_abs_volume_diff": round(max_volume_abs, 3),
        "max_volume_rel_diff": round(max_volume_rel, 6),
    }


def _dividend_formula(
    tiingo_series: list[dict[str, Any]], gateway_dividends: list[dict[str, Any]]
) -> dict[str, Any]:
    """Which formula makes Tiingo's ``divCash`` equal the gateway's dividends:
    raw, or divided by ``split_cum`` (a split-adjusted series divides pre-split
    cash dividends too)."""
    tiingo_by_date = {row["date"]: row for row in tiingo_series}
    factors = split_cum_factors(
        [{"date": row["date"], "splitFactor": row["splitFactor"]} for row in tiingo_series]
    )
    shared = 0
    max_raw = 0.0
    max_adjusted = 0.0
    for dividend in gateway_dividends:
        day = dividend["date"][:10]
        row = tiingo_by_date.get(day)
        if row is None:
            continue
        shared += 1
        value = float(dividend["value"])
        raw = float(row["divCash"] or 0.0)
        adjusted = raw / factors.get(day, 1.0)
        max_raw = max(max_raw, abs(raw - value))
        max_adjusted = max(max_adjusted, abs(adjusted - value))
    formula = "divCash / split_cum" if max_adjusted <= max_raw else "divCash (raw)"
    return {
        "control": DIVIDEND_CONTROL,
        "shared_dates": shared,
        "formula": formula,
        "max_abs_diff_raw": round(max_raw, 6),
        "max_abs_diff_adjusted": round(max_adjusted, 6),
    }


# -- the run ---------------------------------------------------------------


def _validate_membership(spans: dict[str, list[dict[str, str]]], universe_db: Path) -> None:
    """Stop-and-ask if a line's latest valid_to changed, or if it no longer
    gives report §2's expected session count (brief §3, correction 7)."""
    for spec in LINES:
        stints = spans.get(spec.ask)
        if not stints:
            raise LeaverPricesError(f"{spec.ask}: no membership stint in {universe_db}")
        latest = max(stint["valid_to"] for stint in stints)
        if latest != spec.valid_to:
            raise LeaverPricesError(
                f"{spec.ask}: latest valid_to is {latest!r}, expected "
                f"{spec.valid_to!r} -- membership span changed; stop and ask"
            )
        if expected_sessions(spec.valid_to) != spec.expected_sessions:
            raise LeaverPricesError(
                f"{spec.ask}: valid_to {spec.valid_to} gives a different expected session "
                f"count than report §2 -- stop and ask"
            )


def _assert_on_calendar(spec: LineSpec, raw: list[dict[str, Any]]) -> None:
    """Reviewer A's rule: every row's date must be an NYSE session, else the
    run stops and nothing is written."""
    for row in raw:
        day = _parse_tiingo_date(row["date"])
        if not is_trading_day(date.fromisoformat(day)):
            raise LeaverPricesError(
                f"{spec.ask}: {day} is not an NYSE session -- date off the calendar; stop and ask"
            )


def _fetch_lines(
    tiingo_client: httpx.Client,
    gateway_client: httpx.Client,
    limiter: RateLimiter,
    financial_db: Path,
    expectations: dict[str, Any],
    pace_seconds: float,
) -> tuple[list[LineResult], list[dict[str, Any]]]:
    """Fetch, build and check every line. Returns the results and the
    report-reproduction mismatches (empty means it reproduces report §2/§3)."""
    results: list[LineResult] = []
    report_mismatches: list[dict[str, Any]] = []
    served_by_gateway: list[str] = []
    for spec in LINES:
        raw = _tiingo_fetch(tiingo_client, limiter, spec.symbol, START, spec.valid_to)
        _assert_on_calendar(spec, raw)
        series = build_series(spec.symbol, raw, spec.valid_to)
        in_range = [r for r in raw if START <= _parse_tiingo_date(r["date"]) <= spec.valid_to]
        detected = detect_non_real(spec.symbol, in_range)
        kept = [row for row in series if row["date"] not in detected]
        mismatches = _check_line(spec, kept, detected, expectations)
        if mismatches:
            report_mismatches.append({"ask": spec.ask, "mismatches": mismatches})
        candles = _gateway_candles(gateway_client, spec.ask, START, spec.valid_to)
        if candles:
            served_by_gateway.append(f"{spec.ask} ({len(candles)})")
        stored = production_stored_count(spec.cik, spec.ask, financial_db)
        results.append(LineResult(spec, kept, detected, len(candles), stored))
        if pace_seconds:
            time.sleep(pace_seconds)
    if served_by_gateway:
        raise LeaverPricesError(
            "the gateway served candles for a line that must be Tiingo-only "
            f"({', '.join(served_by_gateway)}) -- one vendor per line; stop and ask"
        )
    return results, report_mismatches


def fetch_leaver_prices(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    expectations_path: Path | None = None,
    tiingo_client: httpx.Client | None = None,
    gateway_client: httpx.Client | None = None,
    financial_db: Path = FINANCIAL_DB,
    universe_db: Path = UNIVERSE_GICS_DB,
    brief_path: Path = BRIEF_PATH,
    production_dbs: tuple[Path, ...] | None = None,
    pace_seconds: float = PACE_SECONDS,
) -> dict[str, Any]:
    """Run every check, then write the data file and the manifest.

    Raises :class:`LeaverPricesError` (writing nothing) if any check fails.
    Returns the manifest dict.
    """
    expectations_path = expectations_path or (output_dir / EXPECTATIONS_FILENAME)
    production_dbs = production_dbs if production_dbs is not None else PRODUCTION_DBS

    machine_check(financial_db, brief_path)
    key = os.environ.get("TIINGO_API_KEY")
    if not key:
        raise LeaverPricesError("TIINGO_API_KEY is not set")
    if not expectations_path.exists():
        raise LeaverPricesError(f"private expectations file not found: {expectations_path}")
    expectations = json.loads(expectations_path.read_text())

    assert_not_in_git_worktree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    sha1_before = {str(p): sha1_file(p) for p in production_dbs if p.exists()}
    spans = read_membership_spans([spec.ask for spec in LINES], universe_db)
    _validate_membership(spans, universe_db)

    owns_tiingo = tiingo_client is None
    owns_gateway = gateway_client is None
    tiingo_client = tiingo_client or httpx.Client(
        headers={"Authorization": f"Token {key}"}, timeout=60.0
    )
    gateway_client = gateway_client or httpx.Client(timeout=60.0)
    limiter = RateLimiter(path=output_dir / REQUEST_LOG_FILENAME)
    requests_before = limiter.total_all_runs()
    try:
        results, report_mismatches = _fetch_lines(
            tiingo_client, gateway_client, limiter, financial_db, expectations, pace_seconds
        )
        if report_mismatches:
            raise LeaverPricesError(
                "report reproduction failed; nothing written:\n"
                + json.dumps(report_mismatches, indent=2)
            )
        wbd_series = next((r.series for r in results if r.spec.ask == "WBD"), None)
        checks = _run_controls(
            tiingo_client, gateway_client, limiter, financial_db, wbd_series, pace_seconds
        )
    finally:
        if owns_tiingo:
            tiingo_client.close()
        if owns_gateway:
            gateway_client.close()

    sha1_after = {str(p): sha1_file(p) for p in production_dbs if p.exists()}
    if sha1_before != sha1_after:
        raise LeaverPricesError("a production DB changed during the run")

    return _write_outputs(
        output_dir,
        expectations_path,
        results,
        checks,
        limiter,
        sha1_before,
        sha1_after,
        spans,
        requests_this_run=limiter.total_all_runs() - requests_before,
    )


def _run_controls(
    tiingo_client: httpx.Client,
    gateway_client: httpx.Client,
    limiter: RateLimiter,
    financial_db: Path,
    wbd_series: list[dict[str, Any]] | None,
    pace_seconds: float,
) -> dict[str, Any]:
    controls: dict[str, Any] = {"convention": [], "dividend": None, "wbd_vs_production": None}
    for ticker in CONVENTION_TICKERS:
        raw = _tiingo_fetch(tiingo_client, limiter, ticker, START, "2026-10-14")
        series = build_series(ticker, raw, "2026-10-14")
        gateway = _gateway_candles(gateway_client, ticker, START, "2026-10-14")
        comparison = _compare_convention(ticker, series, gateway)
        if comparison["max_abs_close_diff"] > CLOSE_TOLERANCE:
            raise LeaverPricesError(
                f"convention control {ticker}: close differs by "
                f"{comparison['max_abs_close_diff']} > {CLOSE_TOLERANCE}"
            )
        controls["convention"].append(comparison)
        if pace_seconds:
            time.sleep(pace_seconds)

    raw = _tiingo_fetch(tiingo_client, limiter, DIVIDEND_CONTROL, START, "2026-10-14")
    series = build_series(DIVIDEND_CONTROL, raw, "2026-10-14")
    dividends = _gateway_dividends(gateway_client, DIVIDEND_CONTROL, START, "2026-10-14")
    controls["dividend"] = _dividend_formula(series, dividends)

    # WBD is compared against production, not against the gateway (which no
    # longer serves it). Reuses the WBD line's own series -- no extra request.
    if wbd_series is not None:
        production = production_wbd_closes(financial_db)
        shared = 0
        max_diff = 0
        for row in wbd_series:
            stored = production.get(row["date"])
            if stored is None:
                continue
            shared += 1
            diff = abs(round(row["adj_close"] * 100) - stored)
            max_diff = max(max_diff, diff)
        if max_diff > 1:
            raise LeaverPricesError(
                f"WBD vs production: {max_diff} cents > 1 cent on a shared date"
            )
        controls["wbd_vs_production"] = {"shared_dates": shared, "max_abs_diff_cents": max_diff}
    return controls


def _write_outputs(
    output_dir: Path,
    expectations_path: Path,
    results: list[LineResult],
    checks: dict[str, Any],
    limiter: RateLimiter,
    sha1_before: dict[str, str],
    sha1_after: dict[str, str],
    spans: dict[str, list[dict[str, str]]],
    *,
    requests_this_run: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output_dir, 0o700)
    data_path = output_dir / DATA_FILENAME

    with data_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "ask_ticker",
                "cik",
                "tiingo_symbol",
                "date",
                "adj_open",
                "adj_high",
                "adj_low",
                "adj_close",
                "adj_volume",
                "raw_close",
                "raw_volume",
                "divCash",
                "splitFactor",
            ]
        )
        for result in results:
            for row in result.series:
                writer.writerow(
                    [
                        result.spec.ask,
                        result.spec.cik,
                        result.spec.symbol,
                        row["date"],
                        row["adj_open"],
                        row["adj_high"],
                        row["adj_low"],
                        row["adj_close"],
                        row["adj_volume"],
                        row["raw_close"],
                        row["raw_volume"],
                        row["divCash"],
                        row["splitFactor"],
                    ]
                )
    os.chmod(data_path, 0o600)

    manifest = {
        "fetch_time_utc": _utc_now(),
        "formula": (
            "split_cum(d) = product of splitFactor(s) for split ex-dates s > d; "
            "adj_price = raw_price / split_cum; adj_volume = raw_volume * split_cum; "
            "dividends are NOT removed (never Tiingo's own adjClose)."
        ),
        "trim_rule": (
            "a row is real only if it is an exchange trade. The rule is applied by class "
            "(carried / halt / otc) and checked against the committed per-line table; a "
            "class mismatch stops the run. carried = close == prev_close with volume 0, or "
            "with volume < 0.10 * median; halt = the OTC symbol's exchange-delisting day "
            "when the line later reprices; otc = any other row on/after the delisting date."
        ),
        "data_file": DATA_FILENAME,
        "data_file_sha256": sha256_file(data_path),
        "request_count": {
            "this_run": requests_this_run,
            "total_all_runs": limiter.total_all_runs(),
            "rolling_hour_now": limiter.rolling_count(),
            "largest_rolling_hour": limiter.largest_rolling_hour(),
            "cap_per_hour": MAX_REQUESTS_PER_HOUR,
            "log_file": REQUEST_LOG_FILENAME,
        },
        "tiingo_terms": {
            "licence": "UNCONFIRMED",
            "quote": (
                'Tiingo free tier: "Internal Use Only ... you may not display or share the '
                'data with another person or organization".'
            ),
        },
        "output": {
            "directory": str(output_dir),
            "directory_mode": _effective_modes(output_dir),
            "data_file_mode": _effective_modes(data_path),
            "filesystem_type": _filesystem_type(output_dir),
            "request_log": REQUEST_LOG_FILENAME,
            "note": "chmod is a no-op on this filesystem; effective modes are recorded, not enforced.",
        },
        "production_db_sha1_before": sha1_before,
        "production_db_sha1_after": sha1_after,
        "expectations_file": str(expectations_path),
        "provenance": _provenance(expectations_path),
        "declared_exception": _declared_exceptions(expectations_path),
        "checks": checks,
        "lines": [
            {
                "ask": r.spec.ask,
                "cik": r.spec.cik,
                "tiingo_symbol": r.spec.symbol,
                "rows_kept": len(r.series),
                "expected_nyse_sessions": r.spec.expected_sessions,
                "missing_sessions": [
                    _iso(d)
                    for d in _days(START, r.spec.valid_to)
                    if is_trading_day(d) and _iso(d) not in {row["date"] for row in r.series}
                ],
                "trimmed_rows": [
                    {"date": day, "reason": reason, "detected_by": r.detected.get(day)}
                    for day, reason in r.spec.trims
                ],
                "first_date": r.series[0]["date"] if r.series else None,
                "last_date": r.series[-1]["date"] if r.series else None,
                "membership_span": spans.get(r.spec.ask, []),
                "notes": list(r.spec.notes),
                "why_tiingo": {
                    "gateway_candle_count": r.gateway_candles,
                    "production_stored_count": r.production_rows,
                },
                "dividend_rows": sum(1 for row in r.series if float(row["divCash"] or 0) > 0),
                "split_rows": sum(
                    1 for row in r.series if float(row["splitFactor"] or 1) not in (0, 1)
                ),
            }
            for r in results
        ],
        "not_delivered": list(NOT_DELIVERED),
        "deletion_step": _deletion_step(output_dir),
    }
    manifest_path = output_dir / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2))
    os.chmod(manifest_path, 0o600)
    return manifest


def _declared_exceptions(expectations_path: Path) -> list[dict[str, Any]]:
    """The report §6 declared exceptions: the committed label/accession plus the
    private values (last real close, consideration, spread) from the
    expectations file."""
    private = json.loads(expectations_path.read_text()).get("declared_exception", {})
    out: list[dict[str, Any]] = []
    for ask, meta in DECLARED_EXCEPTIONS.items():
        out.append({"ask": ask, **private.get(ask, {}), **meta})
    return out


def _provenance(expectations_path: Path) -> dict[str, Any]:
    """The report §2/§5 prices and anchors, copied from the private file."""
    data = json.loads(expectations_path.read_text())
    return {
        "source": "Task B feasibility report (2026-10-14), §2 last real closes and §5 anchors",
        "sec_anchors": data.get("sec_anchors", {}),
        "declared_exception": data.get("declared_exception", {}),
        "spreads": data.get("spreads", {}),
    }


def _deletion_step(output_dir: Path) -> list[dict[str, str]]:
    """The one procedure covering every copy (brief §6)."""
    scratch = FEASIBILITY_SCRATCH
    return [
        {
            "copy": "this machine's private folder",
            "path": str(output_dir),
            "command": f"rm -rf {output_dir}",
            "check": f"test ! -e {output_dir}",
        },
        {
            "copy": "T-100's machine, if different",
            "path": "<T-100 thesis folder>/data/private/tiingo",
            "command": "rm -rf <T-100 thesis folder>/data/private/tiingo",
            "check": "test ! -e <T-100 thesis folder>/data/private/tiingo",
            "example": "on the user's Mac: /Users/dova/thesis/data/private/tiingo",
        },
        {
            "copy": "every backup of thesis/data the user names",
            "path": "<backup thesis folder>/data/private/tiingo",
            "command": "rm -rf <backup thesis folder>/data/private/tiingo",
            "check": "test ! -e <backup thesis folder>/data/private/tiingo",
        },
        {
            "copy": "feasibility scratch",
            "path": f"{scratch}/",
            "command": f"rm -rf {scratch}",
            "check": f"test ! -e {scratch}",
        },
    ]


__all__ = [
    "CONVENTION_TICKERS",
    "DEFAULT_OUTPUT_DIR",
    "DIVIDEND_CONTROL",
    "LINES",
    "NOT_DELIVERED",
    "LeaverPricesError",
    "LineSpec",
    "RateLimiter",
    "build_series",
    "detect_non_real",
    "expected_sessions",
    "fetch_leaver_prices",
    "machine_check",
    "split_cum_factors",
]
