"""Reference data lookups: GICS Sector / Sub-Industry per ticker, sourced
live from Wikipedia's "List of S&P 500 companies" page. See CLAUDE.md for
why this replaced the old data/sp500_sample.csv-based sector lookup.
"""

import warnings

import httpx
from bs4 import BeautifulSoup

# Constituents table columns used below: [0]=ticker [2]=GICS sector [3]=GICS sub-industry
_MIN_CONSTITUENTS_COLUMNS = 4

WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
WIKIPEDIA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) sp500-news-extractor/0.1 "
        "(+https://github.com/; research/dataset-building use)"
    )
}


def parse_gics_table(html: str) -> dict[str, dict[str, str]]:
    """Pure parse: Wikipedia constituents table HTML -> ticker -> GICS map.

    Uses cell position, not header text -- the "GICS Sector" header cell
    renders as "GICSSector" (embedded <br>, no space) so text-matching the
    header row is unreliable. Raises ValueError if the #constituents table
    isn't present (e.g. page structure changed) so callers can decide how
    to degrade.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "constituents"})
    if table is None:
        raise ValueError("constituents table not found in Wikipedia S&P 500 page")

    gics_map: dict[str, dict[str, str]] = {}
    rows = table.find_all("tr")[1:]  # skip header row
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < _MIN_CONSTITUENTS_COLUMNS:
            continue
        ticker = cells[0].get_text(strip=True).upper()
        if not ticker:
            continue
        gics_map[ticker] = {
            "sector": cells[2].get_text(strip=True),
            "sub_industry": cells[3].get_text(strip=True),
        }
    return gics_map


async def fetch_wikipedia_sp500_html(client: httpx.AsyncClient) -> str:
    """Thin I/O wrapper: GET the Wikipedia S&P 500 constituents page."""
    response = await client.get(
        WIKIPEDIA_SP500_URL, headers=WIKIPEDIA_HEADERS, follow_redirects=True
    )
    response.raise_for_status()
    return str(response.text)


async def load_gics_map(client: httpx.AsyncClient) -> dict[str, dict[str, str]]:
    """Fetch + parse, degrading to {} on any failure.

    Covers both network-level failures (timeout, connection error, HTTP
    error status) and structural ones (Wikipedia's table markup changed and
    parse_gics_table can't find #constituents) -- either way the pipeline
    should keep running with gics_sector/gics_sub_industry left NULL for
    this run, not abort the whole batch.
    """
    try:
        html = await fetch_wikipedia_sp500_html(client)
        return parse_gics_table(html)
    except (httpx.HTTPError, ValueError) as exc:
        warnings.warn(
            f"Failed to load GICS sector/sub-industry map from Wikipedia: {exc}", stacklevel=2
        )
        return {}
