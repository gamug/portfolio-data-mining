"""Tests for EdgarAgent, the SEC EDGAR filings/financials tool wrapper.

All calls into the third-party `edgar` (edgartools) library are mocked --
these tests never hit the network. Every public method is expected to
return {"success": True, "data": ...} or {"success": False, "error": ...}
and never raise, so most tests assert on that shape directly.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from sec_edgar.agent import (
    EdgarAgent,
    _filed_nondimensional_values,
    _parse_period_column,
    _safe_cover_shares_outstanding,
    _safe_reconcile_with_filed_facts,
    correct_revenue_totals,
    cover_shares_outstanding,
    reconcile_with_filed_facts,
)


@pytest.fixture
def agent() -> EdgarAgent:
    with patch("sec_edgar.agent.set_identity"):
        return EdgarAgent(name="Jane Doe", email="jane@example.com")


def make_filing(
    form: str = "10-K",
    filing_date: date = date(2023, 3, 15),
    accession_number: str = "0000320193-23-000001",
) -> MagicMock:
    filing = MagicMock()
    filing.form = form
    filing.filing_date = filing_date
    filing.accession_number = accession_number
    return filing


# ---------------------------------------------------------------------
# __init__ / identity
# ---------------------------------------------------------------------


def test_init_sets_identity_from_explicit_args() -> None:
    with patch("sec_edgar.agent.set_identity") as mock_set_identity:
        EdgarAgent(name="Jane Doe", email="jane@example.com")
    mock_set_identity.assert_called_once_with("Jane Doe jane@example.com")


def test_init_sets_identity_from_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAME", "Env Name")
    monkeypatch.setenv("EMAIL", "env@example.com")
    with patch("sec_edgar.agent.set_identity") as mock_set_identity:
        EdgarAgent()
    mock_set_identity.assert_called_once_with("Env Name env@example.com")


def test_init_falls_back_to_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NAME", raising=False)
    monkeypatch.delenv("EMAIL", raising=False)
    with patch("sec_edgar.agent.set_identity") as mock_set_identity:
        EdgarAgent()
    mock_set_identity.assert_called_once_with("Your Name Your Email")


# ---------------------------------------------------------------------
# _resolve_company / _filing_to_dict (internal helpers)
# ---------------------------------------------------------------------


def test_resolve_company_rejects_empty_string(agent: EdgarAgent) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        agent._resolve_company("")


def test_resolve_company_rejects_whitespace_only(agent: EdgarAgent) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        agent._resolve_company("   ")


def test_resolve_company_strips_whitespace(agent: EdgarAgent) -> None:
    with patch("sec_edgar.agent.Company") as mock_company:
        agent._resolve_company("  AAPL  ")
    mock_company.assert_called_once_with("AAPL")


def test_filing_to_dict_maps_expected_fields(agent: EdgarAgent) -> None:
    filing = make_filing(form="10-K", filing_date=date(2023, 3, 15), accession_number="acc-1")
    result = agent._filing_to_dict(filing)
    assert result == {
        "form": "10-K",
        "filing_date": "2023-03-15",
        "accession_number": "acc-1",
    }


def test_filing_to_dict_tolerates_missing_attrs(agent: EdgarAgent) -> None:
    result = agent._filing_to_dict(object())
    assert result == {"form": None, "filing_date": "", "accession_number": None}


# ---------------------------------------------------------------------
# get_company_info
# ---------------------------------------------------------------------


def test_get_company_info_success(agent: EdgarAgent) -> None:
    mock_company = MagicMock()
    mock_company.name = "Apple Inc."
    mock_company.cik = 320193
    mock_company.sic = "3571"
    mock_company.sic_description = "Electronic Computers"
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_company_info("AAPL")

    assert result == {
        "success": True,
        "data": {
            "name": "Apple Inc.",
            "cik": "320193",
            "sic": "3571",
            "sic_description": "Electronic Computers",
        },
    }


def test_get_company_info_failure_returns_error_dict(agent: EdgarAgent) -> None:
    with patch("sec_edgar.agent.Company", side_effect=Exception("not found")):
        result = agent.get_company_info("NOT_A_REAL_TICKER")

    assert result["success"] is False
    assert "NOT_A_REAL_TICKER" in result["error"]


def test_get_company_info_rejects_blank_ticker(agent: EdgarAgent) -> None:
    result = agent.get_company_info("   ")
    assert result["success"] is False


# ---------------------------------------------------------------------
# get_filings
# ---------------------------------------------------------------------


def test_get_filings_filters_by_form(agent: EdgarAgent) -> None:
    filings = [make_filing(), make_filing()]
    mock_company = MagicMock()
    mock_company.get_filings.return_value = filings
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_filings("AAPL", form="10-K", limit=5)

    mock_company.get_filings.assert_called_once_with(form="10-K")
    assert result["success"] is True
    assert len(result["data"]) == 2


def test_get_filings_without_form_calls_get_filings_with_no_args(agent: EdgarAgent) -> None:
    mock_company = MagicMock()
    mock_company.get_filings.return_value = []
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        agent.get_filings("AAPL")

    mock_company.get_filings.assert_called_once_with()


def test_get_filings_respects_limit(agent: EdgarAgent) -> None:
    filings = [make_filing() for _ in range(10)]
    mock_company = MagicMock()
    mock_company.get_filings.return_value = filings
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_filings("AAPL", form="10-K", limit=3)

    assert len(result["data"]) == 3


def test_get_filings_failure_returns_error_dict(agent: EdgarAgent) -> None:
    with patch("sec_edgar.agent.Company", side_effect=Exception("boom")):
        result = agent.get_filings("AAPL", form="10-K")

    assert result == {
        "success": False,
        "error": "Failed to retrieve filings for 'AAPL': boom",
    }


# ---------------------------------------------------------------------
# get_filing_by_year
# ---------------------------------------------------------------------


def test_get_filing_by_year_returns_match(agent: EdgarAgent) -> None:
    match = make_filing(filing_date=date(2023, 3, 15))
    other = make_filing(filing_date=date(2022, 3, 15))
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [other, match]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_filing_by_year("AAPL", form="10-K", year=2023)

    assert result["success"] is True
    assert len(result["data"]) == 1
    assert result["data"][0]["filing_date"] == "2023-03-15"


def test_get_filing_by_year_no_match_returns_empty_list(agent: EdgarAgent) -> None:
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [make_filing(filing_date=date(2022, 3, 15))]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_filing_by_year("AAPL", form="10-K", year=1800)

    assert result == {"success": True, "data": []}


def test_get_filing_by_year_returns_all_matches_for_form_and_year(agent: EdgarAgent) -> None:
    q3 = make_filing(form="10-Q", filing_date=date(2023, 11, 1), accession_number="acc-q3")
    q2 = make_filing(form="10-Q", filing_date=date(2023, 8, 1), accession_number="acc-q2")
    q1 = make_filing(form="10-Q", filing_date=date(2023, 5, 1), accession_number="acc-q1")
    other_year = make_filing(
        form="10-Q", filing_date=date(2022, 11, 1), accession_number="acc-other"
    )
    mock_company = MagicMock()
    # edgartools returns filings most-recent-first.
    mock_company.get_filings.return_value = [q3, q2, q1, other_year]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_filing_by_year("AAPL", form="10-Q", year=2023)

    assert result["success"] is True
    assert [f["accession_number"] for f in result["data"]] == ["acc-q3", "acc-q2", "acc-q1"]


def test_get_filing_by_year_failure_returns_error_dict(agent: EdgarAgent) -> None:
    with patch("sec_edgar.agent.Company", side_effect=Exception("boom")):
        result = agent.get_filing_by_year("AAPL", form="10-K", year=2023)

    assert result["success"] is False


# ---------------------------------------------------------------------
# get_latest_filing
# ---------------------------------------------------------------------


def test_get_latest_filing_returns_first_result(agent: EdgarAgent) -> None:
    newest = make_filing(filing_date=date(2024, 1, 1))
    older = make_filing(filing_date=date(2023, 1, 1))
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [newest, older]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_latest_filing("AAPL", form="8-K")

    assert result["success"] is True
    assert result["data"]["filing_date"] == "2024-01-01"


def test_get_latest_filing_no_filings_returns_error(agent: EdgarAgent) -> None:
    mock_company = MagicMock()
    mock_company.get_filings.return_value = []
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_latest_filing("AAPL", form="8-K")

    assert result == {
        "success": False,
        "error": "No '8-K' filings found for 'AAPL'.",
    }


def test_get_latest_filing_failure_returns_error_dict(agent: EdgarAgent) -> None:
    with patch("sec_edgar.agent.Company", side_effect=Exception("boom")):
        result = agent.get_latest_filing("AAPL", form="8-K")

    assert result["success"] is False


# ---------------------------------------------------------------------
# clean_data_frame
# ---------------------------------------------------------------------


def test_clean_data_frame_converts_nan_to_none(agent: EdgarAgent) -> None:
    df = pd.DataFrame({"label": ["Revenue", "Net Income"], "value": [100.0, np.nan]})

    result = agent.clean_data_frame(df)

    assert result == [
        {"label": "Revenue", "value": 100.0},
        {"label": "Net Income", "value": None},
    ]


def test_clean_data_frame_empty_frame_returns_empty_list(agent: EdgarAgent) -> None:
    assert agent.clean_data_frame(pd.DataFrame()) == []


# ---------------------------------------------------------------------
# correct_revenue_totals (T-118: tracks portfolio-financial-analysis's T-117)
# ---------------------------------------------------------------------


def _row(concept: str, label: str, *, dimension: bool = False, **periods: float) -> dict:
    """A minimal non-abstract income-statement row, edgartools' real shape."""
    row: dict = {
        "concept": concept,
        "label": label,
        "standard_concept": None,
        "abstract": False,
        "dimension": dimension,
        "is_breakdown": False,
    }
    row.update(periods)
    return row


