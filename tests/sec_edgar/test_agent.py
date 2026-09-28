"""Tests for EdgarAgent, the SEC EDGAR filings/financials tool wrapper.

All calls into the third-party `edgar` (edgartools) library are mocked --
these tests never hit the network. Every public method is expected to
return {"success": True, "data": ...} or {"success": False, "error": ...}
and never raise, so most tests assert on that shape directly.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from sec_edgar.agent import EdgarAgent, correct_revenue_totals


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


def test_get_financials_corrects_a_contradicted_revenue_total_end_to_end(
    agent: EdgarAgent,
) -> None:
    """T-118: the income statement edgartools returns is run through
    `correct_revenue_totals` before `get_financials` hands it back -- APA's real FY2023
    shape resolves to the statement's own derived "Total revenues" ($8,279M), not the
    mislabeled $16,558M edgartools' dataframe carries."""
    key = "2023-12-31 (FY)"
    filing = make_filing(filing_date=date(2024, 2, 22))
    filing.xbrl.return_value = _mock_xbrl_with_frames(
        pd.DataFrame(_apa_fy2023_rows(key)),
        pd.DataFrame({"line": ["Assets"], "amount": [5000.0]}),
        pd.DataFrame({"line": ["Operating"], "amount": [200.0]}),
    )
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [filing]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
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
