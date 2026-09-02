"""
FastAPI application entry point for stock pricing/market data — one of the
two modules split out of finhub's original combined app.py (the other is
apps/sec_edgar_api.py). Source: finhub/app.py, split by tag. See
docs/modules/pricing.md.

Endpoints, grouped by tag:
- Universe:     the tracked S&P 500 ticker/company universe.
- Pricing:      daily stock price history (Finnhub, falls back to yfinance
                on the free tier's lack of historical candles).
- News:         Finnhub company news, general market news, news sentiment.
- Market Data:  Finnhub company profile, peers, basic financials.

Run:
    .venv\\Scripts\\python.exe apps\\pricing_api.py
    -> http://127.0.0.1:8004/docs
"""

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse

from common.errors import UpstreamDataError
from common.portfolio import list_universe, resolve_symbol
from pricing.fetcher import StockPriceFetcher
from pricing.market_data import MarketDataClient
from pricing.news import FinnhubNewsFetcher

load_dotenv()  # populate os.environ from .env before anything reads it

# Fail fast at startup if the key is missing, rather than lazily on first
# request with a useless "YOUR_API_KEY" placeholder.
FINNHUB_API_KEY = os.environ["FINNHUB_API_KEY"]


app = FastAPI(
    title="Portfolio Data Mining — Pricing API",
    version="1.0.0",
    openapi_tags=[
        {"name": "Universe", "description": "Tracked S&P 500 ticker/company universe"},
        {
            "name": "Pricing",
            "description": "Daily stock price history (Finnhub, falls back to yfinance)",
        },
        {
            "name": "News",
            "description": "Finnhub company news, general market news, and news sentiment",
        },
        {
            "name": "Market Data",
            "description": "Finnhub company profile, peers, and basic financials",
        },
    ],
    swagger_ui_parameters={"docExpansion": "none"},
)

news_fetcher = FinnhubNewsFetcher(api_key=FINNHUB_API_KEY)
price_fetcher = StockPriceFetcher(finnhub_api_key=FINNHUB_API_KEY)
market_client = MarketDataClient(finnhub_api_key=FINNHUB_API_KEY)


@app.exception_handler(UpstreamDataError)
async def upstream_error_handler(request: Request, exc: UpstreamDataError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "provider": exc.provider, "error": exc.message},
    )


@app.get("/")
def welcome() -> RedirectResponse:
    """Redirect root URL to the interactive docs."""
    return RedirectResponse(url="/docs")


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------
@app.get("/universe", tags=["Universe"])
def universe(
    sector: str | None = Query(
        None, description="Filter by GICS Sector, e.g. 'Information Technology'"
    ),
    as_of: date | None = Query(
        None,
        description="Point-in-time membership date, YYYY-MM-DD; omit for today's "
        "live/cached snapshot. Requires `universe-backfill` to have been run once.",
    ),
) -> list[dict]:
    """List the tracked S&P 500 ticker/company universe, optionally filtered by
    sector and/or as of a past date."""
    try:
        return list_universe(sector=sector, as_of=as_of)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/universe/resolve/{query}", tags=["Universe"])
def universe_resolve(
    query: str,
    as_of: date | None = Query(
        None, description="Point-in-time membership date, YYYY-MM-DD; omit for today's snapshot."
    ),
) -> dict:
    """Resolve a ticker symbol or company name to its canonical universe row,
    optionally as of a past date."""
    try:
        row = resolve_symbol(query, as_of=as_of)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if row is None:
        raise HTTPException(status_code=404, detail=f"'{query}' not found in tracked universe.")
    return row


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
@app.get("/pricing/{ticker}", tags=["Pricing"])
def daily_pricing(
    ticker: str,
    start_date: date = Query(..., description="Start date, YYYY-MM-DD (inclusive)"),
    end_date: date = Query(..., description="End date, YYYY-MM-DD (inclusive)"),
) -> JSONResponse:
    """
    Daily OHLCV price history for a ticker over a date range.

    Tries Finnhub first; free-tier Finnhub keys don't return historical
    candles, so this transparently falls back to yfinance in that case
    (see "source"/"warning" in the response to tell which happened).
    """
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")

    result = price_fetcher.get_daily_candles(
        ticker.upper(), start_date.isoformat(), end_date.isoformat()
    )
    return JSONResponse(content=jsonable_encoder(result))


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------
@app.get("/news/company/{ticker}", tags=["News"])
def company_news(
    ticker: str,
    start_date: date = Query(..., description="Start date, YYYY-MM-DD (inclusive)"),
    end_date: date = Query(..., description="End date, YYYY-MM-DD (inclusive)"),
) -> list[dict]:
    """
    Daily historical news for a company. NOTE: Finnhub's free tier only
    serves company news for roughly the last 12 months -- requests further
    back typically return an empty list, not an error.
    """
    articles = news_fetcher.fetch_ticker_news(
        ticker.upper(), start_date.isoformat(), end_date.isoformat()
    )
    return [news_fetcher._normalize_article(ticker.upper(), a) for a in articles]


@app.get("/news/market", tags=["News"])
def market_news(
    category: str = Query("general", description="general | forex | crypto | merger"),
) -> list[dict]:
    """General market news (not tied to a specific ticker)."""
    return news_fetcher.fetch_general_news(category=category)


@app.get("/news/sentiment/{ticker}", tags=["News"])
def news_sentiment(ticker: str) -> JSONResponse:
    """Finnhub news-sentiment score (buzz + bullish/bearish split) for a ticker."""
    result = news_fetcher.fetch_news_sentiment(ticker.upper())
    return JSONResponse(
        status_code=200 if result["success"] else 502, content=jsonable_encoder(result)
    )


# ---------------------------------------------------------------------------
# Market Data
# ---------------------------------------------------------------------------
@app.get("/market/profile/{ticker}", tags=["Market Data"])
def company_profile(ticker: str) -> JSONResponse:
    """Company profile: exchange, market cap, industry, shares outstanding, etc."""
    result = market_client.get_company_profile(ticker.upper())
    return JSONResponse(
        status_code=200 if result["success"] else 502, content=jsonable_encoder(result)
    )


@app.get("/market/peers/{ticker}", tags=["Market Data"])
def company_peers(ticker: str) -> JSONResponse:
    """Sector/industry comparable tickers."""
    result = market_client.get_company_peers(ticker.upper())
    return JSONResponse(
        status_code=200 if result["success"] else 502, content=jsonable_encoder(result)
    )


@app.get("/market/basic_financials/{ticker}", tags=["Market Data"])
def basic_financials(ticker: str, metric: str = "all") -> JSONResponse:
    """Basic financial metrics (valuation ratios, margins, growth, etc.)."""
    result = market_client.get_basic_financials(ticker.upper(), metric=metric)
    return JSONResponse(
        status_code=200 if result["success"] else 502, content=jsonable_encoder(result)
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8004, reload=False)
