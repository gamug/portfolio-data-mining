"""
edgar_agent_examples.py

Runnable examples showing how an agent (or a human) should call EdgarAgent.
Every call returns a dict -- always check "success" before touching "data".

Run with:  NAME="Jane Doe" EMAIL="jane@example.com" python edgar_agent_examples.py
(SEC requires a real identity string; set NAME/EMAIL env vars or pass them
directly to EdgarAgent(name=..., email=...))
"""

import json
from edgar.agent import EdgarAgent


def pretty(label, result):
    print(f"\n--- {label} ---")
    print(json.dumps(result, indent=2, default=str)[:1500])  # truncate long output


agent = EdgarAgent(name="Jane Doe", email="jane@example.com")


# ---------------------------------------------------------------------
# 1. Confirm a company/ticker before doing anything else
# ---------------------------------------------------------------------
result = agent.get_company_info("AAPL")
pretty("get_company_info('AAPL')", result)
if result["success"]:
    print(f"Resolved to: {result['data']['name']} (CIK {result['data']['cik']})")


# ---------------------------------------------------------------------
# 2. List recent filings, optionally filtered by form type
# ---------------------------------------------------------------------
result = agent.get_filings("AAPL", form="10-K", limit=5)
pretty("get_filings('AAPL', form='10-K', limit=5)", result)


# ---------------------------------------------------------------------
# 3. Check which years actually have data before requesting a specific one
# ---------------------------------------------------------------------
result = agent.list_years_available("AAPL", form="10-K")
pretty("list_years_available('AAPL', '10-K')", result)
if result["success"] and result["data"]:
    latest_year = max(result["data"])
else:
    latest_year = 2023  # fallback


# ---------------------------------------------------------------------
# 4. Get a specific filing's metadata by year
# ---------------------------------------------------------------------
result = agent.get_filing_by_year("AAPL", form="10-K", year=latest_year)
pretty(f"get_filing_by_year('AAPL', '10-K', {latest_year})", result)

# Handle the "not found" case explicitly -- this is a normal outcome, not a crash
result_missing = agent.get_filing_by_year("AAPL", form="10-K", year=1800)
pretty("get_filing_by_year('AAPL', '10-K', 1800)  # deliberately invalid", result_missing)
if not result_missing["success"]:
    print(f"Handled gracefully: {result_missing['error']}")


# ---------------------------------------------------------------------
# 5. Get the most recent filing of a type without knowing the year
# ---------------------------------------------------------------------
result = agent.get_latest_filing("AAPL", form="8-K")
pretty("get_latest_filing('AAPL', '8-K')", result)


# ---------------------------------------------------------------------
# 6. Pull financial statements for a given year
# ---------------------------------------------------------------------
result = agent.get_financials("AAPL", form="10-K", year=latest_year)
pretty(f"get_financials('AAPL', '10-K', {latest_year})", result)
if result["success"]:
    income_rows = result["data"]["income_statement"]
    print(f"Income statement has {len(income_rows)} line items")


# ---------------------------------------------------------------------
# 7. Full-text search across recent filings (bounded, so it stays fast)
# ---------------------------------------------------------------------
result = agent.search_filings(
    "AAPL",
    keyword="climate",
    form="10-K",
    max_filings_to_search=5,
)
pretty("search_filings('AAPL', 'climate', form='10-K', max_filings_to_search=5)", result)
if result["success"]:
    if result["data"]:
        for match in result["data"]:
            print(f"  Found in {match['form']} filed {match['filing_date']} "
                  f"(accession {match['accession_number']})")
    else:
        print("  No matches found (this is a normal result, not an error).")


# ---------------------------------------------------------------------
# 8. Example of a bad ticker -- shows the error path an agent should expect
# ---------------------------------------------------------------------
result = agent.get_company_info("NOT_A_REAL_TICKER_XYZ")
pretty("get_company_info('NOT_A_REAL_TICKER_XYZ')", result)
if not result["success"]:
    print(f"Agent should relay this to the user: {result['error']}")