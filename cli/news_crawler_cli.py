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
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from data_mining.env import load_env

load_env()  # .env wins over the inherited environment (T-066b)

import httpx
from tqdm import tqdm

from data_mining.db import resolve_db_path
from extractor.db import (
    connect,
    ensure_articles_table,
    get_match_class_counts,
    get_status_counts,
    get_urls_by_status,
    record_fetch,
)
from extractor.pipeline import process_one
from extractor.reference import load_gics_map
from extractor.scheduler import DomainScheduler

# $DATABASE_URL is a filesystem path today (this is still SQLite) -- kept as
# an env var so pointing this at a real connection string later is a
# one-line env change, not a code change. Falls back to the pre-existing
# default when unset.
DEFAULT_DB = resolve_db_path()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=None, help="Max URLs to process this run")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Process rows with status='failed' instead of 'pending' -- retries URLs that "
        "previously came back with an HTTP error or a network failure (fetch_status="
        "'failed', e.g. http_status>=400 or a connection/timeout error), without a "
        "separate reset-to-pending step. 'paywalled'/'thin_content' rows are left alone "
        "since re-fetching them won't change the outcome.",
    )
    parser.add_argument("--default-concurrency", type=int, default=2)
    parser.add_argument(
        "--cnbc-concurrency",
        type=int,
        default=4,
        help="cnbc.com is ~86%% of discovered URLs; give it its own budget",
    )
    return parser.parse_args()


def progress_line(
    status_counts: dict[str, int], class_counts: dict[str, int], target_status: str, n_rows: int
) -> str:
    """The startup line: processed rows (matcher-rejected pairs are not work, so they are
    not counted) and the rows this run takes, plus the rejected count when there is one."""
    done = sum(c for status, c in status_counts.items() if status != "pending")
    line = f"Already processed: {done}. {target_status.capitalize()} this run: {n_rows}."
    rejected = class_counts.get("reject", 0)
    return f"{line} Matcher-rejected, not fetched: {rejected}." if rejected else line


async def run(args: argparse.Namespace) -> dict:
    conn = connect(args.db)
    ensure_articles_table(conn)

    target_status = "failed" if args.retry_failed else "pending"
    rows = get_urls_by_status(conn, [target_status], limit=args.limit)

    print(
        progress_line(
            get_status_counts(conn), get_match_class_counts(conn), target_status, len(rows)
        )
    )

    scheduler = DomainScheduler(
        default_concurrency=args.default_concurrency,
        domain_concurrency={"cnbc.com": args.cnbc_concurrency},
    )

    counts: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        gics_map = await load_gics_map(client)
        tasks = [
            process_one(
                client,
                scheduler,
                dict(row),
                gics_sector=gics_map.get(row["ticker"], {}).get("sector"),
                gics_sub_industry=gics_map.get(row["ticker"], {}).get("sub_industry"),
                fetched_at=datetime.now(UTC).isoformat(),
            )
            for row in rows
        ]
        progress = tqdm(total=len(tasks), unit="url", desc="Extracting")
        for coro in asyncio.as_completed(tasks):
            article = await coro
            record_fetch(conn, article)
            counts[article["fetch_status"]] = counts.get(article["fetch_status"], 0) + 1
            progress.set_postfix(counts, refresh=False)
            progress.update(1)
        progress.close()

    conn.close()
    return counts


def main() -> None:
    args = parse_args()
    try:
        counts = asyncio.run(run(args))
    except KeyboardInterrupt:
        # Every completed URL is committed to the DB as it finishes (see
        # extractor.db.record_fetch), so nothing done so far is
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
