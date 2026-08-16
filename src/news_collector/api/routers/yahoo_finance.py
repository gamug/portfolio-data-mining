"""Yahoo Finance discovery router."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends

from news_collector.api.deps import get_client
from news_collector.api.routers._common import run_domain_discovery
from news_collector.api.schemas import DiscoverRequest, DiscoverResponse
from news_collector.connectors.yahoo_finance import YahooFinanceConnector

router = APIRouter(prefix="/discover/yahoo-finance", tags=["Yahoo Finance"])


@router.post("/", response_model=list[DiscoverResponse])
async def discover_yahoo_finance(
    req: DiscoverRequest,
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
) -> list[DiscoverResponse]:
    """
    Run DDG discovery + yfinance API for Yahoo Finance for each requested ticker.

    In addition to DuckDuckGo searches, this endpoint calls yfinance Ticker.news
    for each ticker to retrieve additional article links.
    """
    connector = YahooFinanceConnector(client)
    return await run_domain_discovery(
        req,
        connector,
        client,
        domain="finance.yahoo.com",
        extra_fetch=connector.fetch_via_yfinance,
    )