def _apa_fy2023_rows(key: str) -> list[dict]:
    """APA's real FY2023 10-K income-statement shape (CIK 0001841666, live-verified):
    `us-gaap_Revenues` "Total revenues" is edgartools' own mislabeled breakdown figure,
    roughly double the statement's later "Total revenues and other" subtotal, less four
    small adjustment lines between the two."""
    return [
        _row("us-gaap_Revenues", "Total revenues", **{key: 16_558_000_000.0}),
        _row(
            "us-gaap_GainLossOnDerivativeInstrumentsNetPretax",
            "Derivative instrument gains (losses), net",
            **{key: 99_000_000.0},
        ),
        _row("us-gaap_GainLossOnSaleOfBusiness", "Gain on divestitures, net", **{key: 8_000_000.0}),
        _row(
            "apa_LossOnPreviouslySoldProperties",
            "Losses on previously sold Gulf of Mexico properties",
            **{key: -212_000_000.0},
        ),
        _row("apa_OtherSalesRevenueLossesNet", "Other, net", **{key: 18_000_000.0}),
        _row("apa_RevenuesAndOther", "Total revenues and other", **{key: 8_192_000_000.0}),
        _row("us-gaap_OperatingLeaseExpense", "Lease operating expenses", **{key: 1_436_000_000.0}),
    ]


def test_correct_revenue_totals_recovers_apa_s_real_total() -> None:
    """The mirror-image defect: a `total_concepts`-style match that is implausibly
    *large*, corrected to the statement's own derived "Total revenues" ($8,279M) -- the
    exact figure portfolio-financial-analysis's T-117 acceptance criterion names. The
    correction is also recorded (T-118, PR #44 review) so a derived number is never
    indistinguishable from a filed fact."""
    key = "2023-12-31 (FY)"
    corrected, corrections = correct_revenue_totals(_apa_fy2023_rows(key))

    total_row = next(r for r in corrected if r["concept"] == "us-gaap_Revenues")
    assert total_row[key] == 8_279_000_000.0
    assert corrections == [
        {
            "concept": "us-gaap_Revenues",
            "column": key,
            "original": 16_558_000_000.0,
            "corrected": 8_279_000_000.0,
            "rule": "T-118",
        }
    ]


def test_correct_revenue_totals_leaves_a_genuinely_larger_total_alone() -> None:
    """A later, larger "and other" total (APA's own FY2022, not a defect: "Total revenues
    and other" $12,132M >= "Total revenues" $11,075M) must not be treated as a
    contradiction -- and no correction is recorded for it."""
    key = "2022-12-31 (FY)"
    rows = [
        _row("us-gaap_Revenues", "Total revenues", **{key: 11_075_000_000.0}),
        _row("apa_RevenuesAndOther", "Total revenues and other", **{key: 12_132_000_000.0}),
    ]

    corrected, corrections = correct_revenue_totals(rows)

    assert corrected[0][key] == 11_075_000_000.0
    assert corrections == []


def test_correct_revenue_totals_ignores_cost_of_revenue_lines() -> None:
    """ "Total cost of revenues" -- a near-universal COGS-line label -- must never be
    mistaken for a later revenue total merely because its label contains both "total" and
    "revenue" (found as a false-positive shape across 6 other tickers by
    portfolio-financial-analysis's full-universe validation before T-117 shipped)."""
    key = "2021-12-31 (FY)"
    rows = [
        _row("us-gaap_Revenues", "Total revenues", **{key: 3_702_881_000.0}),
        _row(
            "us-gaap_CostOfGoodsAndServicesSold",
            "Total cost of revenues (exclusive of acquired intangible assets amortization "
            "shown separately below)",
            **{key: 1_496_225_000.0},
        ),
    ]

    corrected, corrections = correct_revenue_totals(rows)

    assert corrected[0][key] == 3_702_881_000.0
    assert corrections == []


