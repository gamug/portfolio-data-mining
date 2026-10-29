"""data_mining/leaver_calibration.py

Fetch Tiingo's daily EOD prices for the **32 pre-2022 S&P 500 leaver lines**
into a second private calibration file, for reviewer A's GUARD-layer
calibration of 2019-2021.

Follow-up of ``data_mining/leaver_prices.py`` (the 33-line leaver-prices fetch
of 2026-10-15). It reuses that module's trims, split formula, rate limit and
key handling unchanged; it does not touch its output folder or its manifest.

Why a second file
-----------------
The S&P 500 members that left the index before 2022-01-03 are absent from the
analysis database. The pricing gateway serves 26 of the 58 missing lines; these
32 are delisted or acquired and it cannot serve them. The user keeps Tiingo
(2026-10-29 decision); the data stays private, is never committed, never
redistributed and never loaded into the analysis database.

Range and identity
------------------
- Start: ``2019-02-01``, or the line's first membership day if later (BMS).
- End: the line's last membership day (``valid_to`` in
  ``universe_history_gics.db``, read-only), inclusive.
- The CIK is verified on EDGAR: the submissions JSON names the entity, and an
  in-span 10-K/10-Q (or 20-F/6-K) cover page is checked for the ticker. Only
  the accession/form/date and a boolean are recorded -- no SEC document.
- Reused Tiingo symbols are mapped to the old security's own series (see
  ``CALIBRATION_LINES``); a line Tiingo does not have is reported, never
  proxied from another series, ticker or estimate.

Output (brief section 3), under
``/workspaces/thesis/data/private/tiingo/calibration_2019_2021/`` (outside
every git repository):
- ``prices.csv`` -- one row per line and session, the 2026-10-15 columns plus
  ``split_cum``;
- ``manifest.json`` -- the fetch record (counts, dates, hashes; no prices);
- ``request_log.json`` -- the persisted rolling-hour request timestamps.

The run is split in two phases because 64 requests exceed the 50/hour cap:
``phase a`` fetches the 32 price series plus 17 metadata records (49 requests);
``phase b`` fetches the remaining 15 metadata records, verifies the CIKs, and
writes the outputs. Raw responses are cached in a private ``.raw/`` folder
inside the output directory and deleted once the manifest is written, so a
crash does not re-spend the request budget.
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

import httpx

from data_mining.leaver_prices import (
    FINANCIAL_DB,
    FINANCIAL_DB_SHA1,
    GATEWAY_BASE,
    PACE_SECONDS,
    PRODUCTION_DBS,
    TIINGO_PRICES_URL,
    UNIVERSE_GICS_DB,
    LeaverPricesError,
    RateLimiter,
    assert_not_in_git_worktree,
    build_series,
    detect_non_real,
    machine_check,
    production_stored_count,
    read_membership_spans,
    sha1_file,
    sha256_file,
    split_cum_factors,
)
from data_mining.nyse_calendar import is_trading_day

# -- endpoints and paths ---------------------------------------------------

TIINGO_META_URL = "https://api.tiingo.com/tiingo/daily/{symbol}"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

CALIBRATION_START = "2019-02-01"
CALIBRATION_OUTPUT_DIR = Path("/workspaces/thesis/data/private/tiingo/calibration_2019_2021")
BRIEF_PATH = Path("/workspaces/thesis/handoff/data_mining_tiingo_calibration32_task.md")

DATA_FILENAME = "prices.csv"
MANIFEST_FILENAME = "manifest.json"
REQUEST_LOG_FILENAME = "request_log.json"
CACHE_DIRNAME = ".raw"

#: The 2026-10-15 AAPL control established that Tiingo's raw ``divCash`` is on
#: the raw price basis, so it must be divided by ``split_cum`` to sit on the
#: split-adjusted price basis this file writes (max residual 0.0).
DIVIDEND_FORMULA = "divCash / split_cum"

#: A session whose adjusted close-to-close return exceeds this is reported
#: (known cause) or stops the run (unexplained).
COVID = "March 2020 COVID-19 crash and rebound"
VACCINE = "2020-11-09 vaccine rally"
CORPORATE_EVENT = "corporate event"
MARKET_WIDE = "market-wide move"
JUMP_THRESHOLD = 0.25

#: EDGAR allows 10 requests/second; stay well below it.
#: splitFactor rows that are distributions, not splits (reviewer B). The
#: adjusted series divides by them, so the line's returns exclude the spin-off.
KNOWN_DISTRIBUTIONS: dict[tuple[str, str], str] = {
    ("DWDP", "2019-04-02"): (
        "Dow Inc. distribution (ex-date 2019-04-01; Tiingo records the factor on 2019-04-02)"
    ),
}
_SIMPLE_SPLIT_FACTORS = {0.1, 0.2, 0.25, 0.5, 2.0, 3.0, 4.0, 5.0, 10.0, 15.0, 20.0}

#: EDGAR allows 10 requests/second; stay well below it.
SEC_PACE_SECONDS = 0.15

#: Forms whose cover page names the listed security.
COVER_FORMS = ("10-K", "20-F", "40-F", "10-Q", "6-K")
_COVER_FORM_RANK = {"10-K": 4, "20-F": 4, "40-F": 4, "10-Q": 3, "6-K": 2}


@dataclass(frozen=True)
class CalibrationLine:
    """One pre-2022 leaver line. All fields are committed: no price."""

    ask: str
    cik: str  # verified CIK of the entity that traded under ``ask`` in the span
    symbol: str  # Tiingo symbol
    start: str  # 2019-02-01, or the first membership day if later
    end: str  # last membership day, inclusive
    names: tuple[str, ...]  # accepted Tiingo metadata names (case-fold substrings)
    notes: tuple[str, ...] = ()
    known_jumps: tuple[tuple[str, str, str], ...] = ()  # (date, cause_type, label)


CALIBRATION_LINES: tuple[CalibrationLine, ...] = (
    CalibrationLine(
        "ADS",
        "0001101215",
        "BFH",
        "2019-02-01",
        "2020-06-21",
        ("bread financial", "alliance data"),
        ("CIK 1101215 renamed Bread Financial in 2022; Tiingo carries the same listing as BFH.",),
        (
            ("2020-03-16", MARKET_WIDE, COVID),
            ("2020-03-18", MARKET_WIDE, COVID),
            ("2020-03-24", MARKET_WIDE, COVID),
        ),
    ),
    CalibrationLine(
        "AGN",
        "0001578845",
        "AGN",
        "2019-02-01",
        "2020-05-11",
        ("allergan",),
        (),
        (("2019-06-25", CORPORATE_EVENT, "AbbVie-Allergan deal announced"),),
    ),
    CalibrationLine(
        "ALXN",
        "0000899866",
        "ALXN",
        "2019-02-01",
        "2021-07-20",
        ("alexion",),
        (),
        (
            (
                "2020-12-14",
                CORPORATE_EVENT,
                "AstraZeneca-Alexion deal announced (Saturday 2020-12-12)",
            ),
        ),
    ),
    CalibrationLine(
        "APC",
        "0000773910",
        "APC",
        "2019-02-01",
        "2019-08-08",
        ("anadarko",),
        (
            "APC is now ARKO Petroleum Corp (2026+); the old Anadarko NYSE segment "
            "(1986-09-09..2019-08-08) is listed in Tiingo's supported tickers but the "
            "keyed API serves only the current owner, so it is not delivered.",
        ),
    ),
    CalibrationLine(
        "ARNC",
        "0000004281",
        "HWM",
        "2019-02-01",
        "2020-03-31",
        ("howmet", "arconic"),
        (
            "CIK 4281 (Alcoa Inc. -> Arconic Inc. -> Howmet Aerospace) is the span's entity; "
            "Tiingo's ARNC is the 2020 Arconic Corp spinoff, so the old series is HWM.",
        ),
        (("2020-03-24", MARKET_WIDE, COVID),),
    ),
    CalibrationLine(
        "BMS",
        "0000011199",
        "BMS",
        "2019-06-07",
        "2019-06-10",
        ("bemis", "amcor flexibles"),
    ),
    CalibrationLine("CELG", "0000816284", "CELG", "2019-02-01", "2019-11-20", ("celgene",)),
    CalibrationLine("CXO", "0001358071", "CXO", "2019-02-01", "2021-01-20", ("concho",)),
    CalibrationLine(
        "DWDP",
        "0001666700",
        "DD",
        "2019-02-01",
        "2019-06-02",
        ("dupont", "dowdupont"),
        (
            "CIK 1666700 renamed DuPont de Nemours; ticker DD since 2019-06-03, so the "
            "DowDuPont segment is served under DD.",
        ),
        (("2019-04-02", CORPORATE_EVENT, "Dow Inc. distribution (ex-date 2019-04-01)"),),
    ),
    CalibrationLine(
        "ETFC",
        "0001015780",
        "ETFC",
        "2019-02-01",
        "2020-10-06",
        ("e*trade", "e trade", "etrade"),
    ),
    CalibrationLine("FL", "0000850209", "FL", "2019-02-01", "2019-08-08", ("foot locker",)),
    CalibrationLine("FLIR", "0000354908", "FLIR", "2019-02-01", "2021-05-13", ("flir",)),
    CalibrationLine(
        "FOX",
        "0001308161",
        "TFCF",
        "2019-02-01",
        "2019-03-18",
        ("twenty-first century fox", "twenty first century fox", "twentyfirst century fox", "tfcf"),
        (
            "Tiingo reassigned FOX to Fox Corp (CIK 1754301) from 2019-03-13; 21CF Class B's "
            "series is TFCF.",
        ),
    ),
    CalibrationLine(
        "FOXA",
        "0001308161",
        "TFCFA",
        "2019-02-01",
        "2019-03-18",
        ("twenty-first century fox", "twenty first century fox", "twentyfirst century fox", "tfcf"),
        (
            "Tiingo reassigned FOXA to Fox Corp (CIK 1754301) from 2019-03-12; 21CF Class A's "
            "series is TFCFA.",
        ),
    ),
    CalibrationLine(
        "HBI", "0001359841", "HBI", "2019-02-01", "2021-12-19", ("hanesbrands", "hanes brands")
    ),
    CalibrationLine(
        "HFC",
        "0000048039",
        "DINO",
        "2019-02-01",
        "2021-06-03",
        ("hf sinclair", "hollyfrontier", "holly front"),
        (
            "CIK 48039 (HollyFrontier) is the span's entity; the 2022 reorg moved the listing to "
            "HF Sinclair Corp (DINO, CIK 1915657).",
        ),
        (("2020-11-09", MARKET_WIDE, VACCINE),),
    ),
    CalibrationLine("JWN", "0000072333", "JWN", "2019-02-01", "2020-06-21", ("nordstrom",)),
    CalibrationLine(
        "KSU", "0000054480", "KSU", "2019-02-01", "2021-12-13", ("kansas city southern",)
    ),
    CalibrationLine("LEG", "0000058492", "LEG", "2019-02-01", "2021-12-19", ("leggett",)),
    CalibrationLine(
        "LLL",
        "0001039101",
        "LLL",
        "2019-02-01",
        "2019-06-30",
        ("l3 technologies", "l-3 technologies"),
    ),
    CalibrationLine(
        "MXIM", "0000743316", "MXIM", "2019-02-01", "2021-08-29", ("maxim integrated", "maxim")
    ),
    CalibrationLine(
        "NBL",
        "0000072207",
        "NBL",
        "2019-02-01",
        "2020-10-11",
        ("noble energy", "noble"),
        (),
        (
            ("2020-03-09", MARKET_WIDE, COVID),
            ("2020-03-16", MARKET_WIDE, COVID),
            ("2020-03-18", MARKET_WIDE, COVID),
            ("2020-03-19", MARKET_WIDE, COVID),
            ("2020-03-24", MARKET_WIDE, COVID),
        ),
    ),
    CalibrationLine(
        "NFX",
        "0000912750",
        "NFX",
        "2019-02-01",
        "2019-02-14",
        ("newfield",),
        (
            "NFX is now a 2026 BATS ETF; the old Newfield NYSE segment "
            "(1993-11-12..2019-02-14) is listed in Tiingo's supported tickers but the "
            "keyed API serves only the current owner, so it is not delivered.",
        ),
    ),
    CalibrationLine("RHT", "0001087423", "RHT", "2019-02-01", "2019-07-14", ("red hat",)),
    CalibrationLine(
        "RTN",
        "0001047122",
        "RTN",
        "2019-02-01",
        "2020-04-05",
        ("raytheon",),
        (
            "Tiingo still holds the old Raytheon Company series to 2020-04-03; RTX is UTC's history.",
        ),
    ),
    CalibrationLine(
        "STI",
        "0000750556",
        "STI",
        "2019-02-01",
        "2019-12-08",
        ("suntrust", "sun trust"),
        (
            "STI is now Solidion Technology (2024+); the old SunTrust NYSE segment "
            "(1987-12-30..2019-12-06) is listed in Tiingo's supported tickers but the "
            "keyed API serves only the current owner, so it is not delivered.",
        ),
    ),
    CalibrationLine(
        "TIF",
        "0000098246",
        "TIF",
        "2019-02-01",
        "2021-01-06",
        ("tiffany",),
        (),
        (("2019-10-28", CORPORATE_EVENT, "LVMH-Tiffany talks confirmed"),),
    ),
    CalibrationLine(
        "TSS",
        "0000721683",
        "TSS",
        "2019-02-01",
        "2019-09-22",
        ("total system services", "tsys"),
    ),
    CalibrationLine(
        "VAR", "0000203527", "VAR", "2019-02-01", "2021-04-19", ("varian medical", "varian")
    ),
    CalibrationLine("VIAB", "0001339947", "VIAB", "2019-02-01", "2019-12-04", ("viacom",)),
    CalibrationLine(
        "WCG", "0001279363", "WCG", "2019-02-01", "2020-01-27", ("wellcare", "well care")
    ),
    CalibrationLine("XEC", "0001168054", "XEC", "2019-02-01", "2020-03-01", ("cimarex",)),
)

#: Lines whose gateway candles are Fox Corporation's first sessions, not the
#: line's own (reviewer B, 2026-10-29). Any count is recorded, not a stop.
GATEWAY_REUSED = {"FOX", "FOXA"}

#: Lines whose metadata endpoint resolves the reused ticker to a 2026 ETF while
#: the price endpoint still returns the old company's series (reviewer B,
#: 2026-10-29). Accepted only under the date/count override below; never a proxy.
SHADOWED_METADATA: dict[str, dict[str, str]] = {
    "LLL": {
        "last": "2019-06-28",
        "supported_tickers_range": "1998-05-19..2019-06-28",
        "reason": (
            "L3 Technologies' series: it ends on 2019-06-28, the session before the "
            "L3Harris merger closed on 2019-06-29; the 2026 GraniteShares ETF cannot "
            "have 2019 rows."
        ),
    },
    "TSS": {
        "last": "2019-09-17",
        "supported_tickers_range": "1989-06-30..2019-09-17",
        "reason": (
            "Total System Services' series: it ends on 2019-09-17, the Global Payments "
            "merger close; the 2026 GraniteShares ETF cannot have 2019 rows."
        ),
    },
}
OVERRIDE_RULE = (
    "override: identity from dates and counts; metadata names the reused ticker's current owner"
)


# -- small helpers ---------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(value: str) -> date:
    return date.fromisoformat(value[:10])


def _days(start: str, end: str) -> list[date]:
    first, last = _day(start), _day(end)
    out: list[date] = []
    current = first
    while current <= last:
        out.append(current)
        current = date.fromordinal(current.toordinal() + 1)
    return out


def expected_days(line: CalibrationLine) -> list[str]:
    return [d.isoformat() for d in _days(line.start, line.end) if is_trading_day(d)]


def last_session(end: str) -> str:
    day = _day(end)
    while not is_trading_day(day):
        day = date.fromordinal(day.toordinal() - 1)
    return day.isoformat()


def _filesystem_type(path: Path) -> str:
    result = subprocess.run(  # noqa: S603 -- fixed, trusted argv
        ["stat", "-f", "-c", "%T", str(path)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or "unknown"


def _effective_modes(path: Path) -> str:
    return oct(stat.S_IMODE(path.stat().st_mode))


def _write_private(path: Path, text: str) -> None:
    path.write_text(text)
    os.chmod(path, 0o600)


def _cache_path(cache: Path, kind: str, symbol: str) -> Path:
    return cache / f"{kind}_{symbol}.json"


def _load_cache(cache: Path, kind: str, symbol: str) -> Any:
    path = _cache_path(cache, kind, symbol)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _save_cache(cache: Path, kind: str, symbol: str, value: Any) -> None:
    _write_private(_cache_path(cache, kind, symbol), json.dumps(value))


# -- membership and the environment ---------------------------------------


def _validate_lines(
    lines: tuple[CalibrationLine, ...], spans: dict[str, list[dict[str, str]]]
) -> dict[str, dict[str, str]]:
    """Stop if a line's membership span changed since the brief was written."""
    picked: dict[str, dict[str, str]] = {}
    for line in lines:
        stints = spans.get(line.ask)
        if not stints:
            raise LeaverPricesError(f"{line.ask}: no membership stint in {UNIVERSE_GICS_DB}")
        latest = max(stint["valid_to"] for stint in stints)
        if latest != line.end:
            raise LeaverPricesError(
                f"{line.ask}: latest valid_to is {latest!r}, expected {line.end!r} -- stop and ask"
            )
        # The stint that ends with the line's exit: the latest valid_from among
        # those (MXIM has an older 1976..2007 stint; BMS a 1976..2014 one).
        ending = [s for s in stints if s["valid_to"] == line.end]
        stint = max(ending, key=lambda s: s["valid_from"])
        derived_start = max(CALIBRATION_START, stint["valid_from"])
        if derived_start != line.start:
            raise LeaverPricesError(
                f"{line.ask}: membership start is {derived_start!r}, expected {line.start!r}"
                " -- stop and ask"
            )
        picked[line.ask] = stint
    return picked


