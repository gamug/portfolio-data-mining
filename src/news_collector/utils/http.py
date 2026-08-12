"""Shared HTTP client factory with retry logic."""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    wait_random,
    before_sleep_log,
)
import logging

log = logging.getLogger(__name__)

# Realistic browser User-Agents for rotation
_USER_AGENTS: list[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4.1 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
]


def random_user_agent() -> str:
    return random.choice(_USER_AGENTS)


def build_client(
    timeout: float = 20.0,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """Return a pre-configured async HTTP client."""
    default_headers = {
        "User-Agent": random_user_agent(),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if headers:
        default_headers.update(headers)
    return httpx.AsyncClient(
        headers=default_headers,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        http2=True,
    )


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient errors worth retrying."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.NetworkError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


def make_retry_decorator(max_attempts: int = 5, base_wait: float = 2.0) -> Any:
    """Return a tenacity retry decorator for HTTP fetches."""
    return retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=base_wait, min=base_wait, max=60.0)
        + wait_random(0, 2),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )


async def fetch_text(
    client: httpx.AsyncClient,
    url: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> str:
    """Fetch URL and return response text. Raises httpx.HTTPStatusError on 4xx/5xx."""
    headers = extra_headers or {}

    @make_retry_decorator()
    async def _inner() -> str:
        response = await client.get(url, headers=headers)
        # Non-retryable client errors raised immediately
        if response.status_code in (403, 404, 410):
            response.raise_for_status()
        response.raise_for_status()
        return response.text

    return await _inner()
