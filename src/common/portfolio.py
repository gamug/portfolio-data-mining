"""
src/common/portfolio.py

Loads and queries the tracked S&P 500 ticker universe (seeded into
input/s&p500.csv by `common.utils.init_repository()`). Single source of
truth for "what tickers/companies do we track", used to list the universe
and to resolve a ticker symbol or company name to a canonical row.

Shared by the pricing and edgar modules (both need "what tickers do we
track"), which is why it lives in `common/` rather than either one.
"""

import functools
from pathlib import Path
from typing import Optional

import pandas as pd

from common.config import general

COLUMNS = [
    "Symbol", "Security", "GICS Sector", "GICS Sub-Industry",
    "Headquarters Location", "Date added", "CIK", "Founded",
]


@functools.lru_cache(maxsize=1)
def load_universe() -> pd.DataFrame:
    """
    Load the tracked S&P 500 universe CSV (';'-delimited). Cached after the
    first call — call `load_universe.cache_clear()` to force a reload (e.g.
    after replacing the CSV on disk).
    """
    path = Path(general["paths"]["input"]) / "s&p500.csv"
    return pd.read_csv(path, sep=";")


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