def _prepare(
    *,
    output_dir: Path,
    financial_db: Path,
    universe_db: Path,
    brief_path: Path,
    lines: tuple[CalibrationLine, ...],
) -> dict[str, dict[str, str]]:
    machine_check(financial_db, brief_path)
    assert_not_in_git_worktree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output_dir, 0o700)
    spans = read_membership_spans([line.ask for line in lines], universe_db)
    return _validate_lines(lines, spans)


# -- HTTP ------------------------------------------------------------------


def _tiingo_client(key: str | None) -> httpx.Client:
    if not key:
        raise LeaverPricesError("TIINGO_API_KEY is not set")
    return httpx.Client(headers={"Authorization": f"Token {key}"}, timeout=60.0)


def _fetch_prices(
    client: httpx.Client, limiter: RateLimiter, symbol: str, start: str, end: str
) -> list[dict[str, Any]]:
    limiter.check()
    response = client.get(
        TIINGO_PRICES_URL.format(symbol=symbol),
        params={"startDate": start, "endDate": end, "format": "json"},
    )
    response.raise_for_status()
    return cast("list[dict[str, Any]]", response.json())


def _fetch_meta(client: httpx.Client, limiter: RateLimiter, symbol: str) -> dict[str, Any]:
    limiter.check()
    response = client.get(TIINGO_META_URL.format(symbol=symbol))
    response.raise_for_status()
    return cast("dict[str, Any]", response.json())


