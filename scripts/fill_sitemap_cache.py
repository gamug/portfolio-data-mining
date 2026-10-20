"""T-073: fill the sitemap cache of one domain on a database file, with no discovery.

    # a rehearsal copy (no backup needed):
    PYTHONPATH=src uv run python scripts/fill_sitemap_cache.py --db COPY.db --domain cnbc.com \
        --rehearsal-copy

    # the real file, only with the maintainer's approval and a backup that matches it:
    PYTHONPATH=src uv run python scripts/fill_sitemap_cache.py --db REAL.db --domain cnbc.com \
        --backup REAL_BACKUP.db --i-have-the-g3-approval

It runs one crawl of the domain's sitemaps (the `T-070a` code) and writes only the two cache tables;
it stores no pair and runs no matcher. Unlike `reclaim` it takes no exclusive lock and runs no process
check: a cache write is safe next to the gateway (the `T-070b` crawl guard), and the runbook stops the
services for the whole G3 window anyway. A complete, fresh crawl already in the file is reused, so a
second run is a no-op. Exit status 1 when the crawl is incomplete or the step is refused.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

from data_mining import resolve_db_path
from data_mining.env import load_env

load_env()

from news_collector.config import build_date_range, default_start_date  # noqa: E402
from news_collector.orchestrator import build_connectors  # noqa: E402
from news_collector.reclaim_sitemap import ReclaimError  # noqa: E402
from news_collector.sitemap_fill import fill_cache  # noqa: E402
from news_collector.utils.http import build_client  # noqa: E402


def looks_real(db: Path) -> bool:
    """The file `DATABASE_URL`/the default names, or any file called `urls.db`."""
    real = resolve_db_path(None)
    return db.name == "urls.db" or (real is not None and db.resolve() == Path(real).resolve())


async def run(args: argparse.Namespace) -> int:
    db = Path(args.db)
    if args.rehearsal_copy and looks_real(db):
        print(f"{db} looks like the real database: use --backup and --i-have-the-g3-approval")
        return 1
    if not args.rehearsal_copy and not (args.g3 and args.backup):
        print("the real database needs --backup and --i-have-the-g3-approval (T-073, G3)")
        return 1
    date_range = build_date_range(args.start_date, args.end_date)
    try:
        async with build_client() as client:
            connector = build_connectors(client).get(args.domain)
            if connector is None:
                print(f"REFUSED, nothing changed: no connector for {args.domain}")
                return 1
            report = await fill_cache(
                db,
                connector,
                client,
                date_range,
                backup=Path(args.backup) if args.backup else None,
                force=args.force,
            )
    except ReclaimError as exc:
        print(f"REFUSED, nothing changed: {exc}")
        return 1
    crawl = report.run.crawl
    print(f"Domain           : {report.domain}")
    print(f"Crawl            : {'made now' if report.crawled else 'reused (complete and fresh)'}")
    if crawl is not None:
        print(f"Entries          : {len(report.run.rows)} (range from {crawl.range_start})")
    for line in (*report.run.failures, *report.run.warnings):
        print(f"  {line}")
    print(f"Pipeline tables  : {'unchanged' if report.pipeline_unchanged else 'CHANGED'}")
    print(f"  discovered_urls {report.before.discovered} -> {report.after.discovered}")
    print(f"  articles        {report.before.articles} -> {report.after.articles}")
    if not report.pipeline_unchanged:
        print("  a service wrote meanwhile? this command writes only the two cache tables")
    if not report.complete:
        print("INCOMPLETE: the cache is not usable; nothing else was changed")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--backup", help="a file with the database's fingerprint (the real file)")
    parser.add_argument("--i-have-the-g3-approval", dest="g3", action="store_true")
    parser.add_argument("--rehearsal-copy", action="store_true", help="a copy: no backup needed")
    parser.add_argument("--force", action="store_true", help="crawl even if the cache is fresh")
    parser.add_argument("--start-date", type=date.fromisoformat, default=default_start_date())
    parser.add_argument("--end-date", type=date.fromisoformat, default=None)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
