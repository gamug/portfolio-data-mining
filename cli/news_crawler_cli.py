#!/usr/bin/env python
"""CLI entrypoint: extract article title/body/metadata for pending URLs —
pipeline stage 2 (full-text extraction), batch/unattended mode. Runs the
extraction pipeline directly, no FastAPI/uvicorn involved (for that, see
apps/news_crawler_api.py instead). Source: news-crawler/run_extraction.py.
See docs/modules/news-crawler.md.

Reads rows from discovered_urls where status='pending' (or, with
--retry-failed, status='failed'), fetches each with httpx (per-domain rate
limited), extracts title/author/pub_date/body via JSON-LD + trafilatura,
classifies the result, and writes it to the `articles` table.

Usage:
    .venv\\Scripts\\python.exe cli\\news_crawler_cli.py --limit 20
    .venv\\Scripts\\python.exe cli\\news_crawler_cli.py --retry-failed
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx
from dotenv import load_dotenv
from tqdm import tqdm

from extractor.db import (
    connect,
    ensure_articles_table,
    get_status_counts,
    get_urls_by_status,
    mark_status,
    save_article,
)
from extractor.pipeline import process_one
from extractor.reference import load_gics_map
from extractor.scheduler import DomainScheduler

load_dotenv()

# $DATABASE_URL is a filesystem path today (this is still SQLite) -- kept as
# an env var so pointing this at a real connection string later is a
# one-line env change, not a code change. Falls back to the pre-existing
# default when unset.
DEFAULT_DB = os.environ.get("DATABASE_URL", "data/urls.db")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=None, help="Max URLs to process this run")
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Process rows with status='failed' instead of 'pending' -- retries URLs that "
             "previously came back with an HTTP error or a network failure (fetch_status="
             "'failed', e.g. http_status>=400 or a connection/timeout error), without a "
             "separate reset-to-pending step. 'paywalled'/'thin_content' rows are left alone "
             "since re-fetching them won't change the outcome.",
    )
    parser.add_argument("--default-concurrency", type=int, default=2)
    parser.add_argument(
        "--cnbc-concurrency", type=int, default=4,
        help="cnbc.com is ~86%% of discovered URLs; give it its own budget",
    )
    return parser.parse_args()


async def run(args) -> dict:
    conn = connect(args.db)
    ensure_articles_table(conn)

    target_status = "failed" if args.retry_failed else "pending"
    rows = get_urls_by_status(conn, [target_status], limit=args.limit)

    prior = get_status_counts(conn)
    already_done = sum(c for status, c in prior.items() if status != "pending")
    print(f"Already processed: {already_done}. {target_status.capitalize()} this run: {len(rows)}.")

    scheduler = DomainScheduler(
        default_concurrency=args.default_concurrency,
        domain_concurrency={"cnbc.com": args.cnbc_concurrency},
    )

    counts: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        gics_map = await load_gics_map(client)
        tasks = [
            process_one(
                client, scheduler, dict(row),
                gics_sector=gics_map.get(row["ticker"], {}).get("sector"),
                gics_sub_industry=gics_map.get(row["ticker"], {}).get("sub_industry"),
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
            for row in rows
        ]
        progress = tqdm(total=len(tasks), unit="url", desc="Extracting")
        for coro in asyncio.as_completed(tasks):
            article = await coro
            save_article(conn, article)
            mark_status(conn, article["id"], article["fetch_status"], article["http_status_code"])
            counts[article["fetch_status"]] = counts.get(article["fetch_status"], 0) + 1
            progress.set_postfix(counts, refresh=False)
            progress.update(1)
        progress.close()

    conn.close()
    return counts


def main():
    args = parse_args()
    try:
        counts = asyncio.run(run(args))
    except KeyboardInterrupt:
        # Every completed URL is committed to the DB as it finishes (see
        # extractor.db.save_article/mark_status), so nothing done so far is
        # lost -- whatever was still in flight just stays 'pending' and
        # gets retried automatically next run. No separate resume step.
        print("\nInterrupted. Progress so far is saved -- re-run the same command to continue.")
        return

    total = sum(counts.values())
    print(f"Processed {total} URLs:")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")


if __name__ == "__main__":
    main()
