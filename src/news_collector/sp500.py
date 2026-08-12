"""S&P 500 constituent list, sourced live from Wikipedia.

Fetches and parses the "constituents" table from
https://en.wikipedia.org/wiki/List_of_S%26P_500_companies, so discovery can run
across the entire index without a maintained local CSV. This is the fallback
source whenever neither --tickers/tickers nor --sp500 is supplied to the CLI/API.
"""

from __future__ import annotations

import logging

import httpx
from lxml import html

from news_collector.models import Company
from news_collector.utils.http import fetch_text

log = logging.getLogger(__name__)

SP500_WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

_TABLE_ID = "constituents"
_SYMBOL_COLUMN = "Symbol"
_SECURITY_COLUMN = "Security"
_SECTOR_COLUMN = "GICS Sector"


async def fetch_sp500_companies(client: httpx.AsyncClient) -> list[Company]:
    """
    Fetch and parse the current S&P 500 constituent list from Wikipedia.

    Returns:
        One Company (ticker, name, sector) per constituent row, in table order.

    Raises:
        ValueError: if the constituents table or its expected columns aren't
            found (e.g. Wikipedia's markup changed). Callers should not treat
            this as "zero companies" and silently proceed — that would look
            like a full S&P 500 run while actually discovering nothing.
    """
    page_html = await fetch_text(client, SP500_WIKIPEDIA_URL)
    companies = _parse_constituents(page_html)
    log.info("Fetched %d S&P 500 companies from Wikipedia", len(companies))
    return companies


def _parse_constituents(page_html: str) -> list[Company]:
    tree = html.fromstring(page_html)
    try:
        table = tree.get_element_by_id(_TABLE_ID)
    except KeyError:
        raise ValueError(
            f"No element with id={_TABLE_ID!r} found at {SP500_WIKIPEDIA_URL} - "
            "Wikipedia's page markup may have changed"
        ) from None

    header_cells = [th.text_content().strip() for th in table.xpath(".//tr[1]/th")]
    try:
        symbol_idx = header_cells.index(_SYMBOL_COLUMN)
        security_idx = header_cells.index(_SECURITY_COLUMN)
    except ValueError as exc:
        raise ValueError(
            f"Expected {_SYMBOL_COLUMN!r}/{_SECURITY_COLUMN!r} columns in the constituents "
            f"table, got {header_cells!r} - Wikipedia's page markup may have changed"
        ) from exc
    sector_idx = header_cells.index(_SECTOR_COLUMN) if _SECTOR_COLUMN in header_cells else None

    companies: list[Company] = []
    for row in table.xpath(".//tbody/tr")[1:]:  # skip the header row
        cells = row.xpath("./td")
        if len(cells) <= max(symbol_idx, security_idx):
            continue
        ticker = cells[symbol_idx].text_content().strip()
        name = cells[security_idx].text_content().strip()
        sector = (
            cells[sector_idx].text_content().strip()
            if sector_idx is not None and len(cells) > sector_idx
            else ""
        )
        if ticker and name:
            companies.append(Company(ticker=ticker.upper(), name=name, sector=sector))

    if not companies:
        raise ValueError(
            f"Parsed 0 companies from the constituents table at {SP500_WIKIPEDIA_URL} - "
            "Wikipedia's page markup may have changed"
        )
    return companies
