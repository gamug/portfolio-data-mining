"""CNBC discovery router."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends

from news_collector.api.deps import get_client
from news_collector.api.routers._common import run_domain_discovery
from news_collector.api.schemas import DiscoverRequest, DiscoverResponse
from news_collector.connectors.cnbc import CNBCConnector

router = APIRouter(prefix="/discover/cnbc", tags=["CNBC"])


@router.post("/", response_model=list[DiscoverResponse])
async def discover_cnbc(
    req: DiscoverRequest,
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
) -> list[DiscoverResponse]:
    """Run DDG + Sitemap discovery for CNBC for each requested ticker."""
    connector = CNBCConnector(client)
    return await run_domain_discovery(req, connector, client, domain="cnbc.com")