def test_correct_revenue_totals_drops_the_value_when_between_rows_are_too_large() -> None:
    """When a later, smaller "total"-labeled row is found but the rows between it and the
    first match are too large relative to it to trust as a clean subtraction, the value is
    dropped (`None`) rather than guessed or left at the untrustworthy original -- and the
    drop is itself recorded as a correction with `corrected: None`."""
    key = "2023-12-31 (FY)"
    rows = [
        _row("us-gaap_Revenues", "Total revenues", **{key: 16_558_000_000.0}),
        _row(
            "us-gaap_SomeHugeUnrelatedAdjustment",
            "Some huge unrelated adjustment",
            **{key: 6_000_000_000.0},  # far more than 25% of the later total below
        ),
        _row("apa_RevenuesAndOther", "Total revenues and other", **{key: 8_192_000_000.0}),
    ]

    corrected, corrections = correct_revenue_totals(rows)

    assert corrected[0][key] is None
    assert corrections == [
        {
            "concept": "us-gaap_Revenues",
            "column": key,
            "original": 16_558_000_000.0,
            "corrected": None,
            "rule": "T-118",
        }
    ]


def test_correct_revenue_totals_ignores_dimensional_breakdown_rows() -> None:
    """A dimensional row (a segment/product/equity-investee breakdown, `dimension=True`)
    must never be picked as the Tier 1 total or as the later contradicting candidate --
    only the statement's own non-dimensional rows are structurally meaningful totals."""
    key = "2023-12-31 (FY)"
    rows = [
        _row(
            "us-gaap_Revenues",
            "Kinetik",
            dimension=True,
            **{key: 121_000_000.0},
        ),
        *_apa_fy2023_rows(key),
    ]

    corrected, _corrections = correct_revenue_totals(rows)

    dimensional = next(r for r in corrected if r["label"] == "Kinetik")
    total_row = next(r for r in corrected if r["label"] == "Total revenues")
    assert dimensional[key] == 121_000_000.0  # untouched
    assert total_row[key] == 8_279_000_000.0


def test_correct_revenue_totals_no_total_concept_present_is_a_no_op() -> None:
    rows = [_row("us-gaap_CostOfRevenue", "Total cost of revenue", **{"2023 (FY)": 500.0})]

    corrected, corrections = correct_revenue_totals(rows)
    assert corrected == rows
    assert corrections == []


def test_correct_revenue_totals_does_not_mutate_its_input() -> None:
    key = "2023-12-31 (FY)"
    rows = _apa_fy2023_rows(key)
    original_value = rows[0][key]

    correct_revenue_totals(rows)

    assert rows[0][key] == original_value


# ---------------------------------------------------------------------
# _parse_period_column (T-042)
# ---------------------------------------------------------------------


def test_parse_period_column_instant() -> None:
    assert _parse_period_column("2023-12-31") == ("2023-12-31", None)


def test_parse_period_column_fiscal_year() -> None:
    assert _parse_period_column("2023-12-31 (FY)") == ("2023-12-31", "FY")


def test_parse_period_column_quarter() -> None:
    assert _parse_period_column("2023-12-31 (Q2)") == ("2023-12-31", "Q2")


def test_parse_period_column_ytd() -> None:
    assert _parse_period_column("2023-12-31 (YTD)") == ("2023-12-31", "YTD")


def test_parse_period_column_unparseable_returns_none() -> None:
    assert _parse_period_column("concept") is None
    assert _parse_period_column("Total revenues") is None


# ---------------------------------------------------------------------
# _filed_nondimensional_values (T-042)
# ---------------------------------------------------------------------


def _mock_facts_xbrl(df: pd.DataFrame) -> MagicMock:
    """A minimal `xbrl` mock whose `.facts.query()...to_dataframe()` chain always returns
    *df*, regardless of which filter methods are chained -- good enough for exercising
    `_filed_nondimensional_values`' own logic without depending on edgartools' real
    `FactQuery`."""
    xbrl = MagicMock()
    query = xbrl.facts.query.return_value
    query.by_concept.return_value = query
    query.by_dimension.return_value = query
    query.by_instant_date.return_value = query
    query.by_date_range.return_value = query
    query.to_dataframe.return_value = df
    return xbrl


def test_filed_nondimensional_values_instant_match() -> None:
    xbrl = _mock_facts_xbrl(pd.DataFrame({"value": [15_244_000_000.0]}))
    assert _filed_nondimensional_values(xbrl, "us-gaap_Assets", "2023-12-31", None) == [
        15_244_000_000.0
    ]


def test_filed_nondimensional_values_nothing_filed_is_empty() -> None:
    """APA's real shape: `us-gaap:Revenues` was never filed non-dimensionally at all."""
    xbrl = _mock_facts_xbrl(pd.DataFrame())
    assert _filed_nondimensional_values(xbrl, "us-gaap_Revenues", "2023-12-31", "FY") == []


def test_filed_nondimensional_values_never_falls_back_to_instant_for_duration_columns() -> None:
    """A cash-flow statement's "beginning of period"/"end of period" cash balance is rendered
    under a duration column but genuinely filed as an *instant* fact -- live-verified against
    MSFT's FY2024 10-K (`CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents``). An
    earlier version fell back to an instant lookup at the column's end date to catch this, but
    the "beginning of period" row actually needs the period's *start* date -- the fallback
    silently substituted the wrong endpoint's value (live-verified regression). No fallback:
    a duration lookup finding nothing stays empty, so the caller drops the value rather than
    risk substituting a wrong one."""
    xbrl = MagicMock()
    duration_query = MagicMock()
    duration_query.by_concept.return_value = duration_query
    duration_query.by_dimension.return_value = duration_query
    duration_query.by_date_range.return_value = duration_query
    duration_query.to_dataframe.return_value = pd.DataFrame()
    xbrl.facts.query.return_value = duration_query

    result = _filed_nondimensional_values(
        xbrl,
        "us-gaap_CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "2024-06-30",
        "FY",
    )

    assert result == []
    duration_query.by_instant_date.assert_not_called()


def test_filed_nondimensional_values_disambiguates_same_end_date_by_duration() -> None:
    """A quarter and a fiscal year can share an end date -- only the candidate whose own
    day-span lands in the requested bucket is returned."""
    df = pd.DataFrame(
        {
            "value": [9_737_000_000.0, 2_500_000_000.0],
            "period_start": ["2023-01-01", "2023-10-01"],  # 364 days vs. 91 days
            "period_end": ["2023-12-31", "2023-12-31"],
        }
    )
    xbrl = _mock_facts_xbrl(df)
    assert _filed_nondimensional_values(xbrl, "us-gaap_Revenues", "2023-12-31", "FY") == [
        9_737_000_000.0
    ]
    assert _filed_nondimensional_values(xbrl, "us-gaap_Revenues", "2023-12-31", "Q4") == [
        2_500_000_000.0
    ]


def test_filed_nondimensional_values_ambiguous_returns_every_candidate() -> None:
    """Two distinct facts land in the same bucket for the same concept/end-date -- the
    function itself doesn't pick one; the caller (`reconcile_with_filed_facts`) must treat
    more than one candidate as ambiguous rather than guess."""
    df = pd.DataFrame(
        {
            "value": [100.0, 200.0],
            "period_start": ["2023-01-01", "2023-01-02"],
            "period_end": ["2023-12-31", "2023-12-31"],
        }
    )
    xbrl = _mock_facts_xbrl(df)
    assert _filed_nondimensional_values(xbrl, "us-gaap_Revenues", "2023-12-31", "FY") == [
        100.0,
        200.0,
    ]


# ---------------------------------------------------------------------
# reconcile_with_filed_facts (T-042)
# ---------------------------------------------------------------------


