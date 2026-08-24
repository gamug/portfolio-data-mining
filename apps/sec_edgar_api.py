"""
FastAPI application entry point for SEC EDGAR filings/financials — the
second of the two modules split out of finhub's original combined app.py
(the other is apps/pricing_api.py). Source: finhub/app.py, split by tag.
See docs/modules/edgar.md.

Named sec_edgar (not edgar) deliberately: the underlying `edgartools`
library is imported as `edgar` (see src/sec_edgar/agent.py), and a local
package literally named `edgar` would shadow it on sys.path.

Run:
    .venv\\Scripts\\python.exe apps\\sec_edgar_api.py
    -> http://127.0.0.1:8005/docs
"""

import os
import sys
from enum import StrEnum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse

from sec_edgar.agent import EdgarAgent

load_dotenv()  # populate os.environ from .env before anything reads it

NAME = os.environ.get("NAME", "Your Name")
EMAIL = os.environ.get("EMAIL", "your.email@example.com")


class FilingForm(StrEnum):
    ten_k = "10-K"
    ten_q = "10-Q"
    eight_k = "8-K"
    s1 = "S-1"


app = FastAPI(
    title="Portfolio Data Mining — SEC EDGAR API",
    version="1.0.0",
    openapi_tags=[
        {"name": "Edgar", "description": "SEC Edgar filings and financial statements"},
    ],
    swagger_ui_parameters={"docExpansion": "none"},
)

edgar_agent = EdgarAgent(name=NAME, email=EMAIL)


@app.get("/")
def welcome() -> RedirectResponse:
    """Redirect root URL to the interactive docs."""
    return RedirectResponse(url="/docs")


@app.get("/edgar/company_info/{ticker}", tags=["Edgar"])
def edgar_company_info(ticker: str) -> dict:
    return edgar_agent.get_company_info(ticker)


@app.get("/edgar/filings/{ticker}", tags=["Edgar"])
def edgar_filings(ticker: str, form: FilingForm, limit: int = 5) -> dict:
    return edgar_agent.get_filings(ticker, form=form.value, limit=limit)


@app.get("/edgar/years_available/{ticker}", tags=["Edgar"])
def edgar_years_available(ticker: str, form: FilingForm) -> dict:
    return edgar_agent.list_years_available(ticker, form=form.value)


@app.get("/edgar/filing_by_year/{ticker}", tags=["Edgar"])
def edgar_filing_by_year(ticker: str, form: FilingForm, year: int) -> dict:
    return edgar_agent.get_filing_by_year(ticker, form=form.value, year=year)


@app.get("/edgar/latest_filing/{ticker}", tags=["Edgar"])
def edgar_latest_filing(ticker: str, form: FilingForm) -> dict:
    return edgar_agent.get_latest_filing(ticker, form=form.value)


@app.get("/edgar/financials/{ticker}", tags=["Edgar"])
def edgar_financials(ticker: str, form: FilingForm, year: int) -> JSONResponse:
    result = edgar_agent.get_financials(ticker, form=form.value, year=year)
    return JSONResponse(content=jsonable_encoder(result))


@app.get("/edgar/search_filings/{ticker}", tags=["Edgar"])
def edgar_search_filings(
    ticker: str,
    keyword: str,
    form: FilingForm,
    max_filings_to_search: int = 5,
) -> dict:
    return edgar_agent.search_filings(
        ticker,
        keyword=keyword,
        form=form.value,
        max_filings_to_search=max_filings_to_search,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8005, reload=False)
