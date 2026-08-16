"""Per-domain concurrency budget for the fetch step.

cnbc.com is ~86% of discovered URLs (see CLAUDE.md) and needs its own
budget so the pipeline doesn't hammer -- or get itself rate-limited by --
a single dominant domain while starving/serializing everything else.
"""
import asyncio
from contextlib import asynccontextmanager


class DomainScheduler:
    def __init__(self, default_concurrency: int = 2, domain_concurrency: dict | None = None):
        self._default_concurrency = default_concurrency
        self._domain_concurrency = domain_concurrency or {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def get_concurrency(self, domain: str) -> int:
        return self._domain_concurrency.get(domain, self._default_concurrency)

    def _semaphore_for(self, domain: str) -> asyncio.Semaphore:
        if domain not in self._semaphores:
            self._semaphores[domain] = asyncio.Semaphore(self.get_concurrency(domain))
        return self._semaphores[domain]

    @asynccontextmanager
    async def slot(self, domain: str):
        semaphore = self._semaphore_for(domain)
        async with semaphore:
            yield
