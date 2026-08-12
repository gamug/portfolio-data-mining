#!/usr/bin/env python
"""CLI entrypoint: extract article title/body/metadata for pending URLs —
pipeline stage 2 (full-text extraction), batch/unattended mode. Source:
news-crawler/run_extraction.py. See docs/modules/news-crawler.md.

Reads rows from discovered_urls where status='pending', fetches each with
httpx (per-domain rate limited), extracts title/author/pub_date/body via
JSON-LD + trafilatura, classifies the result, and writes it to the
`articles` table.

Usage:
    .venv\\Scripts\\python.exe apps\\news_crawler_extract.py --limit 20
"""
import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx
from tqdm import tqdm

from extractor.db import (
    connect,
    ensure_articles_table,
    get_pending_urls,
    get_status_counts,
    mark_status,
    save_article,
)
from extractor.pipeline import process_one
from extractor.reference import load_gics_map
from extractor.scheduler import DomainScheduler

DEFAULT_DB = "data/urls.db"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=None, help="Max URLs to process this run")
    parser.add_argument("--default-concurrency", type=int, default=2)
    parser.add_argument(
        "--cnbc-concurrency", type=int, default=4,
        help="cnbc.com is ~86%% of discovered URLs; give it its own budget",
    )
    return parser.parse_args()


async def run(args) -> dict:
    conn = connect(args.db)
    ensure_articles_table(conn)

    rows = get_pending_urls(conn, limit=args.limit)

    prior = get_status_counts(conn)
    already_done = sum(c for status, c in prior.items() if status != "pending")
    print(f"Already processed: {already_done}. Pending this run: {len(rows)}.")

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
