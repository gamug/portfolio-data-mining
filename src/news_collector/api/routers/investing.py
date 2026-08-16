"""Investing.com discovery router."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends

from news_collector.api.deps import get_client
from news_collector.api.routers._common import run_domain_discovery
from news_collector.api.schemas import DiscoverRequest, DiscoverResponse
from news_collector.connectors.investing import InvestingConnector

router = APIRouter(prefix="/discover/investing", tags=["Investing.com"])


@router.post("/", response_model=list[DiscoverResponse])
async def discover_investing(
    req: DiscoverRequest,
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
) -> list[DiscoverResponse]:
    """
    Run DDG-only discovery for Investing.com for each requested ticker.

    Direct crawling is blocked by Cloudflare; DuckDuckGo indexed content
    is used as the discovery source.
    """
    connector = InvestingConnector(client)
    return await run_domain_discovery(req, connector, client, domain="investing.com")
