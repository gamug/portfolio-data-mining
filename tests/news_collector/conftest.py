"""Fixtures of the sitemap cache tests (T-070a)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sitemap_helpers import Site, standard_site

import news_collector.sitemap_cache as sc
from news_collector.api.routers import _common
from news_collector.storage.queue import URLQueue


@pytest.fixture
def db(tmp_path: Path) -> Iterator[str]:
    sc.reset_shared_caches()
    queue = URLQueue(str(tmp_path / "cache.db"))
    queue.initialize()
    queue.close()
    yield queue.db_path
    sc.reset_shared_caches()


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> Site:
    """Every strategy of the test reads this site: the cache's fetch, and no DDG."""
    site = standard_site()

    async def fetch_text(_client: object, url: str) -> str:
        return await site.fetch(url)

    async def no_ddg(self: object, *_: object) -> list:
        return []

    monkeypatch.setattr(sc, "fetch_text", fetch_text)
    monkeypatch.setattr(_common.DDGStrategy, "discover", no_ddg)
    return site


@pytest.fixture
def every_entry_a_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The token index brings every entry as a candidate, so a fake matcher decides the class.

    The cache and wiring tests that use a connector whose matcher accepts everything check the
    crawl, the rows and the counts, not the matching: the real index preselects by the real
    matcher's signals and would drop entries that fake accepts. The index has its own tests
    (`test_match_from_cache.py`, `tests/scripts/test_sitemap_preselect_check.py`).
    """
    from data_mining.token_index import TokenIndex  # noqa: PLC0415

    monkeypatch.setattr(TokenIndex, "candidates", lambda self, _c, _t: set(range(self.size)))
