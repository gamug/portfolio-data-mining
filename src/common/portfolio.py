"""
src/common/portfolio.py

Loads and queries the tracked S&P 500 ticker universe, fetched live from
Wikipedia's "List of S&P 500 companies" page and cached in-process for the
life of the process. Single source of truth for "what tickers/companies do
we track", used to list the universe and to resolve a ticker symbol or
company name to a canonical row.

Previously read a committed `s&p500/s&p500.csv` seeded into `input/` by
`common.utils.init_repository()`; switched to a live fetch (same approach
as `news_collector.sp500` and `extractor.reference`, both of which already
source the S&P 500 constituent list from this same Wikipedia page) so the
tracked universe doesn't silently drift from reality as index membership
changes, and to drop a static CSV file this repo had to remember to keep
up to date.

Shared by the pricing and edgar modules (both need "what tickers do we
track"), which is why it lives in `common/` rather than either one.
"""

import functools
import re
from typing import Optional

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
    "Symbol", "Security", "GICS Sector", "GICS Sub-Industry",
    "Headquarters Location", "Date added", "CIK", "Founded",
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
            re.sub(r"\s*\|\s*", "", c.get_text(strip=True)).strip()
            for c in cells[: len(COLUMNS)]
        ]
        if not values[0]:
            continue
        values[0] = values[0].upper()  # Symbol
        records.append(dict(zip(COLUMNS, values)))

    if not records:
        raise ValueError(
            "Parsed 0 companies from the constituents table at "
            f"{WIKIPEDIA_SP500_URL} -- Wikipedia's page markup may have changed"
        )
    return pd.DataFrame.from_records(records, columns=COLUMNS)


def _fetch_universe_from_wikipedia() -> pd.DataFrame:
    """Blocking GET + parse. `load_universe()`'s cache means this only ever
    runs once per process, so a synchronous client here -- rather than
    threading async through every caller (the /universe routes are sync
    FastAPI handlers) -- is a fine trade."""
    response = httpx.get(
        WIKIPEDIA_SP500_URL, headers=WIKIPEDIA_HEADERS, follow_redirects=True, timeout=30.0
    )
    response.raise_for_status()
    return _parse_constituents_table(response.text)


@functools.lru_cache(maxsize=1)
def load_universe() -> pd.DataFrame:
    """
    Load the tracked S&P 500 universe, fetched live from Wikipedia. Cached
    after the first call — call `load_universe.cache_clear()` to force a
    re-fetch (e.g. to pick up index changes without restarting the process).
    """
    return _fetch_universe_from_wikipedia()


def list_universe(sector: Optional[str] = None) -> list[dict]:
    """Return the tracked universe as a list of row dicts, optionally filtered
    by GICS Sector (case-insensitive exact match)."""
    df = load_universe()
    if sector:
        df = df[df["GICS Sector"].str.lower() == sector.lower()]
    return df.to_dict(orient="records")

def resolve_symbol(query: str) -> Optional[dict]:
    """
    Resolve a ticker symbol OR company name (case-insensitive, partial match
    on name) to a single canonical universe row, or None if nothing matches.
    """
    df = load_universe()
    q = query.strip()
    if not q:
        return None

    exact = df[df["Symbol"].str.upper() == q.upper()]
    if not exact.empty:
        return exact.iloc[0].to_dict()

    name_match = df[df["Security"].str.contains(q, case=False, na=False, regex=False)]
    if not name_match.empty:
        return name_match.iloc[0].to_dict()

    return None


def is_tracked(symbol: str) -> bool:
    """Advisory only — a ticker not being in the tracked universe doesn't
    mean it's invalid for the price/news/market-data endpoints, which work
    against any real ticker."""
    return resolve_symbol(symbol) is not None