def test_reconcile_with_filed_facts_drops_value_with_no_filed_fact() -> None:
    key = "2023-12-31 (FY)"
    rows = [_row("us-gaap_OperatingIncomeLoss", "Operating earnings", **{key: 999.0})]
    with patch("sec_edgar.agent._filed_nondimensional_values", return_value=[]):
        corrected, corrections = reconcile_with_filed_facts(rows, MagicMock(), "income_statement")

    assert corrected[0][key] is None
    assert corrections == [
        {
            "statement": "income_statement",
            "concept": "us-gaap_OperatingIncomeLoss",
            "column": key,
            "original": 999.0,
            "corrected": None,
            "rule": "T-042",
            "reason": "no_filed_nondimensional_fact",
        }
    ]


def test_reconcile_with_filed_facts_replaces_value_that_differs_from_filed() -> None:
    rows = [_row("us-gaap_Assets", "Total assets", **{"2023-12-31": 500.0})]
    with patch("sec_edgar.agent._filed_nondimensional_values", return_value=[480.0]):
        corrected, corrections = reconcile_with_filed_facts(rows, MagicMock(), "balance_sheet")

    assert corrected[0]["2023-12-31"] == 480.0
    assert corrections == [
        {
            "statement": "balance_sheet",
            "concept": "us-gaap_Assets",
            "column": "2023-12-31",
            "original": 500.0,
            "corrected": 480.0,
            "rule": "T-042",
            "reason": "filed_value_mismatch",
        }
    ]


def test_reconcile_with_filed_facts_leaves_a_matching_value_untouched() -> None:
    rows = [_row("us-gaap_Assets", "Total assets", **{"2023-12-31": 500.0})]
    with patch("sec_edgar.agent._filed_nondimensional_values", return_value=[500.0]):
        corrected, corrections = reconcile_with_filed_facts(rows, MagicMock(), "balance_sheet")

    assert corrected[0]["2023-12-31"] == 500.0
    assert corrections == []


def test_reconcile_with_filed_facts_leaves_a_sign_convention_flip_untouched() -> None:
    """A rendered value's sign can legitimately differ from the raw filed fact's own sign --
    edgartools' presentation layer applies a per-concept convention (contra accounts like
    Treasury Stock, cash-flow decreases, etc.) on top of whatever sign the filer tagged.
    Live-verified against APA: `us-gaap:TreasuryStockCommonValue` is filed as a positive
    5,790,000,000 but correctly rendered as -5,790,000,000. Flipping it back to the filed
    fact's raw sign would be wrong, not a fix -- the same-magnitude-opposite-sign shape the
    full-universe scan explicitly excluded ("after excluding sign-convention flips")."""
    rows = [
        _row(
            "us-gaap_TreasuryStockCommonValue", "Treasury stock", **{"2023-12-31": -5_790_000_000.0}
        )
    ]
    with patch("sec_edgar.agent._filed_nondimensional_values", return_value=[5_790_000_000.0]):
        corrected, corrections = reconcile_with_filed_facts(rows, MagicMock(), "balance_sheet")

    assert corrected[0]["2023-12-31"] == -5_790_000_000.0
    assert corrections == []


def test_reconcile_with_filed_facts_skips_ambiguous_multiple_filed_values() -> None:
    rows = [_row("us-gaap_Assets", "Total assets", **{"2023-12-31": 500.0})]
    with patch("sec_edgar.agent._filed_nondimensional_values", return_value=[480.0, 500.0]):
        corrected, corrections = reconcile_with_filed_facts(rows, MagicMock(), "balance_sheet")

    assert corrected[0]["2023-12-31"] == 500.0  # left alone -- never guess
    assert corrections == []


def test_reconcile_with_filed_facts_respects_skip() -> None:
    """A `(concept, column)` T-118 already corrected must never be re-evaluated here -- doing
    so would find "nothing filed" for revenue again and wipe out T-118's reconstruction."""
    key = "2023-12-31 (FY)"
    rows = [_row("us-gaap_Revenues", "Total revenues", **{key: 8_279_000_000.0})]
    with patch("sec_edgar.agent._filed_nondimensional_values") as mocked:
        corrected, corrections = reconcile_with_filed_facts(
            rows, MagicMock(), "income_statement", skip=frozenset({("us-gaap_Revenues", key)})
        )

    mocked.assert_not_called()
    assert corrected[0][key] == 8_279_000_000.0
    assert corrections == []


def test_reconcile_with_filed_facts_ignores_unparseable_column() -> None:
    rows = [_row("us-gaap_Assets", "Total assets", **{"weird-column": 500.0})]
    with patch("sec_edgar.agent._filed_nondimensional_values") as mocked:
        corrected, corrections = reconcile_with_filed_facts(rows, MagicMock(), "balance_sheet")

    mocked.assert_not_called()
    assert corrected == rows
    assert corrections == []


def test_reconcile_with_filed_facts_skips_dimensional_and_abstract_rows() -> None:
    rows = [
        _row("us-gaap_Revenues", "Kinetik", dimension=True, **{"2023-12-31": 121.0}),
        {**_row("us-gaap_Revenues", "Revenues", **{"2023-12-31": None}), "abstract": True},
    ]
    with patch("sec_edgar.agent._filed_nondimensional_values") as mocked:
        reconcile_with_filed_facts(rows, MagicMock(), "income_statement")

    mocked.assert_not_called()


def test_reconcile_with_filed_facts_does_not_mutate_its_input() -> None:
    rows = [_row("us-gaap_Assets", "Total assets", **{"2023-12-31": 500.0})]
    with patch("sec_edgar.agent._filed_nondimensional_values", return_value=[]):
        reconcile_with_filed_facts(rows, MagicMock(), "balance_sheet")

    assert rows[0]["2023-12-31"] == 500.0


# ---------------------------------------------------------------------
# _safe_reconcile_with_filed_facts (T-042 review, PR #45)
# ---------------------------------------------------------------------


def test_safe_reconcile_with_filed_facts_returns_result_on_success() -> None:
    rows = [_row("us-gaap_Assets", "Total assets", **{"2023-12-31": 500.0})]
    with patch("sec_edgar.agent._filed_nondimensional_values", return_value=[500.0]):
        corrected, corrections, error = _safe_reconcile_with_filed_facts(
            rows, MagicMock(), "balance_sheet"
        )

    assert corrected[0]["2023-12-31"] == 500.0
    assert corrections == []
    assert error is None


def test_safe_reconcile_with_filed_facts_isolates_a_query_failure() -> None:
    """A failure inside `reconcile_with_filed_facts` (e.g. `xbrl.facts.query()` raising for
    an unusual filing) must not propagate -- it's an enhancement layer on top of
    already-successfully-rendered rows, not load-bearing data. Reported in PR #45's review:
    an uncaught exception here previously converted `get_financials`' entire response to
    `success: False`, discarding the other two statements' already-loaded data along with
    it. PR #46's review noted a silent failure would be indistinguishable from "verified,
    nothing to correct" -- the third return value must carry a non-None error message."""
    rows = [_row("us-gaap_Assets", "Total assets", **{"2023-12-31": 500.0})]
    with patch("sec_edgar.agent.reconcile_with_filed_facts", side_effect=RuntimeError("boom")):
        corrected, corrections, error = _safe_reconcile_with_filed_facts(
            rows, MagicMock(), "balance_sheet"
        )

    assert corrected == rows  # returned rendered-but-unvalidated, not discarded
    assert corrections == []
    assert error == "RuntimeError: boom"


