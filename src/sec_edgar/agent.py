"""
edgar_agent.py

Agent-facing tool wrapper around the `edgar` (edgartools) SEC EDGAR library.

Design notes for whoever wires this into the agent framework:
- Every public method returns a plain JSON-serializable dict, never a custom
    SDK object (Company, Filing, DataFrame, etc). Agents can't reason about or
    serialize arbitrary Python objects, so everything is converted to
    dicts/lists/strings before returning.
- Every public method returns {"success": bool, "data": ...} on success or
    {"success": False, "error": "<human readable message>"} on failure.
    No method raises an exception during normal use -- this means the agent
    always gets something it can inspect and react to (e.g. tell the user
    "that ticker doesn't exist" instead of crashing).
- Methods that could be slow or unbounded (e.g. full-text search across a
    company's filing history) take an explicit limit parameter with a small
    default, to keep tool calls fast and predictable.
"""

import logging
import os
from typing import Any

import numpy as np
from edgar import Company, set_identity

logger = logging.getLogger(__name__)


class EdgarAgent:
    """
    Tool for looking up U.S. public companies' SEC EDGAR filings and financial data.

    WHEN TO USE THIS TOOL
    ----------------------
    Use this whenever the user asks about a public company's SEC filings,
    financial statements (income statement, balance sheet, cash flow),
    annual/quarterly reports, or wants filings searched for a topic
    (e.g. "climate risk", "litigation", "executive compensation").
    This tool only covers companies that file with the U.S. SEC (i.e. it
    will not have data for private companies or most non-U.S. companies).

    HOW TO IDENTIFY A COMPANY
    --------------------------
    Every method takes `cik_or_symbol`, a single string identifying the
    company. Accepted formats:
        - Stock ticker symbol, e.g. "AAPL", "MSFT", "TSLA"
        - SEC CIK number (as a string), e.g. "320193" or "0000320193"
    If unsure a ticker is correct, call `get_company_info` first to confirm.

    COMMON SEC FORM TYPES (used in the `form` parameter)
    -------------------------------------------------------
        "10-K"    Annual report (audited, full-year financials)
        "10-Q"    Quarterly report (unaudited)
        "8-K"     Current report on major events (M&A, exec changes, etc.)
        "DEF 14A" Proxy statement (executive comp, shareholder votes)
        "S-1"     IPO registration statement

    RETURN FORMAT
    --------------
    Every method returns a dict shaped like one of:
        {"success": True,  "data": <result>}
        {"success": False, "error": "<what went wrong>"}
    Always check "success" before using "data". A False result is a normal,
    expected outcome (e.g. bad ticker, no filing for that year) -- handle it
    by informing the user, not by retrying blindly.

    SUGGESTED WORKFLOW
    --------------------
        1. get_company_info(...)        -- confirm the company/ticker is right
        2. list_years_available(...)    -- see which years/forms actually exist
        3. get_financials(...) / get_filing_by_year(...) -- pull a specific filing
        4. search_filings(...)          -- find filings mentioning a keyword
    """

    def __init__(self, name: str | None = None, email: str | None = None) -> None:
        """
        Initialize the tool. The SEC requires a real identity (name + email)
        for programmatic access to EDGAR; this is configured once here and
        is not something the agent needs to pass on individual calls.

        Args:
            name: Your name, or reads from the NAME env var if omitted.
            email: Your email, or reads from the EMAIL env var if omitted.
        """
        name = name or os.getenv("NAME", "Your Name")
        email = email or os.getenv("EMAIL", "Your Email")
        set_identity(f"{name} {email}")

    # ------------------------------------------------------------------
    # Internal helpers (not exposed to the agent as tool methods)
    # ------------------------------------------------------------------

    def _resolve_company(self, cik_or_symbol: str) -> Company:
        if not cik_or_symbol or not cik_or_symbol.strip():
            raise ValueError("cik_or_symbol must be a non-empty ticker symbol or CIK number.")
        return Company(cik_or_symbol.strip())

    def _filing_to_dict(self, f: Any) -> dict:
        return {
            "form": getattr(f, "form", None),
            "filing_date": str(getattr(f, "filing_date", "")),
            "accession_number": getattr(f, "accession_number", None),
        }

    # ------------------------------------------------------------------
    # Public tool methods
    # ------------------------------------------------------------------

    def get_company_info(self, cik_or_symbol: str) -> dict:
        """
        Look up basic identifying information for a company.

        Use this first if you're unsure a ticker/CIK is correct, or the user
        just wants to confirm which company is being referred to.

        Args:
            cik_or_symbol: Ticker symbol (e.g. "AAPL") or CIK number (e.g. "320193").

        Returns:
            On success: {"success": True, "data": {"name": str, "cik": str,
                "sic": str, "sic_description": str}}
            On failure: {"success": False, "error": str} -- e.g. ticker not found.

        Example:
            get_company_info("AAPL")
            -> {"success": True, "data": {"name": "Apple Inc.", "cik": "320193",
                "sic": "3571", "sic_description": "Electronic Computers"}}
        """
        try:
            company = self._resolve_company(cik_or_symbol)
            return {
                "success": True,
                "data": {
                    "name": getattr(company, "name", None),
                    "cik": str(getattr(company, "cik", "")),
                    "sic": getattr(company, "sic", None),
                    "sic_description": getattr(company, "sic_description", None),
                },
            }
        except Exception as e:
            return {"success": False, "error": f"Could not find company '{cik_or_symbol}': {e}"}

    def get_filings(self, cik_or_symbol: str, form: str | None = None, limit: int = 20) -> dict:
        """
        List a company's recent filings, optionally filtered by form type.

        Args:
            cik_or_symbol: Ticker symbol or CIK number.
            form: SEC form type to filter by, e.g. "10-K". Omit to get all types.
            limit: Max filings to return, most recent first (default 20).

        Returns:
            On success: {"success": True, "data": [{"form": str,
                "filing_date": "YYYY-MM-DD", "accession_number": str}, ...]}
            On failure: {"success": False, "error": str}
            An empty list is a valid, non-error result (no filings found).
        """
        try:
            company = self._resolve_company(cik_or_symbol)
            filings = company.get_filings(form=form) if form else company.get_filings()
            results = [self._filing_to_dict(f) for f in list(filings)[:limit]]
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to retrieve filings for '{cik_or_symbol}': {e}",
            }
        else:
            return {"success": True, "data": results}

    def get_filing_by_year(self, cik_or_symbol: str, form: str, year: int) -> dict:
        """
        Get metadata for a specific filing, identified by form type and year.

        Args:
            cik_or_symbol: Ticker symbol or CIK number.
            form: SEC form type, e.g. "10-K", "10-Q" (required).
            year: Four-digit calendar year of the filing date, e.g. 2023.

        Returns:
            On success: {"success": True, "data": {"form": str,
                "filing_date": str, "accession_number": str}}
            On failure: {"success": False, "error": str} -- e.g. no such filing.
            Tip: call list_years_available first if you're not sure which
            years actually have a filing of this form.
        """
        try:
            company = self._resolve_company(cik_or_symbol)
            filings = company.get_filings(form=form)
            match = next((f for f in filings if f.filing_date.year == year), None)  # type: ignore[union-attr]
            if match is None:
                return {
                    "success": False,
                    "error": f"No '{form}' filing found for '{cik_or_symbol}' in {year}.",
                }
            return {"success": True, "data": self._filing_to_dict(match)}
        except Exception as e:
            return {"success": False, "error": f"Failed to retrieve filing: {e}"}

    def get_latest_filing(self, cik_or_symbol: str, form: str) -> dict:
        """
        Get metadata for the most recent filing of a given form type.

        Args:
            cik_or_symbol: Ticker symbol or CIK number.
            form: SEC form type, e.g. "10-K", "10-Q", "8-K".

        Returns:
            On success: {"success": True, "data": {"form": str,
                "filing_date": str, "accession_number": str}}
            On failure: {"success": False, "error": str} -- e.g. no filings of that type.
        """
        try:
            company = self._resolve_company(cik_or_symbol)
            filings = company.get_filings(form=form)
        except Exception as e:
            return {"success": False, "error": f"Failed to retrieve latest filing: {e}"}
        else:
            if filings_list := list(filings):
                return {"success": True, "data": self._filing_to_dict(filings_list[0])}
            return {"success": False, "error": f"No '{form}' filings found for '{cik_or_symbol}'."}

    def clean_data_frame(self, df: Any) -> list[Any]:
        """_summary_

        Args:
            df (_type_): _description_

        Returns:
            list: _description_
        """
        df = df.astype(object).replace({np.nan: None})
        income_statement: list[Any] = df.to_dict(orient="records")
        return income_statement

    def get_financials(self, cik_or_symbol: str, form: str, year: int) -> dict:
        """
        Extract the three core financial statements (income statement, balance
        sheet, cash flow statement) from a company's filing for a given year.

        NOTE: Only works for forms containing XBRL financial data -- typically
        "10-K" (annual) or "10-Q" (quarterly). The result can be large; when
        replying to the user, summarize the key figures rather than dumping
        every row unless they specifically ask for full detail.

        Args:
            cik_or_symbol: Ticker symbol or CIK number.
            form: "10-K" or "10-Q".
            year: Four-digit calendar year of the filing, e.g. 2023.

        Returns:
            On success: {"success": True, "data": {
                "income_statement": [<row dicts>],
                "balance_sheet": [<row dicts>],
                "cash_flow": [<row dicts>]}}
            On failure: {"success": False, "error": str} -- e.g. filing not
            found, or it has no XBRL financial data.
        """
        try:
            company = self._resolve_company(cik_or_symbol)
            filings = company.get_filings(form=form)
            filing = next((f for f in filings if f.filing_date.year == year), None)  # type: ignore[union-attr]
            if filing is None:
                return {
                    "success": False,
                    "error": f"No '{form}' filing found for '{cik_or_symbol}' in {year}.",
                }
            xbrl = filing.xbrl()
            if xbrl is None:
                return {
                    "success": False,
                    "error": f"The {year} '{form}' filing for '{cik_or_symbol}' has no XBRL financial data.",
                }
            return {
                "success": True,
                "data": {
                    "income_statement": self.clean_data_frame(
                        xbrl.statements.income_statement().to_dataframe()
                    ),  # type: ignore[union-attr]
                    "balance_sheet": self.clean_data_frame(
                        xbrl.statements.balance_sheet().to_dataframe()
                    ),  # type: ignore[union-attr]
                    "cash_flow": self.clean_data_frame(
                        xbrl.statements.cashflow_statement().to_dataframe()
                    ),  # type: ignore[union-attr]
                },
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to extract financials: {e}"}

    def list_years_available(self, cik_or_symbol: str, form: str) -> dict:
        """
        List which calendar years have a filing of the given form type.

        Useful to check BEFORE calling get_financials/get_filing_by_year with
        a specific year, so you don't have to guess.

        Args:
            cik_or_symbol: Ticker symbol or CIK number.
            form: SEC form type, e.g. "10-K", "10-Q".

        Returns:
            On success: {"success": True, "data": [2020, 2021, 2022, 2023]}
            On failure: {"success": False, "error": str}
        """
        try:
            company = self._resolve_company(cik_or_symbol)
            filings = company.get_filings(form=form)
            years = sorted({f.filing_date.year for f in filings})  # type: ignore[union-attr]
        except Exception as e:
            return {"success": False, "error": f"Failed to list available years: {e}"}
        else:
            return {"success": True, "data": years}

    def search_filings(
        self,
        cik_or_symbol: str,
        keyword: str,
        form: str | None = None,
        max_filings_to_search: int = 15,
    ) -> dict:
        """
        Search the full text of a company's recent filings for a keyword or phrase.

        WARNING: This downloads and scans the text of each filing, so it is
        slow relative to the other methods. `max_filings_to_search` caps how
        many of the most recent filings are checked (default 15, keep it
        small). Pass `form` (e.g. "10-K") to narrow the search when possible.

        Args:
            cik_or_symbol: Ticker symbol or CIK number.
            keyword: Word or phrase to search for, e.g. "climate risk".
                Case-insensitive.
            form: Optional SEC form type to restrict the search to.
            max_filings_to_search: Max number of most-recent filings to scan
                (default 15). Raise only if the user explicitly needs a
                deeper search and is willing to wait.

        Returns:
            On success: {"success": True, "data": [{"filing_date": str,
                "form": str, "accession_number": str}, ...]}
                An empty list means no matches were found -- this is not an error.
            On failure: {"success": False, "error": str}
        """
        try:
            company = self._resolve_company(cik_or_symbol)
            filings = company.get_filings(form=form) if form else company.get_filings()
            keyword_lower = keyword.lower()
            results = []
            for f in list(filings)[:max_filings_to_search]:
                try:
                    if keyword_lower in f.text().lower():
                        results.append(self._filing_to_dict(f))
                except Exception:
                    # Skip filings whose text can't be fetched rather than
                    # failing the whole search.
                    logger.warning(
                        "Could not read text for filing %s", getattr(f, "accession_number", "?")
                    )
                    continue
        except Exception as e:
            return {"success": False, "error": f"Search failed: {e}"}
        else:
            return {"success": True, "data": results}
