# app.py
import os
import pandas as pd
from fastapi import FastAPI, Query
from fastapi.responses import RedirectResponse
from typing import List, Optional

from src.config import general
from src.news.gdelt_collector import GDELTNewsFetcher

app = FastAPI(title="GDELT News API")

# ---------------------------------------------------------------------------
# Welcome page redirect
# ---------------------------------------------------------------------------
@app.get("/")
def welcome():
    """Redirect root URL to the interactive docs."""
    return RedirectResponse(url="/docs")

# ---------------------------------------------------------------------------
# Fetch news endpoint
# ---------------------------------------------------------------------------
@app.get("/fetch_news")
def gdelt_fetch_news(
    start_date: str = Query("2020-01-01", description="Start date in YYYY-MM-DD format"),
    end_date: str = Query("2025-12-31", description="End date in YYYY-MM-DD format"),
    window_days: int = Query(1, description="Window size in days"),
    sleep_between_calls: float = Query(3.0, description="Sleep time between API calls")
):
    """
    Fetch GDELT news for given companies and date range.
    If no companies are provided, defaults to S&P500 list from input path.
    """

    companies = pd.read_csv(
        os.path.join(general["paths"]["input"], "s&p500.csv"),
        sep=";"
    )["Security"].tolist()

    fetcher = GDELTNewsFetcher(sleep_between_calls=sleep_between_calls)

    output_path = os.path.join(general["paths"]["news_links"], "gdelt_news_full.csv")

    fetcher.fetch_rolling_range(
        tickers_or_queries=companies,
        start_date=start_date,
        end_date=end_date,
        window_days=window_days,
        checkpoint_csv=output_path
    )

    return {
        "message": "Fetch completed",
        "companies_count": len(companies),
        "output_file": output_path
    }