# ---------------------------------------------------------------------
# get_financials
# ---------------------------------------------------------------------


def _mock_xbrl_with_frames(
    income: pd.DataFrame, balance: pd.DataFrame, cash_flow: pd.DataFrame
) -> MagicMock:
    xbrl = MagicMock()
    xbrl.statements.income_statement.return_value.to_dataframe.return_value = income
    xbrl.statements.balance_sheet.return_value.to_dataframe.return_value = balance
    xbrl.statements.cashflow_statement.return_value.to_dataframe.return_value = cash_flow
    return xbrl


def test_get_financials_success(agent: EdgarAgent) -> None:
    filing = make_filing(filing_date=date(2023, 3, 15))
    filing.xbrl.return_value = _mock_xbrl_with_frames(
        pd.DataFrame({"line": ["Revenue"], "amount": [1000.0]}),
        pd.DataFrame({"line": ["Assets"], "amount": [5000.0]}),
        pd.DataFrame({"line": ["Operating"], "amount": [200.0]}),
    )
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [filing]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_financials("AAPL", form="10-K", year=2023)

    assert result["success"] is True
    assert result["data"]["income_statement"] == [{"line": "Revenue", "amount": 1000.0}]
    assert result["data"]["balance_sheet"] == [{"line": "Assets", "amount": 5000.0}]
    assert result["data"]["cash_flow"] == [{"line": "Operating", "amount": 200.0}]
    assert result["data"]["corrections"] == []
    assert result["data"]["reconciliation_errors"] == []
    assert result["data"]["cover"] == {"shares_outstanding": [], "error": None}


def test_get_financials_corrects_a_contradicted_revenue_total_end_to_end(
    agent: EdgarAgent,
) -> None:
    """T-118: the income statement edgartools returns is run through
    `correct_revenue_totals` before `get_financials` hands it back -- APA's real FY2023
    shape resolves to the statement's own derived "Total revenues" ($8,279M), not the
    mislabeled $16,558M edgartools' dataframe carries. `reconcile_with_filed_facts` (T-042)
    is patched to a pass-through here so this test stays focused on T-118 in isolation --
    without a filed-facts mock, its other (unrelated) rows would otherwise also be flagged
    as "nothing filed" and dropped; the T-118+T-042 interaction has its own dedicated test
    below."""
    key = "2023-12-31 (FY)"
    filing = make_filing(filing_date=date(2024, 2, 22))
    filing.xbrl.return_value = _mock_xbrl_with_frames(
        pd.DataFrame(_apa_fy2023_rows(key)),
        pd.DataFrame({"line": ["Assets"], "amount": [5000.0]}),
        pd.DataFrame({"line": ["Operating"], "amount": [200.0]}),
    )
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [filing]
    with (
        patch("sec_edgar.agent.Company", return_value=mock_company),
        patch(
            "sec_edgar.agent.reconcile_with_filed_facts",
            side_effect=lambda rows, xbrl, statement, skip=frozenset(): (rows, []),
        ),
    ):
        result = agent.get_financials("APA", form="10-K", year=2024)

    assert result["success"] is True
    total_row = next(
        r for r in result["data"]["income_statement"] if r["concept"] == "us-gaap_Revenues"
    )
    assert total_row[key] == 8_279_000_000.0
    assert result["data"]["corrections"] == [
        {
            "concept": "us-gaap_Revenues",
            "column": key,
            "original": 16_558_000_000.0,
            "corrected": 8_279_000_000.0,
            "rule": "T-118",
            "statement": "income_statement",
        }
    ]


def _mock_xbrl_with_facts(
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cash_flow: pd.DataFrame,
    facts_by_concept: dict[str, pd.DataFrame],
) -> MagicMock:
    """Like `_mock_xbrl_with_frames`, plus a `.facts.query()` chain that resolves to
    *facts_by_concept*'s entry for whatever concept `by_concept` was called with (normalized
    the same way edgartools' own `FactQuery.by_concept` does: underscores to colons) -- an
    empty dataframe (nothing filed) for any concept not given an entry."""
    xbrl = _mock_xbrl_with_frames(income, balance, cash_flow)

    def by_concept(pattern: str, exact: bool = False) -> MagicMock:
        query = MagicMock()
        query.by_dimension.return_value = query
        query.by_instant_date.return_value = query
        query.by_date_range.return_value = query
        query.to_dataframe.return_value = facts_by_concept.get(
            pattern.replace("_", ":"), pd.DataFrame()
        )
        return query

    xbrl.facts.query.return_value.by_concept.side_effect = by_concept
    return xbrl


def test_get_financials_t042_general_check_runs_alongside_t118_end_to_end(
    agent: EdgarAgent,
) -> None:
    """T-042 alongside T-118, all in one `get_financials` call: revenue's label-based
    reconstruction (T-118) survives untouched by the general check; an unrelated income
    concept with no filed non-dimensional fact at all is dropped (T-042); a balance-sheet
    concept whose rendered value matches what was actually filed passes through with no
    correction at all."""
    key = "2023-12-31 (FY)"
    income_rows = [
        _row("us-gaap_Revenues", "Total revenues", **{key: 16_558_000_000.0}),
        _row("apa_RevenuesAndOther", "Total revenues and other", **{key: 8_279_000_000.0}),
        _row("us-gaap_OperatingIncomeLoss", "Operating earnings", **{key: 500_000_000.0}),
    ]
    balance_rows = [_row("us-gaap_Assets", "Total assets", **{"2023-12-31": 480.0})]
    filing = make_filing(filing_date=date(2024, 2, 22))
    filing.xbrl.return_value = _mock_xbrl_with_facts(
        pd.DataFrame(income_rows),
        pd.DataFrame(balance_rows),
        pd.DataFrame(),
        facts_by_concept={"us-gaap:Assets": pd.DataFrame({"value": [480.0]})},
    )
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [filing]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_financials("APA", form="10-K", year=2024)

    assert result["success"] is True
    income = result["data"]["income_statement"]
    revenue_row = next(r for r in income if r["concept"] == "us-gaap_Revenues")
    operating_row = next(r for r in income if r["concept"] == "us-gaap_OperatingIncomeLoss")
    assert revenue_row[key] == 8_279_000_000.0  # T-118's reconstruction, untouched by T-042
    assert operating_row[key] is None  # T-042: no filed non-dimensional fact -> dropped

    balance_sheet = result["data"]["balance_sheet"]
    assert balance_sheet[0]["2023-12-31"] == 480.0  # matches what was filed -> untouched

    corrections = result["data"]["corrections"]
    by_concept_and_rule = {(c["concept"], c["rule"]): c for c in corrections}
    assert by_concept_and_rule[("us-gaap_Revenues", "T-118")]["statement"] == "income_statement"
    operating_correction = by_concept_and_rule[("us-gaap_OperatingIncomeLoss", "T-042")]
    assert operating_correction["statement"] == "income_statement"
    assert operating_correction["reason"] == "no_filed_nondimensional_fact"
    assert not any(c["concept"] == "us-gaap_Assets" for c in corrections)


