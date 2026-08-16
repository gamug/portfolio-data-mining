"""
FastAPI application entry point for the News Collector — pipeline stage 1
(URL discovery). Source: news-collector/app.py. See docs/modules/news-collector.md.

Run with:
    .venv\\Scripts\\python.exe apps\\news_collector_api.py
    -> http://127.0.0.1:8001/docs
or:
    uvicorn apps.news_collector_api:app --reload --port 8001
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()  # populate os.environ (DATABASE_URL, etc.) before anything reads it

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from news_collector.api.routers import (
    cnbc,
    discovery,
    ft,
    investing,
    nasdaq,
    seeking_alpha,
    stocktwits,
    yahoo_finance,
)
from news_collector.utils.http import build_client
from news_collector.utils.logging import setup_logging

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the shared httpx.AsyncClient across the application lifetime."""
    log_path = setup_logging(log_dir=".log", run_name="api")
    if log_path:
        log.info("Logging this server's run to %s", log_path)
    async with build_client() as client:
        app.state.client = client
        yield
    # client is closed automatically when the async context manager exits


app = FastAPI(
    title="News Collector API",
    description=(
        "Hybrid news link discovery for S&P 500 companies across CNBC, "
        "Yahoo Finance, Financial Times, Investing.com, Nasdaq, "
        "Seeking Alpha, and StockTwits."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# Per-domain discovery endpoints
app.include_router(cnbc.router)
app.include_router(yahoo_finance.router)
app.include_router(ft.router)
app.include_router(investing.router)
app.include_router(nasdaq.router)
app.include_router(seeking_alpha.router)
app.include_router(stocktwits.router)

# Orchestrator + queue utility endpoints
app.include_router(discovery.router)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Redirect root URL to the interactive API docs."""
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["Meta"])
async def health() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001, reload=False)
