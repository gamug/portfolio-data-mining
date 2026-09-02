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

from sec_edgar.agent import EdgarAgent


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
    assert result["data"]["filing_date"] == "2023-03-15"


def test_get_filing_by_year_no_match_returns_error(agent: EdgarAgent) -> None:
    mock_company = MagicMock()
    mock_company.get_filings.return_value = [make_filing(filing_date=date(2022, 3, 15))]
    with patch("sec_edgar.agent.Company", return_value=mock_company):
        result = agent.get_filing_by_year("AAPL", form="10-K", year=1800)

    assert result["success"] is False
    assert "1800" in result["error"]


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