def test_get_financials_survives_a_t042_reconciliation_failure_on_one_statement(
    agent: EdgarAgent,
) -> None:
    """PR #45 review: `reconcile_with_filed_facts` raising for one statement (a live
    `xbrl.facts.query()` call, can fail for reasons outside this module's control) must not
    turn the whole `get_financials` response into `success: False` and discard the other
    two statements' already-successfully-rendered data. `balance_sheet`'s reconciliation is
    made to fail here; `income_statement` (T-118 still applies) and `cash_flow` must come
    back normally."""
    key = "2023-12-31 (FY)"
    income_rows = [
        _row("us-gaap_Revenues", "Total revenues", **{key: 16_558_000_000.0}),
        _row("apa_RevenuesAndOther", "Total revenues and other", **{key: 8_279_000_000.0}),
    ]
    balance_rows = [_row("us-gaap_Assets", "Total assets", **{"2023-12-31": 480.0})]
    cash_flow_rows = [
        _row(
            "us-gaap_NetCashProvidedByUsedInOperatingActivities",
            "Operating cash flow",
            **{key: 100.0},
        )
    ]
    filing = make_filing(filing_date=date(2024, 2, 22))
    filing.xbrl.return_value = _mock_xbrl_with_facts(
        pd.DataFrame(income_rows),
        pd.DataFrame(balance_rows),
        pd.DataFrame(cash_flow_rows),
        facts_by_concept={
            "us-gaap:NetCashProvidedByUsedInOperatingActivities": pd.DataFrame(
                {"value": [100.0], "period_start": ["2023-01-01"], "period_end": ["2023-12-31"]}
            )
        },
    )
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [filing]

    real_reconcile = reconcile_with_filed_facts

    def fail_only_for_balance_sheet(rows, xbrl, statement, skip=frozenset()):
        if statement == "balance_sheet":
            raise RuntimeError("simulated xbrl.facts.query() failure")
        return real_reconcile(rows, xbrl, statement, skip=skip)

    with (
        patch("sec_edgar.agent.Company", return_value=mock_company),
        patch(
            "sec_edgar.agent.reconcile_with_filed_facts",
            side_effect=fail_only_for_balance_sheet,
        ),
    ):
        result = agent.get_financials("APA", form="10-K", year=2024)

    assert result["success"] is True
    income = result["data"]["income_statement"]
    revenue_row = next(r for r in income if r["concept"] == "us-gaap_Revenues")
    assert revenue_row[key] == 8_279_000_000.0  # T-118 still applied

    # balance_sheet's reconciliation failed -- rendered rows come back unvalidated, not lost
    assert result["data"]["balance_sheet"] == [
        {
            "concept": "us-gaap_Assets",
            "label": "Total assets",
            "2023-12-31": 480.0,
            "standard_concept": None,
            "abstract": False,
            "dimension": False,
            "is_breakdown": False,
        }
    ]

    cash_flow = result["data"]["cash_flow"]
    assert cash_flow[0]["2023-12-31 (FY)"] == 100.0  # cash_flow's own reconciliation unaffected

    assert not any(c["statement"] == "balance_sheet" for c in result["data"]["corrections"])

    # PR #46 review: the failure must be visible, not indistinguishable from "nothing to
    # correct" -- only balance_sheet's reconciliation failed.
    assert result["data"]["reconciliation_errors"] == [
        {
            "statement": "balance_sheet",
            "error": "RuntimeError: simulated xbrl.facts.query() failure",
        }
    ]


def test_get_financials_multiple_matches_without_accession_number_returns_error(
    agent: EdgarAgent,
) -> None:
    q1 = make_filing(form="10-Q", filing_date=date(2023, 5, 1), accession_number="acc-q1")
    q2 = make_filing(form="10-Q", filing_date=date(2023, 8, 1), accession_number="acc-q2")
    q3 = make_filing(form="10-Q", filing_date=date(2023, 11, 1), accession_number="acc-q3")
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [q3, q2, q1]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_financials("AAPL", form="10-Q", year=2023)

    assert result["success"] is False
    assert "3" in result["error"]
    assert "acc-q1" in result["error"]
    assert "acc-q2" in result["error"]
    assert "acc-q3" in result["error"]
    for f in (q1, q2, q3):
        f.xbrl.assert_not_called()


def test_get_financials_multiple_matches_with_accession_number_selects_correct_filing(
    agent: EdgarAgent,
) -> None:
    q1 = make_filing(form="10-Q", filing_date=date(2023, 5, 1), accession_number="acc-q1")
    q1.xbrl.return_value = _mock_xbrl_with_frames(
        pd.DataFrame({"line": ["Revenue"], "amount": [111.0]}),
        pd.DataFrame({"line": ["Assets"], "amount": [111.0]}),
        pd.DataFrame({"line": ["Operating"], "amount": [111.0]}),
    )
    q2 = make_filing(form="10-Q", filing_date=date(2023, 8, 1), accession_number="acc-q2")
    q2.xbrl.return_value = _mock_xbrl_with_frames(
        pd.DataFrame({"line": ["Revenue"], "amount": [222.0]}),
        pd.DataFrame({"line": ["Assets"], "amount": [222.0]}),
        pd.DataFrame({"line": ["Operating"], "amount": [222.0]}),
    )
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [q2, q1]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_financials("AAPL", form="10-Q", year=2023, accession_number="acc-q1")

    assert result["success"] is True
    assert result["data"]["income_statement"] == [{"line": "Revenue", "amount": 111.0}]
    q1.xbrl.assert_called_once()
    q2.xbrl.assert_not_called()


def test_get_financials_accession_number_not_among_matches_returns_error(
    agent: EdgarAgent,
) -> None:
    q1 = make_filing(form="10-Q", filing_date=date(2023, 5, 1), accession_number="acc-q1")
    q2 = make_filing(form="10-Q", filing_date=date(2023, 8, 1), accession_number="acc-q2")
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [q2, q1]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_financials(
            "AAPL", form="10-Q", year=2023, accession_number="does-not-exist"
        )

    assert result["success"] is False
    assert "does-not-exist" in result["error"]
    assert "acc-q1" in result["error"]
    assert "acc-q2" in result["error"]


def test_get_financials_no_filing_for_year_returns_error(agent: EdgarAgent) -> None:
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [make_filing(filing_date=date(2022, 3, 15))]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_financials("AAPL", form="10-K", year=2023)

    assert result["success"] is False
    assert "2023" in result["error"]


def test_get_financials_no_xbrl_data_returns_error(agent: EdgarAgent) -> None:
    filing = make_filing(filing_date=date(2023, 3, 15))
    filing.xbrl.return_value = None
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [filing]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_financials("AAPL", form="10-K", year=2023)

    assert result["success"] is False
    assert "no XBRL" in result["error"]


