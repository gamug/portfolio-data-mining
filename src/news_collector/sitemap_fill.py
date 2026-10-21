"""Fill the sitemap cache of one domain, with no discovery (T-073, the step before gate G3).

The cache is filled by the first run that needs it. On the real file that run was to be a discovery,
which could not run for `cnbc.com` until `T-071` stopped storing rejects (it has since: PR #99, and
the first discovery ran 2026-10-21), so this does only the crawl:
`SitemapCache.begin` writes `sitemap_crawls` and `sitemap_entries`, and nothing else in the file.
A backup that matches the file is required first (the caller says it is a rehearsal copy
otherwise), and the pipeline tables are fingerprinted before and after to show they were not touched.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import httpx

from news_collector.connectors.base import BaseConnector
from news_collector.models import DateRange
from news_collector.reclaim_sitemap import (
    Fingerprint,
    ReclaimError,
    check_database,
    fingerprint,
    verify_backup,
)
from news_collector.sitemap_cache import SitemapCache, SitemapRun

CACHE_TABLES = ("sitemap_entries", "sitemap_crawls")


@dataclass
class FillReport:
    domain: str
    run: SitemapRun
    crawled: bool  # False: a complete, fresh crawl was already there
    before: Fingerprint
    after: Fingerprint

    @property
    def pipeline_unchanged(self) -> bool:
        a, b = self.before, self.after
        return (a.discovered, a.articles, a.by_class, a.id_sum) == (
            b.discovered,
            b.articles,
            b.by_class,
            b.id_sum,
        )

    @property
    def complete(self) -> bool:
        return self.run.crawl is not None and not self.run.failures


def _read_fingerprint(db_path: Path) -> Fingerprint:
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=60)
    try:
        return fingerprint(conn)
    finally:
        conn.close()


def _require_cache_tables(db_path: Path) -> None:
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=60)
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()
    if missing := [t for t in CACHE_TABLES if t not in have]:
        raise ReclaimError(f"{db_path} has no {missing}: it is not at schema version 4 or later")


async def fill_cache(
    db_path: Path,
    connector: BaseConnector,
    client: httpx.AsyncClient,
    date_range: DateRange,
    *,
    backup: Path | None,
    force: bool = False,
) -> FillReport:
    """Crawl the domain's sitemaps into the cache of `db_path` once, and check nothing else changed.

    `backup` is another file with the live file's fingerprint (`None` only for a rehearsal copy).
    Raises `ReclaimError` before any write when the file, the backup or the connector is unusable.
    """
    db_path = Path(db_path)
    check_database(db_path)
    if not connector.config.sitemap_roots:
        raise ReclaimError(f"{connector.config.domain} has no sitemap roots")
    _require_cache_tables(db_path)
    if backup is not None:
        verify_backup(db_path, backup)
    before = _read_fingerprint(db_path)
    cache = SitemapCache.for_connector(connector, client, str(db_path))
    run = await cache.begin(date_range, force=force)
    return FillReport(
        connector.config.domain, run, cache.crawls > 0, before, _read_fingerprint(db_path)
    )