# -- EDGAR verification ----------------------------------------------------


@dataclass
class SecVerifier:
    """Read-only EDGAR verification: the entity's name and an in-span cover page.

    Only the accession/form/date and a boolean are kept; no SEC document is
    stored, in the manifest or anywhere else.
    """

    client: httpx.Client
    pace_seconds: float = SEC_PACE_SECONDS
    requests: int = 0

    def get(self, url: str) -> httpx.Response:
        time.sleep(self.pace_seconds)
        self.requests += 1
        response = self.client.get(url)
        response.raise_for_status()
        return response


def _cover_window(text: str) -> str | None:
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = html.unescape(plain)
    plain = re.sub(r"\s+", " ", plain)
    index = plain.casefold().find("trading symbol")
    if index < 0:
        return None
    return plain[index : index + 900]


def _symbol_on_cover(text: str, ask: str) -> bool | None:
    window = _cover_window(text)
    if window is None:
        return None
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(ask)}(?![A-Za-z0-9])", window))


def _pick_filing(recent: dict[str, Any], start: str, end: str) -> dict[str, str] | None:
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    documents = recent.get("primaryDocument", [])
    rows = [
        {"form": f, "filed": d, "accession": a, "document": doc}
        for f, d, a, doc in zip(forms, dates, accessions, documents, strict=False)
        if f in COVER_FORMS
    ]
    in_span = [r for r in rows if start <= r["filed"] <= end]
    pool = in_span or [r for r in rows if r["filed"] <= end]
    if not pool:
        return None
    return max(pool, key=lambda r: (_COVER_FORM_RANK.get(r["form"], 0), r["filed"]))