def test_get_financials_failure_returns_error_dict(agent: EdgarAgent) -> None:
    with patch("sec_edgar.agent.Company", side_effect=Exception("boom")):
        result = agent.get_financials("AAPL", form="10-K", year=2023)

    assert result["success"] is False


# ---------------------------------------------------------------------
# cover_shares_outstanding (T-043)
# ---------------------------------------------------------------------

_COVER_FIXTURES = Path(__file__).parent / "fixtures" / "cover_shares_facts.json"
_STATEMENT_FRAMES = (
    pd.DataFrame({"line": ["Revenue"], "amount": [1000.0]}),
    pd.DataFrame({"line": ["Assets"], "amount": [5000.0]}),
    pd.DataFrame({"line": ["Operating"], "amount": [200.0]}),
)


def _captured_cover_facts(key: str) -> list[dict]:
    """Real ``xbrl.facts.query().by_concept("dei:EntityCommonStockSharesOutstanding").execute()``
    rows, captured live from the named filing (``<TICKER>_<FORM>``, latest as of 2026-10-02) --
    see ``fixtures/cover_shares_facts.json``'s ``accession_number``/``filing_date`` per key."""
    return json.loads(_COVER_FIXTURES.read_text())[key]["facts"]


def _cover_xbrl(facts: list[dict]) -> MagicMock:
    """A minimal `xbrl` whose cover-share query resolves to *facts*."""
    xbrl = MagicMock()
    xbrl.facts.query.return_value.by_concept.return_value.execute.return_value = facts
    return xbrl


def _fact(
    value: float | str | None,
    instant: str | None = "2026-01-28",
    member: str | None = None,
    **extra: object,
) -> dict:
    """A synthetic fact row in edgartools' ``execute()`` shape (only what the code reads)."""
    fact: dict = {"numeric_value": value, "value": value, "period_instant": instant}
    if member is not None:
        fact["dim_us-gaap_StatementClassOfStockAxis"] = member
    fact.update(extra)
    return fact


def test_cover_shares_outstanding_single_class_real_filing() -> None:
    """PG's 10-K (captured live): ~2.32B outstanding as of the cover-page date, which is
    *after* the 2026-06-30 fiscal year end -- returned exactly, not replaced with it."""
    result = cover_shares_outstanding(_cover_xbrl(_captured_cover_facts("PG_10-K")))
    assert result == [{"value": 2324433060, "as_of_date": "2026-07-31", "class_member": None}]


def test_cover_shares_outstanding_applies_to_a_10_q_alike() -> None:
    result = cover_shares_outstanding(_cover_xbrl(_captured_cover_facts("PG_10-Q")))
    assert result == [{"value": 2328598978, "as_of_date": "2026-03-31", "class_member": None}]


def test_cover_shares_outstanding_multi_class_returns_one_entry_per_class_and_no_total() -> None:
    """GOOGL's 10-K (captured live): three classes, all dimensional -- no total was filed,
    so none is returned (and certainly none summed)."""
    result = cover_shares_outstanding(_cover_xbrl(_captured_cover_facts("GOOGL_10-K")))
    # sorted by member name, deterministically (not the filing's own fact order)
    assert result == [
        {
            "value": 5438000000,
            "as_of_date": "2026-01-28",
            "class_member": "goog:CapitalClassCMember",
        },
        {
            "value": 5822000000,
            "as_of_date": "2026-01-28",
            "class_member": "us-gaap:CommonClassAMember",
        },
        {
            "value": 837000000,
            "as_of_date": "2026-01-28",
            "class_member": "us-gaap:CommonClassBMember",
        },
    ]
    assert all(e["class_member"] is not None for e in result)
    assert 5822000000 + 837000000 + 5438000000 not in [e["value"] for e in result]


def test_cover_shares_outstanding_two_class_10_q_real_filing() -> None:
    result = cover_shares_outstanding(_cover_xbrl(_captured_cover_facts("BRK-B_10-Q")))
    assert result == [
        {"value": 488450, "as_of_date": "2026-07-29", "class_member": "us-gaap:CommonClassAMember"},
        {
            "value": 1408035161,
            "as_of_date": "2026-07-29",
            "class_member": "us-gaap:CommonClassBMember",
        },
    ]


def test_cover_shares_outstanding_excludes_a_co_registrants_legal_entity_fact() -> None:
    """NEE's 10-K (captured live) also carries 1,000 shares under ``dei:LegalEntityAxis`` for
    Florida Power & Light, a co-registrant -- not a NEE share class, so it must not appear."""
    facts = _captured_cover_facts("NEE_10-K")
    assert any(f.get("dim_dei_LegalEntityAxis") for f in facts)  # fixture still has the trap
    assert cover_shares_outstanding(_cover_xbrl(facts)) == [
        {"value": 2083521964, "as_of_date": "2026-01-31", "class_member": None}
    ]


def test_cover_shares_outstanding_keeps_a_filed_total_alongside_the_classes() -> None:
    """Synthetic: a filer that really filed a non-dimensional total *and* per-class facts gets
    all of them, the total first -- and the total is the filed one, not a sum."""
    facts = [
        _fact(30, member="us-gaap:CommonClassBMember"),
        _fact(100),
        _fact(70, member="us-gaap:CommonClassAMember"),
    ]
    assert cover_shares_outstanding(_cover_xbrl(facts)) == [
        {"value": 100, "as_of_date": "2026-01-28", "class_member": None},
        {"value": 70, "as_of_date": "2026-01-28", "class_member": "us-gaap:CommonClassAMember"},
        {"value": 30, "as_of_date": "2026-01-28", "class_member": "us-gaap:CommonClassBMember"},
    ]


def test_cover_shares_outstanding_missing_is_an_empty_list() -> None:
    assert cover_shares_outstanding(_cover_xbrl([])) == []


def test_cover_shares_outstanding_reads_only_the_dei_outstanding_concept() -> None:
    """Never `CommonStockSharesIssued`/weighted averages as a substitute (PG: ~4.0B issued)."""
    xbrl = _cover_xbrl([])
    cover_shares_outstanding(xbrl)
    xbrl.facts.query.return_value.by_concept.assert_called_once_with(
        "dei:EntityCommonStockSharesOutstanding", exact=True
    )


def test_cover_shares_outstanding_skips_facts_it_cannot_trust() -> None:
    facts = [
        _fact(None),  # no value at all
        _fact("not-a-number"),
        _fact(float("nan")),
        _fact(-5),
        _fact(10, instant=None),  # no instant date -> never fall back to a guess
        _fact(10, instant="garbage"),
        _fact(10, member=""),  # a dimension with no member
        _fact(10, member="us-gaap:CommonClassAMember", **{"dim_us-gaap_OtherAxis": "x:Member"}),
        _fact(10, member="us-gaap:CommonClassAMember", **{"dim_dei_LegalEntityAxis": "x:Member"}),
        _fact(42, instant="2026-01-28T00:00:00"),  # the one good fact (ISO datetime trimmed)
    ]
    assert cover_shares_outstanding(_cover_xbrl(facts)) == [
        {"value": 42, "as_of_date": "2026-01-28", "class_member": None}
    ]


def test_cover_shares_outstanding_collapses_exact_duplicate_facts() -> None:
    facts = [_fact(7, member="us-gaap:CommonClassAMember")] * 2
    assert len(cover_shares_outstanding(_cover_xbrl(facts))) == 1


