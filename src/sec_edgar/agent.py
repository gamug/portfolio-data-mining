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
import re
from typing import Any

import numpy as np
from edgar import Company, set_identity

logger = logging.getLogger(__name__)

# T-118 (tracks portfolio-financial-analysis's T-117, docs/model_fixes.md). Scope: this is a
# targeted stopgap for APA's (CIK 0001841666) revenue instance only, not a general fix -- see
# correct_revenue_totals' docstring for the actual mechanism (broader than first described) and
# PR #44 review (@eldova1702, 2026-09-28) for the full-universe scan that found it recurs
# elsewhere (163 mismatched values, 1,101 entirely-synthesized rows across the stored
# universe). The aggregate revenue concepts a filer's income statement tags for a consolidated
# total -- mirrors portfolio-financial-analysis/src/fundamental_agent/statements.py's
# REGISTRY["revenue"].total_concepts, the downstream consumer of this payload.
_REVENUE_TOTAL_CONCEPTS = (
    "us-gaap_Revenues",
    "us-gaap_RevenuesNetOfInterestExpense",
    "us-gaap_RegulatedAndUnregulatedOperatingRevenue",
)
# A row's label reads as a revenue total independent of any filer's own custom-taxonomy
# extension concept. Excludes "Total cost of revenue(s)" -- a near-universal COGS-line label
# that otherwise matches "total"/"revenue" as bare substrings (found, and excluded, by a
# full-universe scan in portfolio-financial-analysis before T-117 shipped: ADBE, STE, TER,
# TSLA, URI, XYZ all use this exact phrase, none a real revenue-total defect).
_LABEL_TOTAL_RE = re.compile(r"\btotal\b.{0,40}\brevenues?\b", re.IGNORECASE)
_LABEL_TOTAL_EXCLUDE_RE = re.compile(r"\bcost\b", re.IGNORECASE)
# A later candidate must be no more than this fraction of the first total to count as a
# contradiction, not rounding/immaterial noise.
_LABEL_TOTAL_CONTRADICTION_RATIO = 0.75
# Each row between the two totals must be no larger than this fraction of the later, trusted
# total, or the correction is too uncertain to trust -- drop the value rather than guess.
_BETWEEN_ROW_CEILING = 0.25
# Row-metadata keys edgartools' income-statement dataframe carries alongside each period's
# value column -- everything else on a row is a period column (e.g. "2023-12-31 (FY)").
_ROW_METADATA_KEYS = frozenset(
    {
        "concept",
        "label",
        "standard_concept",
        "level",
        "abstract",
        "dimension",
        "is_breakdown",
        "dimension_axis",
        "dimension_member",
        "dimension_member_label",
        "dimension_label",
        "balance",
        "weight",
        "preferred_sign",
        "parent_concept",
        "parent_abstract_concept",
    }
)


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _period_columns(rows: list[dict[str, Any]]) -> list[str]:
    """Every dict key across *rows* that isn't a fixed metadata column -- i.e. a period
    column, in first-seen order."""
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in _ROW_METADATA_KEYS and key not in keys:
                keys.append(key)
    return keys


