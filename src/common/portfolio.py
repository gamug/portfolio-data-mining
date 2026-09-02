"""
src/common/portfolio.py

Loads and queries the tracked S&P 500 ticker universe, fetched live from
Wikipedia's "List of S&P 500 companies" page and cached in-process for the
life of the process. Single source of truth for "what tickers/companies do
we track", used to list the universe and to resolve a ticker symbol or
company name to a canonical row.

Previously read a committed `s&p500/s&p500.csv` seeded into `input/` by a
now-removed `common.utils.init_repository()`; switched to a live fetch
(same approach as `news_collector.sp500` and `extractor.reference`, both
of which already source the S&P 500 constituent list from this same
Wikipedia page) so the tracked universe doesn't silently drift from
reality as index membership changes, and to drop a static CSV file this
repo had to remember to keep up to date.

Shared by the pricing and edgar modules (both need "what tickers do we
track"), which is why it lives in `common/` rather than either one.

Point-in-time membership (an `as_of` date, instead of just "today") is
handled by the sibling `common.universe_history` module, backed by a
dedicated `data/universe.db` -- deliberately not something this module
touches on the default path (as_of=None keeps the exact behavior below:
in-process cached live scrape, no DB, no filesystem write). See
`universe_history` for why, and CLAUDE.md/docs/modules/pricing.md for the
`as_of` API/CLI surface.
"""

import functools
import re
from datetime import date

import httpx
import pandas as pd
from bs4 import BeautifulSoup

WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
WIKIPEDIA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) portfolio-data-mining/1.0 "
        "(+https://github.com/gamug/portfolio-data-mining; research/dataset-building use)"
    )
}

COLUMNS = [
    "Symbol",
    "Security",
    "GICS Sector",
    "GICS Sub-Industry",
    "Headquarters Location",
    "Date added",
    "CIK",
    "Founded",
]


def _parse_constituents_table(page_html: str) -> pd.DataFrame:
    """Pure parse: Wikipedia constituents table HTML -> universe DataFrame.

    Uses cell position, not header text -- the header cells render with an
    embedded <br> and no space (e.g. "GICSSector"), so text-matching the
    header row is unreliable. Same approach as
    `extractor.reference.parse_gics_table`, extended to all 8 columns
    instead of just sector/sub-industry. Raises ValueError if the
    #constituents table isn't present (e.g. page structure changed) so
    callers can decide how to degrade, same convention as
    `news_collector.sp500`.
    """
    soup = BeautifulSoup(page_html, "html.parser")
    table = soup.find("table", {"id": "constituents"})
    if table is None:
        raise ValueError("constituents table not found in Wikipedia S&P 500 page")

    records = []
    for row in table.find_all("tr")[1:]:  # skip header row
        cells = row.find_all("td")
        if len(cells) < len(COLUMNS):
            continue
        # A bare "|" is table/template syntax leaking into the rendered
        # cell (observed live on the CIK column, e.g. "0000066740 |") --
        # never legitimate content in any of these 8 columns, so strip it
        # rather than trust the raw scrape.
        values = [
            re.sub(r"\s*\|\s*", "", c.get_text(strip=True)).strip() for c in cells[: len(COLUMNS)]
        ]
        if not values[0]:
            continue
        values[0] = values[0].upper()  # Symbol
        records.append(dict(zip(COLUMNS, values, strict=False)))

    if not records:
        raise ValueError(
            "Parsed 0 companies from the constituents table at "
            f"{WIKIPEDIA_SP500_URL} -- Wikipedia's page markup may have changed"
        )
    return pd.DataFrame.from_records(records, columns=COLUMNS)


def _fetch_universe_from_wikipedia(client: httpx.Client | None = None) -> pd.DataFrame:
    """Blocking GET + parse. `load_universe()`'s cache means this only ever
    runs once per process, so a synchronous client here -- rather than
    threading async through every caller (the /universe routes are sync
    FastAPI handlers) -- is a fine trade. Accepts an optional injected
    httpx.Client so tests can supply one wired to an httpx.MockTransport,
    matching the convention already used by
    news_collector.sp500/extractor.reference."""
    owns_client = client is None
    client = client or httpx.Client()
    try:
        response = client.get(
            WIKIPEDIA_SP500_URL, headers=WIKIPEDIA_HEADERS, follow_redirects=True, timeout=30.0
        )
        response.raise_for_status()
        return _parse_constituents_table(response.text)
    finally:
        if owns_client:
            client.close()


@functools.lru_cache(maxsize=1)
def load_universe() -> pd.DataFrame:
    """
    Load the tracked S&P 500 universe, fetched live from Wikipedia. Cached
    after the first call — call `load_universe.cache_clear()` to force a
    re-fetch (e.g. to pick up index changes without restarting the process).
    """
    return _fetch_universe_from_wikipedia()


def list_universe(sector: str | None = None, as_of: date | None = None) -> list[dict]:
    """Return the tracked universe as a list of row dicts, optionally filtered
    by GICS Sector (case-insensitive exact match).

    as_of=None (default): today's live/cached scrape -- unchanged behavior,
    no DB touch. as_of=<date>: point-in-time membership from the persisted
    history in common.universe_history, raising ValueError if that date
    predates the backfilled coverage or no backfill has been run yet.
    """
    if as_of is not None:
        # Local import: avoids a module-level cycle (universe_history
        # imports list_universe from here) and keeps the as_of=None path
        # from ever touching common.universe_history at all.
        from common.universe_history import query_as_of  # noqa: PLC0415

        return query_as_of(as_of, sector=sector)

    df = load_universe()
    if sector:
        df = df[df["GICS Sector"].str.lower() == sector.lower()]
    rows: list[dict] = df.to_dict(orient="records")
    return rows


def resolve_symbol(query: str, as_of: date | None = None) -> dict | None:
    """
    Resolve a ticker symbol OR company name (case-insensitive, partial match
    on name) to a single canonical universe row, or None if nothing matches.

    as_of=None (default): resolves against today's live/cached scrape.
    as_of=<date>: resolves against point-in-time membership (see
    common.universe_history), raising ValueError if that date predates the
    backfilled coverage or no backfill has been run yet.
    """
    if as_of is not None:
        from common.universe_history import resolve_as_of  # noqa: PLC0415

        return resolve_as_of(query, as_of)

    df = load_universe()
    q = query.strip()
    if not q:
        return None

    exact = df[df["Symbol"].str.upper() == q.upper()]
    if not exact.empty:
        exact_row: dict = exact.iloc[0].to_dict()
        return exact_row

    name_match = df[df["Security"].str.contains(q, case=False, na=False, regex=False)]
    if not name_match.empty:
        name_row: dict = name_match.iloc[0].to_dict()
        return name_row

    return None


def is_tracked(symbol: str) -> bool:
    """Advisory only — a ticker not being in the tracked universe doesn't
    mean it's invalid for the price/news/market-data endpoints, which work
    against any real ticker."""
    return resolve_symbol(symbol) is not None
