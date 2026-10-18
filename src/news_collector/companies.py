"""Company names for a list of tickers, shared by the collector API and the CLI (T-067).

A ticker is never used as the company name: the matcher compares a URL to the name, so
`kla-corporation` would be rejected for a company called `KLAC`.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import date
from pathlib import Path

import httpx

from data_mining.queries import universe_db_path
from data_mining.universe_history import query_as_of
from news_collector.models import Company
from news_collector.sp500 import fetch_sp500_companies

log = logging.getLogger(__name__)


class CompanyLookupError(Exception):
    """A name could not be resolved. `status` is the HTTP status an API caller gets (422 or 502)."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


async def sp500_companies(client: httpx.AsyncClient) -> list[Company]:
    """The current S&P 500 from Wikipedia; a failure is a 502, never an empty list."""
    try:
        return await fetch_sp500_companies(client)
    except ValueError as exc:
        raise CompanyLookupError(
            f"Failed to resolve S&P 500 company list from Wikipedia: {exc}", 502
        ) from exc


def companies_from_names(tickers: list[str], names: list[str]) -> list[Company]:
    """Pair tickers with explicit names, positionally. The lists must have the same length."""
    if len(names) != len(tickers):
        raise CompanyLookupError(
            f"company_names is positional, one name per ticker: got {len(names)} names "
            f"for {len(tickers)} tickers. Pass the same number, or omit company_names.",
            422,
        )
    blank = [t for t, n in zip(tickers, names, strict=True) if not n.strip()]
    if blank:
        raise CompanyLookupError(f"company_names has a blank name for: {', '.join(blank)}", 422)
    return [Company(ticker=t, name=n.strip()) for t, n in zip(tickers, names, strict=True)]


async def lookup_companies(
    tickers: list[str], client: httpx.AsyncClient, start_date: date
) -> list[Company]:
    """
    Name each (upper-case) ticker by exact symbol: the current S&P 500 first, then the index
    as of `start_date` for a ticker that has left it.

    If Wikipedia is down the store alone may still answer; only a ticker left unresolved
    is then an error (502 when Wikipedia was down, else 422).
    """
    wiki_error: CompanyLookupError | None = None
    current: dict[str, Company] = {}
    try:
        current = {c.ticker.upper(): c for c in await sp500_companies(client)}
    except CompanyLookupError as exc:
        wiki_error = exc

    leavers: dict[str, str] = {}
    store_note = ""
    if any(t not in current for t in tickers):
        leavers, store_note = await _leaver_names(start_date)

    companies: list[Company] = []
    unknown: list[str] = []
    for t in tickers:
        if t in current:
            companies.append(Company(ticker=t, name=current[t].name, sector=current[t].sector))
        elif t in leavers:
            companies.append(Company(ticker=t, name=leavers[t]))
        else:
            unknown.append(t)
    if unknown:
        raise _unresolved(unknown, start_date, store_note, wiki_error)
    if wiki_error is not None:
        log.warning("Wikipedia was unavailable; every ticker was named from the store")
    return companies


def _unresolved(
    unknown: list[str], start_date: date, store_note: str, wiki_error: CompanyLookupError | None
) -> CompanyLookupError:
    detail = (
        f"No company name for: {', '.join(unknown)}. Not in the current S&P 500 or in the index "
        f"as of {start_date.isoformat()}"
        + (f" (point-in-time store: {store_note})" if store_note else "")
        + ". Pass company_names, one per ticker."
    )
    if wiki_error is not None:
        return CompanyLookupError(f"{wiki_error} {detail}", 502)
    return CompanyLookupError(detail, 422)


async def _leaver_names(start_date: date) -> tuple[dict[str, str], str]:
    """Exact Symbol -> Security for the index as of start_date, and why it is empty if it is.

    Never writes: a missing file is reported, not created (`queries.connect` would create it).
    """
    path = Path(universe_db_path())
    if not path.is_file():
        return {}, f"{path} does not exist"
    try:
        rows = await asyncio.to_thread(query_as_of, start_date)
    except (ValueError, OSError, sqlite3.Error) as exc:
        log.warning("No point-in-time universe for %s: %s", start_date, exc)
        return {}, str(exc)
    names = {
        r["Symbol"].upper(): r["Security"] for r in rows if r.get("Symbol") and r.get("Security")
    }
    return names, ""
