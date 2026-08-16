"""Seeking Alpha discovery router."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends

from news_collector.api.deps import get_client
from news_collector.api.routers._common import run_domain_discovery
from news_collector.api.schemas import DiscoverRequest, DiscoverResponse
from news_collector.connectors.seeking_alpha import SeekingAlphaConnector

router = APIRouter(prefix="/discover/seeking-alpha", tags=["Seeking Alpha"])


@router.post("/", response_model=list[DiscoverResponse])
async def discover_seeking_alpha(
    req: DiscoverRequest,
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
) -> list[DiscoverResponse]:
    """
    Run DDG-primary discovery for Seeking Alpha for each requested ticker.

    Direct crawling returns 403/CAPTCHA. DDG indexing is the reliable
    discovery path. Set SEEKINGALPHA_RAPIDAPI_KEY env var to additionally
    enable the RapidAPI source.
    """
    connector = SeekingAlphaConnector(client)
    return await run_domain_discovery(req, connector, client, domain="seekingalpha.com")
