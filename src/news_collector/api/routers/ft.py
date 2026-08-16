"""Financial Times discovery router."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends

from news_collector.api.deps import get_client
from news_collector.api.routers._common import run_domain_discovery
from news_collector.api.schemas import DiscoverRequest, DiscoverResponse
from news_collector.connectors.ft import FTConnector

router = APIRouter(prefix="/discover/ft", tags=["Financial Times"])


@router.post("/", response_model=list[DiscoverResponse])
async def discover_ft(
    req: DiscoverRequest,
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
) -> list[DiscoverResponse]:
    """
    Run DDG + Sitemap + Wayback CDX discovery for the Financial Times.

    Note: FT article content is paywalled; this endpoint discovers URLs only.
    """
    connector = FTConnector(client)
    return await run_domain_discovery(req, connector, client, domain="ft.com")
