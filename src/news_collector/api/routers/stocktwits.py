"""StockTwits discovery router."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends

from news_collector.api.deps import get_client
from news_collector.api.routers._common import run_domain_discovery
from news_collector.api.schemas import DiscoverRequest, DiscoverResponse
from news_collector.connectors.stocktwits import StockTwitsConnector

router = APIRouter(prefix="/discover/stocktwits", tags=["StockTwits"])


@router.post("/", response_model=list[DiscoverResponse])
async def discover_stocktwits(
    req: DiscoverRequest,
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
) -> list[DiscoverResponse]:
    """
    Run DDG discovery + StockTwits public REST API for each requested ticker.

    The public API (no auth required) paginates through the symbol stream
    and is the primary source; DDG provides additional coverage.
    """
    connector = StockTwitsConnector(client)
    return await run_domain_discovery(
        req,
        connector,
        client,
        domain="stocktwits.com",
        extra_fetch=connector.fetch_via_api,
    )
