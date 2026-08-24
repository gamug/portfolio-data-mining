"""
CLI entry point for news link discovery.

Usage:
    python -m news_collector.main discover --tickers AAPL MSFT --domains cnbc.com nasdaq.com
    python -m news_collector.main discover --sp500 data/sp500.csv
    python -m news_collector.main discover   # no --tickers/--sp500: entire S&P 500 from Wikipedia
    python -m news_collector.main stats
    python -m news_collector.main export --output data/urls.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from news_collector.config import default_end_date, default_start_date
from news_collector.models import Company, DateRange
from news_collector.orchestrator import DiscoveryOrchestrator, build_connectors
from news_collector.sp500 import fetch_sp500_companies
from news_collector.storage.queue import URLQueue
from news_collector.utils.http import build_client
from news_collector.utils.logging import get_logger, setup_logging

# So `python -m news_collector.main ...` (which never goes through any
# apps/*.py entrypoint) still picks up .env, e.g. DATABASE_URL below.
load_dotenv()

log = get_logger(__name__)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _load_sp500_csv(path: str) -> list[Company]:
    """
    Load companies from a CSV with columns: ticker, name, sector.
    Minimal format: first column is ticker, second is name.
    """
    companies = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ticker = (row.get("ticker") or row.get("Symbol") or "").strip().upper()
            name = (row.get("name") or row.get("Security") or row.get("Company") or "").strip()
            sector = (row.get("sector") or row.get("GICS Sector") or "").strip()
            if ticker and name:
                companies.append(Company(ticker=ticker, name=name, sector=sector))
    return companies


async def cmd_discover(args: argparse.Namespace) -> None:
    log_path = setup_logging(args.log_level, log_dir=args.log_dir, run_name="discover")
    if log_path:
        log.info("Logging this run to %s", log_path)

    date_range = DateRange(
        start=_parse_date(args.start_date),
        end=_parse_date(args.end_date),
    )

    db_path = Path(args.db)
    queue = URLQueue(db_path)

    async with build_client() as client:
        # Build company list. Neither --tickers nor --sp500 given => run the
        # entire S&P 500, fetched live from Wikipedia.
        companies: list[Company] = []
        if args.sp500:
            companies = _load_sp500_csv(args.sp500)
            log.info("Loaded %d companies from %s", len(companies), args.sp500)
        elif args.tickers:
            companies = [Company(ticker=t.upper(), name=t.upper()) for t in args.tickers]
        else:
            log.info("No --tickers/--sp500 given - fetching the full S&P 500 list from Wikipedia")
            try:
                companies = await fetch_sp500_companies(client)
            except ValueError:
                log.exception("Failed to fetch S&P 500 list from Wikipedia")
                sys.exit(1)

        connectors = build_connectors(client)
        orchestrator = DiscoveryOrchestrator(
            connectors=connectors,
            queue=queue,
            concurrency=args.concurrency,
        )
        stats = await orchestrator.run(
            companies=companies,
            domains=args.domains or None,
            date_range=date_range,
            resume=args.resume,
        )

    # Print summary
    print("\n=== Discovery Summary ===")
    print(f"Total discovered : {stats.total_discovered}")
    print(f"Inserted (new)   : {stats.total_inserted}")
    print(f"Duplicates       : {stats.duplicate_count}")
    print(f"DDG results      : {stats.ddg_count}")
    print(f"Sitemap/API      : {stats.sitemap_count}")
    if args.resume:
        print(f"Skipped (resume) : {stats.skipped_pairs}")
    print(f"Elapsed          : {stats.elapsed_seconds:.1f}s")
    if stats.errors:
        print(f"Errors           : {len(stats.errors)}")
        for e in stats.errors[:5]:
            print(f"  - {e}")

    print("\nBy domain:")
    for domain, count in sorted(stats.by_domain.items(), key=lambda x: -x[1]):
        print(f"  {domain:<30} {count}")

    if args.export:
        queue.initialize()
        exported = queue.export_pending_csv(args.export)
        print(f"\nExported {exported} URLs to {args.export}")


def cmd_stats(args: argparse.Namespace) -> None:
    setup_logging(args.log_level)
    queue = URLQueue(args.db)
    queue.initialize()
    stats = queue.stats()
    print(json.dumps(stats, indent=2))


def cmd_export(args: argparse.Namespace) -> None:
    setup_logging(args.log_level)
    queue = URLQueue(args.db)
    queue.initialize()
    count = queue.export_pending_csv(args.output)
    print(f"Exported {count} pending URLs to {args.output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="news-discovery",
        description="Hybrid news link discovery for S&P 500 companies.",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DATABASE_URL", "data/urls.db"),
        help="SQLite database path (default: $DATABASE_URL if set, else data/urls.db)",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    parser.add_argument(
        "--log-dir",
        default=".log",
        help=(
            "Directory for a timestamped file trace of this run (in addition to console "
            "output) — used by 'discover' to record progress/ETA for long batch jobs. "
            "Pass an empty string to disable file logging."
        ),
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # discover
    disc = sub.add_parser("discover", help="Run link discovery")
    disc.add_argument(
        "--sp500",
        help="Path to S&P 500 CSV (ticker, name, sector) — offline alternative to Wikipedia",
    )
    disc.add_argument(
        "--tickers",
        nargs="+",
        help=(
            "Specific tickers to discover. If neither --tickers nor --sp500 is given, "
            "discovery runs across the entire S&P 500, fetched live from Wikipedia."
        ),
    )
    disc.add_argument(
        "--domains",
        nargs="+",
        default=None,
        help="Domains to search (default: all 7)",
    )
    disc.add_argument(
        "--start-date",
        default=default_start_date().isoformat(),
        help="Start date YYYY-MM-DD (default from DISCOVERY_START_DATE env var, else 2022-01-01)",
    )
    disc.add_argument(
        "--end-date",
        default=default_end_date().isoformat(),
        help="End date YYYY-MM-DD (default from DISCOVERY_END_DATE env var, else today)",
    )
    disc.add_argument("--concurrency", type=int, default=5, help="Max parallel tasks")
    disc.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=True,
        help=(
            "Skip (ticker, domain) pairs already completed for this exact date range "
            "on a prior run, per the queue's checkpoint. On by default; pass "
            "--no-resume to force a full redo."
        ),
    )
    disc.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="Force a full redo of every (ticker, domain) pair, ignoring the checkpoint.",
    )
    disc.add_argument("--export", help="Export pending URLs to CSV after discovery")

    # stats
    sub.add_parser("stats", help="Show queue statistics")

    # export
    exp = sub.add_parser("export", help="Export pending URLs to CSV")
    exp.add_argument("--output", default="data/pending_urls.csv")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "discover":
        asyncio.run(cmd_discover(args))
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "export":
        cmd_export(args)


if __name__ == "__main__":
    main()