def verify_cik(verifier: SecVerifier, line: CalibrationLine) -> dict[str, Any]:
    """Name + in-span cover page for one line, read-only."""
    submissions = verifier.get(SEC_SUBMISSIONS_URL.format(cik=int(line.cik))).json()
    name = str(submissions.get("name") or "")
    former = [str(item.get("name", "")) for item in submissions.get("formerNames", [])]
    tickers = [str(t) for t in submissions.get("tickers", [])]
    recent = submissions.get("filings", {}).get("recent", {})
    filing = _pick_filing(recent, line.start, line.end)
    symbol_on_cover: bool | None = None
    cover_error: str | None = None
    if filing and filing.get("document"):
        url = SEC_ARCHIVE_URL.format(
            cik=int(line.cik),
            accession=filing["accession"].replace("-", ""),
            document=filing["document"],
        )
        try:
            symbol_on_cover = _symbol_on_cover(verifier.get(url).text, line.ask)
        except httpx.HTTPError as error:
            cover_error = f"{type(error).__name__}"
    return {
        "method": "EDGAR submissions JSON (name, former names, tickers) + in-span cover page",
        "sec_name": name,
        "former_names": former,
        "submissions_tickers": tickers,
        "filing": filing,
        "filing_in_span": bool(filing and line.start <= filing["filed"] <= line.end),
        "ask_symbol_on_cover": symbol_on_cover,
        "cover_error": cover_error,
    }


# -- checks ----------------------------------------------------------------


def _assert_on_calendar(line: CalibrationLine, raw: list[dict[str, Any]]) -> None:
    for row in raw:
        day = str(row["date"])[:10]
        if not is_trading_day(_day(day)):
            raise LeaverPricesError(
                f"{line.ask}: {day} is not an NYSE session -- date off the calendar; stop and ask"
            )


def _identity(line: CalibrationLine, meta: dict[str, Any]) -> None:
    name = str(meta.get("name") or "")
    folded = name.casefold()
    if not any(pattern in folded for pattern in line.names):
        raise LeaverPricesError(
            f"{line.ask}: Tiingo metadata names {name!r}, expected one of {line.names}"
            " -- possibly the symbol's new owner; stop and ask"
        )


def _identity_override(
    line: CalibrationLine,
    raw: list[dict[str, Any]],
    in_range: list[dict[str, Any]],
    meta: dict[str, Any],
) -> dict[str, Any] | None:
    """The documented LLL/TSS override: dates + counts, never a name or a proxy.

    Accepted only if the in-span rows start on the line's first session, end on
    the documented last trading date, their count equals the calendar's sessions
    over that span, and no returned row falls outside the span.
    """
    spec = SHADOWED_METADATA.get(line.ask)
    if spec is None:
        return None
    expected = expected_days(line)
    first = expected[0]
    span = [day for day in expected if day <= spec["last"]]
    if len(raw) != len(in_range):
        raise LeaverPricesError(
            f"{line.ask}: rows outside {line.start}..{line.end} -- identity override fails; "
            "stop and ask"
        )
    first_row = str(in_range[0]["date"])[:10]
    last_row = str(in_range[-1]["date"])[:10]
    if first_row != first:
        raise LeaverPricesError(
            f"{line.ask}: override series starts {first_row}, expected {first} -- stop and ask"
        )
    if last_row != spec["last"]:
        raise LeaverPricesError(
            f"{line.ask}: override series ends {last_row}, expected {spec['last']} -- stop and ask"
        )
    if len(in_range) != len(span):
        raise LeaverPricesError(
            f"{line.ask}: override series has {len(in_range)} rows, the calendar gives "
            f"{len(span)} for {first}..{spec['last']} -- stop and ask"
        )
    return {
        "rule": OVERRIDE_RULE,
        "tiingo_name": str(meta.get("name") or ""),
        "tiingo_metadata": {
            "start_date": _meta_date(meta, "startDate"),
            "end_date": _meta_date(meta, "endDate"),
        },
        "supported_tickers_range": spec["supported_tickers_range"],
        "reason": spec["reason"],
        "first_date": first,
        "last_date": spec["last"],
        "rows": len(in_range),
    }


def _meta_date(meta: dict[str, Any], key: str) -> str | None:
    value = meta.get(key)
    if not value:
        return None
    return str(value)[:10]