def correct_revenue_totals(
    income_statement: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """T-118: a filer's aggregate revenue concept (:data:`_REVENUE_TOTAL_CONCEPTS`) can be a
    value edgartools' own XBRL rendering surfaces as if it were the consolidated total, not
    merely a filer-side tagging slip.

    **The actual mechanism (PR #44 review, @eldova1702, 2026-09-28) is broader than this
    function's fix.** APA (CIK 0001841666) never filed a real, non-dimensional
    ``us-gaap:Revenues`` fact at all -- edgartools *synthesizes* the non-dimensional row by
    summing dimensional members of an axis, and when that axis has a parent member whose own
    value already includes its children (APA's ``srt:ProductOrServiceAxis``: "Oil and gas",
    the parent, equals "Oil and gas, excluding purchased" + "Purchased oil and gas costs", its
    two children), the parent is counted twice: verified to the dollar for two independent
    fiscal years -- FY2023 `8,279 + 7,385 + 894 = 16,558`; FY2024
    `9,737 + 8,196 + 1,541 = 19,474`. The same synthesis-with-double-counting shape reproduces
    on other filers/concepts entirely unrelated to revenue (the review's live example: SNA's
    10-Q "Operating earnings"), and a full-universe scan (500 companies, the 61 ``us-gaap``
    concepts ``fundamental_agent`` reads, compared against each company's own SEC
    ``companyfacts``) found 163 stored values matching no SEC-filed value at that period end
    (after excluding sign-convention flips) and 105 ``(company, concept)`` pairs (1,101 rows)
    that were never filed non-dimensionally at all, so every one is synthesized.

    **This function fixes APA's revenue instance only** -- a targeted stopgap, not a general
    fix for the mechanism above. It stays the right shape for APA specifically because APA has
    no filed non-dimensional revenue total to fall back on at all (`T-117`'s local guard in
    ``portfolio-financial-analysis`` needs *some* trustworthy value). The general fix -- validate
    every non-dimensional row against the filing's own default-context (no-dimension) XBRL
    facts, pass through what was genuinely filed, replace what wasn't with the filed value if
    one exists, or mark it synthesized -- is a separate, later `T-118` PR, not this one.

    Detected structurally (document order, the non-dimensional-row filter
    ``portfolio-financial-analysis``'s ``Statements._rows_for`` also applies downstream), not
    by filer: a later, non-dimensional row whose label also reads as a revenue total
    (:data:`_LABEL_TOTAL_RE`) and is materially smaller than the first
    :data:`_REVENUE_TOTAL_CONCEPTS` match is the contradiction itself -- a well-formed
    statement's later, broader total is never smaller than an earlier one labeled the same
    way. When found, and every row between the two is individually small enough to trust as
    an adjustment item, that period's value is corrected to the later row's value less those
    in-between rows (recovers APA's real "Total revenues" to the dollar). Otherwise the value
    is dropped (``None``) rather than trusted or guessed.

    Ported from ``portfolio-financial-analysis``'s ``Statements._label_total_correction``
    (T-117, ``docs/model_fixes.md``), which validated this exact algorithm against every
    filing stored across that repo's full 503-asset production universe: 16 filing-periods
    flagged, all APA, all safely corrected, 0 false positives elsewhere.

    Returns ``(rows, corrections)`` -- a new list (*income_statement* is not mutated) and one
    ``{"concept", "column", "original", "corrected", "rule": "T-118"}`` record per value this
    function changed (``corrected`` is ``None`` for a dropped, not-safely-derivable value) --
    a derived number must never reach a caller looking indistinguishable from a filed fact."""
    rows = [dict(r) for r in income_statement]
    corrections: list[dict[str, Any]] = []
    non_dim = [i for i, r in enumerate(rows) if not r.get("abstract") and not r.get("dimension")]
    winner_pos = next(
        (i for i in non_dim if rows[i].get("concept") in _REVENUE_TOTAL_CONCEPTS), None
    )
    if winner_pos is None:
        return rows, corrections
    concept = str(rows[winner_pos].get("concept"))
    for column in _period_columns(rows):
        total_value = _numeric(rows[winner_pos].get(column))
        if total_value is None:
            continue
        for i in non_dim:
            if i <= winner_pos:
                continue
            label = str(rows[i].get("label") or "")
            if not _LABEL_TOTAL_RE.search(label) or _LABEL_TOTAL_EXCLUDE_RE.search(label):
                continue
            later_value = _numeric(rows[i].get(column))
            if later_value is None or later_value <= 0:
                continue
            if later_value >= total_value * _LABEL_TOTAL_CONTRADICTION_RATIO:
                continue
            between = [
                v
                for j in non_dim
                if winner_pos < j < i
                for v in [_numeric(rows[j].get(column))]
                if v is not None
            ]
            corrected_value = None
            if not any(abs(v) > later_value * _BETWEEN_ROW_CEILING for v in between):
                corrected_value = later_value - sum(between)
            rows[winner_pos][column] = corrected_value
            corrections.append(
                {
                    "concept": concept,
                    "column": column,
                    "original": total_value,
                    "corrected": corrected_value,
                    "rule": "T-118",
                }
            )
            break
    return rows, corrections


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
        3. get_filing_by_year(...)      -- list filings for a form+year (a
                                            year can have more than one, e.g.
                                            three "10-Q"s)
        4. get_financials(..., accession_number=...) -- pull a specific
                                            filing's financials; pass the
                                            accession_number from step 3 if
                                            more than one filing matched
        5. search_filings(...)          -- find filings mentioning a keyword
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
        Get metadata for all filings of a given form type in a given year.

        A single calendar year can have more than one filing of the same
        form -- e.g. a company typically files three "10-Q"s per year, one
        per fiscal quarter. This returns all of them, most-recent-first.
        For forms that are inherently one-per-year (e.g. "10-K") the list
        will normally have exactly one element.

        Args:
            cik_or_symbol: Ticker symbol or CIK number.
            form: SEC form type, e.g. "10-K", "10-Q" (required).
            year: Four-digit calendar year of the filing date, e.g. 2023.

        Returns:
            On success: {"success": True, "data": [{"form": str,
                "filing_date": str, "accession_number": str}, ...]}
                An empty list is a valid, non-error result (no filing of
                that form in that year).
            On failure: {"success": False, "error": str}
            Tip: call list_years_available first if you're not sure which
            years actually have a filing of this form. If more than one
            filing comes back (e.g. "10-Q"), pass the accession_number of
            the one you want to get_financials to disambiguate.
        """
        try:
            company = self._resolve_company(cik_or_symbol)
            filings = company.get_filings(form=form)
            matches = [f for f in filings if f.filing_date.year == year]  # type: ignore[union-attr]
            return {"success": True, "data": [self._filing_to_dict(f) for f in matches]}
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

    def get_financials(
        self,
        cik_or_symbol: str,
        form: str,
        year: int,
        accession_number: str | None = None,
    ) -> dict:
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
            accession_number: Required when form+year matches more than one
                filing (e.g. "10-Q", which has up to three filings per
                year). Call get_filing_by_year first to list the
                candidates and pass the accession_number of the one you
                want. Ignored when form+year matches exactly one filing
                (e.g. "10-K").

        Returns:
            On success: {"success": True, "data": {
                "income_statement": [<row dicts>],
                "balance_sheet": [<row dicts>],
                "cash_flow": [<row dicts>],
                "corrections": [<{concept, column, original, corrected, rule}>]}}
            "corrections" (T-118) lists every income-statement value this method derived or
            dropped rather than returning as-is from edgartools -- APA's revenue instance only
            today, see correct_revenue_totals' docstring; empty when nothing was corrected.
            A derived number is never indistinguishable from a filed fact in this response.
            On failure: {"success": False, "error": str} -- e.g. filing not
            found, no XBRL data, or form+year is ambiguous (multiple
            filings matched and accession_number wasn't given or didn't
            match one of them).
        """
        try:
            company = self._resolve_company(cik_or_symbol)
            filings = company.get_filings(form=form)
            matches = [f for f in filings if f.filing_date.year == year]  # type: ignore[union-attr]
            if not matches:
                return {
                    "success": False,
                    "error": f"No '{form}' filing found for '{cik_or_symbol}' in {year}.",
                }
            if len(matches) == 1:
                filing = matches[0]
            else:
                available = ", ".join(f.accession_number for f in matches)  # type: ignore[union-attr]
                if not accession_number:
                    return {
                        "success": False,
                        "error": (
                            f"Found {len(matches)} '{form}' filings for '{cik_or_symbol}' in "
                            f"{year}; call get_filing_by_year to list them, then pass the "
                            f"accession_number of the one you want. Available: {available}"
                        ),
                    }
                filing = next(
                    (f for f in matches if f.accession_number == accession_number),  # type: ignore[union-attr]
                    None,
                )
                if filing is None:
                    return {
                        "success": False,
                        "error": (
                            f"accession_number '{accession_number}' does not match any "
                            f"'{form}' filing for '{cik_or_symbol}' in {year}. "
                            f"Available: {available}"
                        ),
                    }
            xbrl = filing.xbrl()
            if xbrl is None:
                return {
                    "success": False,
                    "error": f"The {year} '{form}' filing for '{cik_or_symbol}' has no XBRL financial data.",
                }
            income_statement, revenue_corrections = correct_revenue_totals(
                self.clean_data_frame(xbrl.statements.income_statement().to_dataframe())  # type: ignore[union-attr]
            )
            return {
                "success": True,
                "data": {
                    "income_statement": income_statement,
                    "balance_sheet": self.clean_data_frame(
                        xbrl.statements.balance_sheet().to_dataframe()
                    ),  # type: ignore[union-attr]
                    "cash_flow": self.clean_data_frame(
                        xbrl.statements.cashflow_statement().to_dataframe()
                    ),  # type: ignore[union-attr]
                    "corrections": revenue_corrections,
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
