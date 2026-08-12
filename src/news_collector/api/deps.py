"""FastAPI dependencies: shared HTTP client, connectors, and queue."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Depends, Request

from news_collector.connectors.base import BaseConnector
from news_collector.orchestrator import build_connectors
from news_collector.storage.queue import URLQueue


# ---------------------------------------------------------------------------
# Client dependency — resolved from app.state set by the lifespan
# ---------------------------------------------------------------------------


def get_client(request: Request) -> httpx.AsyncClient:
    """Return the shared AsyncClient created during app lifespan."""
    return request.app.state.client


# ---------------------------------------------------------------------------
# Connectors dependency — built fresh each request (lightweight dataclasses)
# ---------------------------------------------------------------------------


def get_connectors(
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
) -> dict[str, BaseConnector]:
    """Instantiate all domain connectors for the current request."""
    return build_connectors(client)


# ---------------------------------------------------------------------------
# Queue factory — creates and initializes a URLQueue for a given db_path
# ---------------------------------------------------------------------------


def get_queue(db_path: str = "data/urls.db") -> URLQueue:
    """Create and initialize a URLQueue at the given path."""
    queue = URLQueue(db_path)
    queue.initialize()
    return queue