def _check_fox_classes(metas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """FOX/FOXA must be two distinguishable series (reviewer B)."""
    b = metas["FOX"]
    a = metas["FOXA"]
    b_name = str(b.get("name") or "")
    a_name = str(a.get("name") or "")
    b_start = _meta_date(b, "startDate")
    a_start = _meta_date(a, "startDate")
    if b_name == a_name and b_start == a_start:
        raise LeaverPricesError(
            "FOX/FOXA: Tiingo's metadata cannot tell the two classes apart "
            f"(both {b_name!r}, start {b_start!r}) -- stop and ask"
        )
    return {
        "TFCF": {"symbol": b.get("ticker", "TFCF"), "name": b_name, "start_date": b_start},
        "TFCFA": {"symbol": a.get("ticker", "TFCFA"), "name": a_name, "start_date": a_start},
        "reason": "distinct symbols and listing starts (Class B 1987, Class A 1996); "
        "the 'A' suffix marks Class A",
    }


@dataclass
class LineResult:
    line: CalibrationLine
    meta: dict[str, Any]
    rows: list[dict[str, Any]]
    trimmed: dict[str, str]
    expected: list[str]
    missing: list[str]
    last_equals_exit: bool
    jumps: list[dict[str, Any]]
    split_events: list[dict[str, Any]]
    identity_override: dict[str, Any] | None
    cik: dict[str, Any]
    gateway: dict[str, Any]
    production: int


def _scan_jumps(
    kept: list[dict[str, Any]],
    known: dict[str, tuple[str, str]],
    spy_returns: dict[str, float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sessions over ``JUMP_THRESHOLD``: (known, unexplained).

    A known cause is labelled by kind (``corporate event`` or ``market-wide
    move``); a market-wide move also carries SPY's same-day return.
    """
    jumps: list[dict[str, Any]] = []
    unexplained: list[dict[str, Any]] = []
    for previous, current in pairwise(kept):
        if float(previous["adj_close"]) == 0:
            continue
        change = float(current["adj_close"]) / float(previous["adj_close"]) - 1
        if abs(change) <= JUMP_THRESHOLD:
            continue
        entry: dict[str, Any] = {
            "date": current["date"],
            "abs_return": round(abs(change), 6),
        }
        spec = known.get(current["date"])
        if spec:
            cause_type, label = spec
            entry["cause_type"] = cause_type
            entry["cause"] = label
            if cause_type == MARKET_WIDE:
                spy = spy_returns.get(current["date"])
                entry["spy_same_day_return"] = round(spy, 6) if spy is not None else None
            jumps.append(entry)
        else:
            unexplained.append(entry)
    return jumps, unexplained


def _split_events(line: CalibrationLine, kept: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every in-span row with ``splitFactor != 1``, dated and classified."""
    events: list[dict[str, Any]] = []
    for row in kept:
        factor = float(row["splitFactor"] or 1.0)
        if factor in (0.0, 1.0):
            continue
        label = KNOWN_DISTRIBUTIONS.get((line.ask, str(row["date"])))
        if label is None:
            label = (
                "stock split"
                if round(factor, 6) in _SIMPLE_SPLIT_FACTORS
                else "corporate action encoded as a split factor"
            )
        events.append({"date": row["date"], "factor": factor, "kind": label})
    return events


def _build_line(
    line: CalibrationLine,
    raw: list[dict[str, Any]],
    meta: dict[str, Any],
    cik: dict[str, Any],
    gateway: dict[str, Any],
    production: int,
    spy_returns: dict[str, float] | None = None,
) -> LineResult | None:
    """Apply the trims, the split formula and the reports checks to one line.

    Returns ``None`` for a line Tiingo does not carry over the span (reported
    in ``not_delivered``), never a proxy.
    """
    meta_start = _meta_date(meta, "startDate")
    meta_end = _meta_date(meta, "endDate")
    end_session = last_session(line.end)
    in_range = [r for r in raw if line.start <= str(r["date"])[:10] <= line.end]
    if not in_range:
        # The symbol is reused by its current owner (APC/STI/NFX): the keyed
        # API serves no rows for the old span. Report, never proxy.
        return None
    override = _identity_override(line, raw, in_range, meta)
    if override is None:
        _identity(line, meta)

    # Reviewer B: accept DD for DWDP only if its metadata names DowDuPont/
    # DuPont and its dates cover 2019-02-01..2019-05-31; otherwise report it
    # missing (no proxy).
    if line.ask == "DWDP" and (
        not meta_start or not meta_end or meta_start > line.start or meta_end < end_session
    ):
        return None

    _assert_on_calendar(line, raw)
    series = build_series(line.symbol, raw, line.end, line.start)
    if not series:
        return None
    detected = detect_non_real(line.symbol, in_range)
    kept = [r for r in series if r["date"] not in detected]
    if not kept:
        return None

    factors = split_cum_factors(raw)
    for row in kept:
        row["split_cum"] = factors[row["date"]]

    known = {day: (kind, label) for day, kind, label in line.known_jumps}
    jumps, unexplained = _scan_jumps(kept, known, spy_returns or {})
    if unexplained:
        raise LeaverPricesError(
            f"{line.ask}: unexplained adjusted close-to-close jump(s) over 25%: "
            f"{json.dumps(unexplained)} -- stop and ask"
        )

    expected = expected_days(line)
    kept_dates = {str(row["date"]) for row in kept}
    missing = [day for day in expected if day not in kept_dates]
    last_equals_exit = bool(kept) and kept[-1]["date"] == end_session
    return LineResult(
        line=line,
        meta=meta,
        rows=kept,
        trimmed=detected,
        expected=expected,
        missing=missing,
        last_equals_exit=last_equals_exit,
        jumps=jumps,
        split_events=_split_events(line, kept),
        identity_override=override,
        cik=cik,
        gateway=gateway,
        production=production,
    )


def _missing_reason(result: LineResult) -> str:
    if not result.missing:
        return ""
    last = str(result.rows[-1]["date"]) if result.rows else ""
    tail = [d for d in result.missing if d > last]
    inside = [d for d in result.missing if d <= last]
    parts: list[str] = []
    if tail:
        parts.append(
            f"trading ended {last}, before the index exit {result.line.end} "
            f"({len(tail)} session(s): {tail[0]}..{tail[-1]})"
        )
    if inside:
        parts.append(f"no Tiingo row for {len(inside)} session(s) inside the span: {inside}")
    return "; ".join(parts)


def _exit_reason(result: LineResult) -> str:
    if result.last_equals_exit:
        return ""
    if not result.rows:
        return "no Tiingo rows in the span"
    last = str(result.rows[-1]["date"])
    if result.trimmed:
        trimmed_days = sorted(result.trimmed)
        if trimmed_days and trimmed_days[-1] > last:
            return (
                f"the last session(s) {trimmed_days[-1]} are "
                f"{result.trimmed[trimmed_days[-1]]} rows (no exchange trade), "
                f"before the index exit {result.line.end}"
            )
    return f"trading ended {last}, before the index exit {result.line.end}"


# -- phase A ---------------------------------------------------------------


def fetch_calibration_phase_a(
    *,
    output_dir: Path = CALIBRATION_OUTPUT_DIR,
    tiingo_client: httpx.Client | None = None,
    financial_db: Path = FINANCIAL_DB,
    universe_db: Path = UNIVERSE_GICS_DB,
    brief_path: Path = BRIEF_PATH,
    lines: tuple[CalibrationLine, ...] = CALIBRATION_LINES,
    pace_seconds: float = PACE_SECONDS,
    batch_size: int = 49,
) -> dict[str, Any]:
    """Fetch the price series and as much metadata as the hour allows (49)."""
    _prepare(
        output_dir=output_dir,
        financial_db=financial_db,
        universe_db=universe_db,
        brief_path=brief_path,
        lines=lines,
    )
    cache = output_dir / CACHE_DIRNAME
    cache.mkdir(exist_ok=True, mode=0o700)
    limiter = RateLimiter(path=output_dir / REQUEST_LOG_FILENAME)
    started_path = cache / "started.json"
    if not started_path.exists():
        _write_private(
            started_path,
            json.dumps({"fetch_time_utc": _utc_now(), "requests_before": limiter.total_all_runs()}),
        )
    owns = tiingo_client is None
    client = tiingo_client or _tiingo_client(os.environ.get("TIINGO_API_KEY"))
    prices = 0
    metadata = 0
    try:
        for line in lines:
            if _load_cache(cache, "prices", line.symbol) is None:
                raw = _fetch_prices(client, limiter, line.symbol, line.start, line.end)
                _assert_on_calendar(line, raw)
                _save_cache(cache, "prices", line.symbol, raw)
                prices += 1
                if pace_seconds:
                    time.sleep(pace_seconds)
        for line in lines:
            if limiter.rolling_count() >= batch_size:
                break
            if _load_cache(cache, "meta", line.symbol) is None:
                _save_cache(cache, "meta", line.symbol, _fetch_meta(client, limiter, line.symbol))
                metadata += 1
                if pace_seconds:
                    time.sleep(pace_seconds)
    finally:
        if owns:
            client.close()
    return {
        "phase": "a",
        "output_dir": str(output_dir),
        "prices_fetched": prices,
        "metadata_fetched": metadata,
        "requests_this_run": limiter.total_all_runs(),
        "rolling_hour_now": limiter.rolling_count(),
    }


# -- phase B ---------------------------------------------------------------


def _gateway_count(client: httpx.Client | None, ask: str, start: str, end: str) -> dict[str, Any]:
    if client is None:
        return {"count": None, "note": "gateway not measured"}
    try:
        response = client.get(
            f"{GATEWAY_BASE}/{ask}", params={"start_date": start, "end_date": end}
        )
        response.raise_for_status()
        count = len(cast("list[dict[str, Any]]", response.json().get("candles", [])))
    except httpx.HTTPError as error:
        return {"count": None, "note": f"gateway not reachable ({type(error).__name__})"}
    note = ""
    if ask in GATEWAY_REUSED and count:
        note = (
            "Fox Corporation's first sessions (from 2019-03-12/13), another company; "
            "the line's entity is Twenty-First Century Fox, so these candles are not used"
        )
    return {"count": count, "note": note}


def _spy_returns(client: httpx.Client | None, start: str, end: str) -> dict[str, float]:
    """SPY's close-to-close returns from the shared gateway (read-only, local).

    Used only to corroborate a market-wide jump; never a data source for the
    file and never a Tiingo request.
    """
    if client is None:
        return {}
    try:
        response = client.get(f"{GATEWAY_BASE}/SPY", params={"start_date": start, "end_date": end})
        response.raise_for_status()
        candles = cast("list[dict[str, Any]]", response.json().get("candles", []))
    except httpx.HTTPError:
        return {}
    returns: dict[str, float] = {}
    for previous, current in pairwise(candles):
        close = float(previous.get("close") or 0.0)
        if close:
            returns[str(current["date"])[:10]] = round(float(current["close"]) / close - 1, 6)
    return returns


def _probe_summary(probes: dict[str, Any] | None) -> dict[str, Any] | None:
    """The probe record with a request count, derived if the file has none."""
    if not probes:
        return None
    count = int(
        probes.get("spent")
        or len(probes.get("searches") or {})
        + len(probes.get("metadata") or [])
        + (1 if probes.get("apc_probe") else 0)
    )
    return {
        **probes,
        "spent": count,
        "note": (
            "reviewer B option B: 3 entity searches + candidate metadata probes + "
            "the APC startDate-only probe; counts and dates only, no price"
        ),
    }


def _write_outputs(
    output_dir: Path,
    results: list[LineResult],
    not_delivered: list[dict[str, Any]],
    *,
    started: dict[str, Any],
    limiter: RateLimiter,
    fox_classes: dict[str, Any] | None,
    sha1_before: dict[str, str],
    sha1_after: dict[str, str],
    sec_requests: int,
    probes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data_path = output_dir / DATA_FILENAME
    probes = _probe_summary(probes)
    covered = sum(1 for result in results if result.cik.get("ask_symbol_on_cover") is True)
    cik_note = (
        f"{covered} of {len(results)} in-span cover pages show the ticker in a 'Trading Symbol' "
        "column; the rest are 2019 filings whose cover format has no ticker column. The "
        "submissions name/former names and the in-span filing verify the entity; the price "
        "series' dates corroborate the symbol."
    )

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
                "split_cum",
            ]
        )
        for result in results:
            for row in result.rows:
                writer.writerow(
                    [
                        result.line.ask,
                        result.line.cik,
                        result.line.symbol,
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
                        row["split_cum"],
                    ]
                )
    os.chmod(data_path, 0o600)

    manifest: dict[str, Any] = {
        "fetch_time_utc": started.get("fetch_time_utc", _utc_now()),
        "formula": (
            "split_cum(d) = product of splitFactor(s) for split ex-dates s > d; "
            "adj_price = raw_price / split_cum; adj_volume = raw_volume * split_cum; "
            "dividends are NOT removed (never Tiingo's own adjClose). "
            "split_cum is written per row."
        ),
        "dividend_formula": (
            f"{DIVIDEND_FORMULA} (the 2026-10-15 AAPL control's formula); "
            "prices.csv keeps raw divCash and split-adjusted prices"
        ),
        "adjusted_note": (
            "split_cum includes the splitFactor rows listed in checks.split_events. "
            "A distribution Tiingo encodes as a split factor (DWDP 2019-04-02, the "
            "Dow Inc. distribution) is therefore removed from the adjusted series: "
            "that line's returns exclude the spin-off."
        ),
        "trim_rule": (
            "a row is real only if it is an exchange trade: carried = close repeats the "
            "previous session's with volume 0 (or below 0.10x the line's median); halt/otc = "
            "the OTC delisting rule (no line here uses an OTC symbol). A detected row is "
            "trimmed and recorded by date and reason, never filled."
        ),
        "data_file": DATA_FILENAME,
        "data_file_sha256": sha256_file(data_path),
        "request_count": {
            "this_run": limiter.total_all_runs() - int(started.get("requests_before", 0)),
            "total_all_runs": limiter.total_all_runs(),
            "rolling_hour_now": limiter.rolling_count(),
            "largest_rolling_hour": limiter.largest_rolling_hour(),
            "cap_per_hour": limiter.max_per_hour,
            "probe_requests": int((probes or {}).get("spent", 0)),
            "log_file": REQUEST_LOG_FILENAME,
        },
        "sec_request_count": sec_requests,
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
            "raw_cache_removed": not (output_dir / CACHE_DIRNAME).exists(),
            "note": (
                "chmod is a no-op on this filesystem; effective modes are recorded, not enforced."
            ),
        },
        "machine": {
            "financial_db": str(FINANCIAL_DB),
            "financial_db_sha1": FINANCIAL_DB_SHA1,
        },
        "production_db_sha1_before": sha1_before,
        "production_db_sha1_after": sha1_after,
        "checks": {
            "membership": "every line's latest valid_to and derived start match the brief",
            "cik": {
                "method": "EDGAR submissions JSON + in-span cover page; no document stored",
                "covers_with_ticker": covered,
                "note": cik_note,
            },
            "fox_classes": fox_classes,
            "probes": probes,
            "jumps": [
                {"ask": result.line.ask, **jump} for result in results for jump in result.jumps
            ],
            "gateway": [{"ask": result.line.ask, **result.gateway} for result in results],
            "split_events": [
                {"ask": result.line.ask, **event}
                for result in results
                for event in result.split_events
            ],
            "identity_overrides": [
                {"ask": result.line.ask, **result.identity_override}
                for result in results
                if result.identity_override
            ],
        },
        "lines": [
            {
                "ask": result.line.ask,
                "cik": result.line.cik,
                "cik_verified": result.cik,
                "tiingo_symbol": result.line.symbol,
                "tiingo_name": str(result.meta.get("name") or ""),
                "tiingo_metadata": {
                    "start_date": _meta_date(result.meta, "startDate"),
                    "end_date": _meta_date(result.meta, "endDate"),
                    "exchange_code": result.meta.get("exchangeCode"),
                },
                "rows_kept": len(result.rows),
                "expected_nyse_sessions": len(result.expected),
                "missing_sessions": result.missing,
                "missing_reason": _missing_reason(result),
                "trimmed_rows": [
                    {"date": day, "reason": reason}
                    for day, reason in sorted(result.trimmed.items())
                ],
                "first_date": result.rows[0]["date"],
                "last_date": result.rows[-1]["date"],
                "last_equals_exit": result.last_equals_exit,
                "exit_reason": _exit_reason(result),
                "membership_end": result.line.end,
                "dividend_rows": sum(
                    1 for row in result.rows if float(row["divCash"] or 0.0) != 0.0
                ),
                "split_rows": sum(
                    1 for row in result.rows if float(row["splitFactor"] or 1.0) not in (0.0, 1.0)
                ),
                "split_dates": [
                    row["date"]
                    for row in result.rows
                    if float(row["splitFactor"] or 1.0) not in (0.0, 1.0)
                ],
                "jumps": result.jumps,
                "identity_override": result.identity_override,
                "why_tiingo": {
                    "gateway_candle_count": result.gateway.get("count"),
                    "gateway_note": result.gateway.get("note", ""),
                    "production_stored_count": result.production,
                },
                "notes": list(result.line.notes),
            }
            for result in results
        ],
        "not_delivered": not_delivered,
        "deletion_step": _deletion_step(output_dir),
    }
    manifest_path = output_dir / MANIFEST_FILENAME
    _write_private(manifest_path, json.dumps(manifest, indent=2))
    return manifest


def _deletion_step(output_dir: Path) -> list[dict[str, str]]:
    path = output_dir.as_posix()
    return [
        {
            "copy": "this machine's calibration folder",
            "path": path,
            "command": f"rm -rf {path}",
            "check": f"test ! -e {path}",
        },
        {
            "copy": "T-100's machine, if different",
            "path": "<T-100 thesis folder>/data/private/tiingo/calibration_2019_2021",
            "command": "rm -rf <T-100 thesis folder>/data/private/tiingo/calibration_2019_2021",
            "check": "test ! -e <T-100 thesis folder>/data/private/tiingo/calibration_2019_2021",
        },
        {
            "copy": "every backup of thesis/data the user names",
            "path": "<backup thesis folder>/data/private/tiingo/calibration_2019_2021",
            "command": "rm -rf <backup thesis folder>/data/private/tiingo/calibration_2019_2021",
            "check": "test ! -e <backup thesis folder>/data/private/tiingo/calibration_2019_2021",
        },
    ]


def _load_metas(
    lines: tuple[CalibrationLine, ...],
    cache: Path,
    client: httpx.Client,
    limiter: RateLimiter,
    pace_seconds: float,
) -> dict[str, dict[str, Any]]:
    metas: dict[str, dict[str, Any]] = {}
    for line in lines:
        cached = _load_cache(cache, "meta", line.symbol)
        if cached is None:
            try:
                cached = _fetch_meta(client, limiter, line.symbol)
            except httpx.HTTPError as error:
                cached = {
                    "name": "",
                    "startDate": None,
                    "endDate": None,
                    "error": f"{type(error).__name__}",
                }
            _save_cache(cache, "meta", line.symbol, cached)
            if pace_seconds:
                time.sleep(pace_seconds)
        metas[line.symbol] = cached
    return metas


def _process_lines(
    lines: tuple[CalibrationLine, ...],
    cache: Path,
    metas: dict[str, dict[str, Any]],
    verifier: SecVerifier,
    gateway: httpx.Client,
    financial_db: Path,
    spy_returns: dict[str, float],
) -> tuple[list[LineResult], list[dict[str, Any]]]:
    results: list[LineResult] = []
    not_delivered: list[dict[str, Any]] = []
    for line in lines:
        raw = _load_cache(cache, "prices", line.symbol)
        if raw is None:
            raise LeaverPricesError(f"{line.ask}: no cached prices; run phase a again")
        cik = verify_cik(verifier, line)
        gateway_info = _gateway_count(gateway, line.ask, line.start, line.end)
        if line.ask not in GATEWAY_REUSED and gateway_info.get("count"):
            raise LeaverPricesError(
                f"{line.ask}: the gateway served {gateway_info['count']} candle(s) for a "
                "line that must be Tiingo-only -- stop and ask"
            )
        production = production_stored_count(line.cik, line.ask, financial_db)
        result = _build_line(
            line, raw, metas[line.symbol], cik, gateway_info, production, spy_returns
        )
        if result is None:
            meta = metas[line.symbol]
            name = str(meta.get("name") or "no metadata")
            reason = (
                f"no Tiingo rows over {line.start}..{line.end}; the keyed API resolves "
                f"{line.symbol} to {name!r} (metadata "
                f"{_meta_date(meta, 'startDate')}..{_meta_date(meta, 'endDate')})"
            )
            if meta.get("error"):
                reason += f"; metadata request failed ({meta['error']})"
            not_delivered.append(
                {
                    "ask": line.ask,
                    "cik": line.cik,
                    "tiingo_symbol": line.symbol,
                    "tiingo_name": name,
                    "tiingo_metadata": {
                        "start_date": _meta_date(meta, "startDate"),
                        "end_date": _meta_date(meta, "endDate"),
                    },
                    "expected_nyse_sessions": len(expected_days(line)),
                    "sessions_covered": 0,
                    "reason": reason,
                    "notes": list(line.notes),
                }
            )
        else:
            results.append(result)
    return results, not_delivered


def fetch_calibration_phase_b(
    *,
    output_dir: Path = CALIBRATION_OUTPUT_DIR,
    tiingo_client: httpx.Client | None = None,
    gateway_client: httpx.Client | None = None,
    sec_client: httpx.Client | None = None,
    financial_db: Path = FINANCIAL_DB,
    universe_db: Path = UNIVERSE_GICS_DB,
    brief_path: Path = BRIEF_PATH,
    production_dbs: tuple[Path, ...] | None = None,
    lines: tuple[CalibrationLine, ...] = CALIBRATION_LINES,
    pace_seconds: float = PACE_SECONDS,
) -> dict[str, Any]:
    """Fetch the remaining metadata, verify the CIKs, check and write."""
    production_dbs = production_dbs if production_dbs is not None else PRODUCTION_DBS
    _prepare(
        output_dir=output_dir,
        financial_db=financial_db,
        universe_db=universe_db,
        brief_path=brief_path,
        lines=lines,
    )
    cache = output_dir / CACHE_DIRNAME
    if not cache.exists():
        raise LeaverPricesError(f"no phase-a cache at {cache}; run phase a first")
    started_path = cache / "started.json"
    if not started_path.exists():
        raise LeaverPricesError(f"no phase-a start record at {started_path}; run phase a first")
    started = json.loads(started_path.read_text())
    probe_path = cache / "probe_results.json"
    probes = json.loads(probe_path.read_text()) if probe_path.exists() else None

    sha1_before = {str(p): sha1_file(p) for p in production_dbs if p.exists()}
    limiter = RateLimiter(path=output_dir / REQUEST_LOG_FILENAME)
    owns_tiingo = tiingo_client is None
    client = tiingo_client or _tiingo_client(os.environ.get("TIINGO_API_KEY"))
    owns_gateway = gateway_client is None
    gateway = gateway_client or httpx.Client(timeout=10.0)
    owns_sec = sec_client is None
    user_agent = f"{os.environ.get('NAME', '')} {os.environ.get('EMAIL', '')}".strip()
    if owns_sec and not user_agent:
        raise LeaverPricesError(
            "SEC verification needs $NAME and $EMAIL (SEC programmatic-access policy)"
        )
    sec = sec_client or httpx.Client(
        headers={"User-Agent": user_agent or "portfolio-data-mining"}, timeout=30.0
    )
    verifier = SecVerifier(sec)

    metas: dict[str, dict[str, Any]] = {}
    results: list[LineResult] = []
    not_delivered: list[dict[str, Any]] = []
    try:
        metas = _load_metas(lines, cache, client, limiter, pace_seconds)
        spy_returns = _spy_returns(gateway, CALIBRATION_START, "2021-12-31")
        results, not_delivered = _process_lines(
            lines, cache, metas, verifier, gateway, financial_db, spy_returns
        )

        fox_classes: dict[str, Any] | None = None
        if {"FOX", "FOXA"} <= set(metas):
            fox_classes = _check_fox_classes(metas)
            # The two classes must not be the same series.
            fox_close = {r.line.ask: [row["adj_close"] for row in r.rows] for r in results}
            if "FOX" in fox_close and fox_close.get("FOX") == fox_close.get("FOXA"):
                raise LeaverPricesError(
                    "FOX/FOXA: the two Tiingo series are identical -- cannot tell the "
                    "classes apart; stop and ask"
                )
    finally:
        if owns_tiingo:
            client.close()
        if owns_gateway:
            gateway.close()
        if owns_sec:
            sec.close()

    sha1_after = {str(p): sha1_file(p) for p in production_dbs if p.exists()}
    if sha1_before != sha1_after:
        raise LeaverPricesError("a production DB changed during the run")

    manifest = _write_outputs(
        output_dir,
        results,
        not_delivered,
        started=started,
        limiter=limiter,
        fox_classes=fox_classes,
        sha1_before=sha1_before,
        sha1_after=sha1_after,
        sec_requests=verifier.requests,
        probes=probes,
    )
    shutil.rmtree(cache)
    return manifest


__all__ = [
    "CALIBRATION_LINES",
    "CALIBRATION_OUTPUT_DIR",
    "DIVIDEND_FORMULA",
    "CalibrationLine",
    "LeaverPricesError",
    "expected_days",
    "fetch_calibration_phase_a",
    "fetch_calibration_phase_b",
    "last_session",
    "verify_cik",
]
