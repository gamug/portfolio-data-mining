"""DuckDuckGo search-based URL discovery strategy."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime

from news_collector.connectors.base import BaseConnector
from news_collector.models import DateRange, DiscoveredURL
from news_collector.utils.url import is_valid_http_url, normalize_url

log = logging.getLogger(__name__)

# Per-query inter-request sleep floor (seconds) to avoid DDG throttling
_DDG_SLEEP_MIN = 1.5
_DDG_SLEEP_JITTER = 1.0


class DDGStrategy:
    """
    Discover article URLs by issuing DuckDuckGo text searches.

    Uses the `duckduckgo-search` library (DDGS class) in a thread pool
    executor since that library is synchronous.
    """

    def __init__(
        self,
        connector: BaseConnector,
        semaphore: asyncio.Semaphore,
    ) -> None:
        self._connector = connector
        self._semaphore = semaphore

    async def discover(
        self,
        company: str,
        ticker: str,
        date_range: DateRange,
    ) -> list[DiscoveredURL]:
        """
        Run DDG queries for (company, ticker) and return discovered article URLs.

        Preconditions:
        - company and ticker are non-empty strings
        Postconditions:
        - All returned URLs pass is_article_url and url_matches_company filters
        - source field is "ddg" on all returned items
        Loop Invariants:
        - seen set grows monotonically; no duplicate URL emitted within this call
        """
        queries = self._connector.build_ddg_queries(company, ticker, date_range)
        seen: set[str] = set()
        results: list[DiscoveredURL] = []

        for query in queries:
            await asyncio.sleep(_DDG_SLEEP_MIN + _random_jitter())
            try:
                hits = await self._ddg_search(query, self._connector.config.ddg_max_results)
            except Exception as exc:
                log.warning(
                    "DDG search failed for '%s' / domain=%s: %s",
                    query,
                    self._connector.config.domain,
                    exc,
                )
                continue

            for hit in hits:
                raw_url = hit.get("href") or hit.get("url") or ""
                if not raw_url or not is_valid_http_url(raw_url):
                    continue
                url = normalize_url(raw_url)
                if url in seen:
                    continue
                if not self._connector.is_article_url(url):
                    continue
                title = hit.get("title") or ""
                if not self._connector.url_matches_company(url, title, company, ticker):
                    continue
                seen.add(url)
                results.append(
                    DiscoveredURL(
                        url=url,
                        domain=self._connector.config.domain,
                        company=company,
                        ticker=ticker,
                        source="ddg",
                        discovered_at=datetime.utcnow(),
                        title=title or None,
                    )
                )

        log.info(
            "DDG discovered %d URLs for %s/%s",
            len(results),
            ticker,
            self._connector.config.domain,
        )
        return results

    async def _ddg_search(self, query: str, max_results: int) -> list[dict]:
        """Run DDG text search in executor (library is synchronous)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_ddg_search, query, max_results)


def _sync_ddg_search(query: str, max_results: int) -> list[dict]:
    """Synchronous wrapper — supports both ddgs (v9+) and duckduckgo_search (legacy)."""
    # Package was renamed from duckduckgo_search to ddgs in v9. The try/except
    # fallback needs to stay function-local (module-level would defeat the
    # point: pick whichever name is importable at call time), so PLC0415 is
    # expected here.
    try:
        from ddgs import DDGS  # noqa: PLC0415
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore[no-redef]  # noqa: PLC0415
        except ImportError as e:
            raise ImportError("Install ddgs: pip install ddgs") from e

    return list(DDGS().text(query, max_results=max_results))


def _random_jitter() -> float:
    return random.uniform(0, _DDG_SLEEP_JITTER)  # noqa: S311 - rate-limit jitter, not crypto