def test_safe_cover_shares_outstanding_returns_result_on_success() -> None:
    entries, error = _safe_cover_shares_outstanding(_cover_xbrl(_captured_cover_facts("PG_10-K")))
    assert error is None
    assert entries[0]["value"] == 2324433060


def test_safe_cover_shares_outstanding_isolates_a_read_failure() -> None:
    xbrl = MagicMock()
    xbrl.facts.query.side_effect = RuntimeError("simulated xbrl.facts.query() failure")
    assert _safe_cover_shares_outstanding(xbrl) == (
        [],
        "RuntimeError: simulated xbrl.facts.query() failure",
    )


def _financials_for(agent: EdgarAgent, xbrl: MagicMock, form: str = "10-K") -> dict:
    filing = make_filing(form=form, filing_date=date(2026, 8, 4))
    filing.xbrl.return_value = xbrl
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [filing]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        return agent.get_financials("PG", form=form, year=2026)


def test_get_financials_exposes_cover_shares_outstanding_end_to_end(agent: EdgarAgent) -> None:
    """Additive: the three statements, ``corrections`` and ``reconciliation_errors`` are
    exactly what they were without the new field."""
    xbrl = _cover_xbrl(_captured_cover_facts("GOOGL_10-K"))
    xbrl.statements.income_statement.return_value.to_dataframe.return_value = _STATEMENT_FRAMES[0]
    xbrl.statements.balance_sheet.return_value.to_dataframe.return_value = _STATEMENT_FRAMES[1]
    xbrl.statements.cashflow_statement.return_value.to_dataframe.return_value = _STATEMENT_FRAMES[2]
    result = _financials_for(agent, xbrl)

    assert result["success"] is True
    data = result["data"]
    assert [e["class_member"] for e in data["cover"]["shares_outstanding"]] == [
        "goog:CapitalClassCMember",
        "us-gaap:CommonClassAMember",
        "us-gaap:CommonClassBMember",
    ]
    assert data["income_statement"] == [{"line": "Revenue", "amount": 1000.0}]
    assert data["balance_sheet"] == [{"line": "Assets", "amount": 5000.0}]
    assert data["cash_flow"] == [{"line": "Operating", "amount": 200.0}]
    assert data["corrections"] == []
    assert data["reconciliation_errors"] == []


def test_get_financials_cover_is_empty_when_the_filer_filed_none(agent: EdgarAgent) -> None:
    xbrl = _cover_xbrl([])
    xbrl.statements.income_statement.return_value.to_dataframe.return_value = _STATEMENT_FRAMES[0]
    xbrl.statements.balance_sheet.return_value.to_dataframe.return_value = _STATEMENT_FRAMES[1]
    xbrl.statements.cashflow_statement.return_value.to_dataframe.return_value = _STATEMENT_FRAMES[2]
    result = _financials_for(agent, xbrl, form="10-Q")

    assert result["success"] is True
    assert result["data"]["cover"] == {"shares_outstanding": [], "error": None}
    assert result["data"]["reconciliation_errors"] == []  # filed none != failed to read


def test_get_financials_survives_a_cover_read_failure(agent: EdgarAgent) -> None:
    """Failure isolation (same as PR #46): the cover read raising returns ``cover`` empty plus
    ``cover.error`` -- the statements are intact and the call still succeeds. The failure must
    *not* land in ``reconciliation_errors`` (PR #48 review): downstream rejects a whole filing
    when that list is non-empty, and a cover failure leaves every statement valid."""
    xbrl = _mock_xbrl_with_frames(*_STATEMENT_FRAMES)
    xbrl.facts.query.return_value.by_concept.side_effect = RuntimeError("simulated cover failure")
    result = _financials_for(agent, xbrl)

    assert result["success"] is True
    data = result["data"]
    assert data["cover"] == {
        "shares_outstanding": [],
        "error": "RuntimeError: simulated cover failure",
    }
    assert data["reconciliation_errors"] == []
    assert data["income_statement"] == [{"line": "Revenue", "amount": 1000.0}]
    assert data["balance_sheet"] == [{"line": "Assets", "amount": 5000.0}]
    assert data["cash_flow"] == [{"line": "Operating", "amount": 200.0}]


# ---------------------------------------------------------------------
# list_years_available
# ---------------------------------------------------------------------


def test_list_years_available_returns_sorted_unique_years(agent: EdgarAgent) -> None:
    filings = [
        make_filing(filing_date=date(2022, 3, 1)),
        make_filing(filing_date=date(2020, 3, 1)),
        make_filing(filing_date=date(2022, 6, 1)),  # duplicate year
        make_filing(filing_date=date(2021, 3, 1)),
    ]
    mock_company = MagicMock()
    mock_company.get_filings.return_value = filings
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.list_years_available("AAPL", form="10-K")

    assert result == {"success": True, "data": [2020, 2021, 2022]}


def test_list_years_available_failure_returns_error_dict(agent: EdgarAgent) -> None:
    with patch("sec_edgar.agent.Company", side_effect=Exception("boom")):
        result = agent.list_years_available("AAPL", form="10-K")

    assert result["success"] is False


# ---------------------------------------------------------------------
# search_filings
# ---------------------------------------------------------------------


def test_search_filings_finds_case_insensitive_match(agent: EdgarAgent) -> None:
    matching = make_filing()
    matching.text.return_value = "This filing discusses Climate Risk extensively."
    non_matching = make_filing()
    non_matching.text.return_value = "Nothing relevant here."
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [matching, non_matching]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.search_filings("AAPL", keyword="climate risk", form="10-K")

    assert result["success"] is True
    assert len(result["data"]) == 1
    assert result["data"][0]["accession_number"] == matching.accession_number


def test_search_filings_empty_result_is_not_an_error(agent: EdgarAgent) -> None:
    filing = make_filing()
    filing.text.return_value = "Nothing relevant here."
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [filing]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.search_filings("AAPL", keyword="nonexistent phrase")

    assert result == {"success": True, "data": []}


def test_search_filings_skips_filing_whose_text_raises(agent: EdgarAgent) -> None:
    broken = make_filing()
    broken.text.side_effect = Exception("could not fetch text")
    good = make_filing()
    good.text.return_value = "climate change discussion"
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [broken, good]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.search_filings("AAPL", keyword="climate")

    assert result["success"] is True
    assert len(result["data"]) == 1
    assert result["data"][0]["accession_number"] == good.accession_number


def test_search_filings_respects_max_filings_to_search(agent: EdgarAgent) -> None:
    filings = [make_filing() for _ in range(5)]
    for f in filings:
        f.text.return_value = "climate"
    mock_company = MagicMock()
    mock_company.get_filings.return_value = filings
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.search_filings("AAPL", keyword="climate", max_filings_to_search=2)

    assert len(result["data"]) == 2


def test_search_filings_failure_returns_error_dict(agent: EdgarAgent) -> None:
    with patch("sec_edgar.agent.Company", side_effect=Exception("boom")):
        result = agent.search_filings("AAPL", keyword="climate")

    assert result["success"] is False
