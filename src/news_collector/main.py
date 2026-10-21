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
import sys
from datetime import date
from pathlib import Path

import httpx
from tqdm import tqdm  # type: ignore[import-untyped]

# `python -m news_collector.main ...` skips the `cli/news_collector_cli.py` wrapper, so this
# module loads `.env` itself, before the imports below read the environment.
from data_mining.env import load_env, replay_notices

load_env()  # .env wins over the inherited environment (T-066b)

from data_mining.db import resolve_db_path
from news_collector.companies import CompanyLookupError, lookup_companies
from news_collector.config import InvalidRangeError, build_date_range, default_start_date
from news_collector.models import Company
from news_collector.orchestrator import DiscoveryOrchestrator, build_connectors
from news_collector.reclass_cache import reclass_from_cache
from news_collector.sp500 import fetch_sp500_companies
from news_collector.storage.queue import URLQueue
from news_collector.utils.http import build_client
from news_collector.utils.logging import get_logger, setup_logging

log = get_logger(__name__)


def _parse_date(s: str) -> date:
    """argparse `type=`: a malformed date is a usage error (exit 2)."""
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {s!r}") from exc


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


async def _load_companies(
    args: argparse.Namespace, client: httpx.AsyncClient, start: date
) -> list[Company]:
    """Neither --tickers nor --sp500 given => run the entire S&P 500, fetched live from Wikipedia.

    --tickers are named by exact symbol (T-067), never by the ticker itself.
    """
    if args.sp500:
        companies = _load_sp500_csv(args.sp500)
        log.info("Loaded %d companies from %s", len(companies), args.sp500)
        return companies
    if args.tickers:
        return await lookup_companies([t.upper().strip() for t in args.tickers], client, start)
    log.info("No --tickers/--sp500 given - fetching the full S&P 500 list from Wikipedia")
    try:
        return await fetch_sp500_companies(client)
    except ValueError:
        log.exception("Failed to fetch S&P 500 list from Wikipedia")
        sys.exit(1)


_NOTES_SHOWN = 5


def _print_notes(label: str, notes: list[str]) -> None:
    """The count of errors or warnings of a run and the first few, in the summary."""
    if notes:
        print(f"{label:<17}: {len(notes)}")
        for note in notes[:_NOTES_SHOWN]:
            print(f"  - {note}")


async def cmd_discover(args: argparse.Namespace) -> int:
    log_path = setup_logging(args.log_level, log_dir=args.log_dir, run_name="discover")
    if log_path:
        log.info("Logging this run to %s", log_path)

    replay_notices(log)  # load_env() ran before the file handler existed (T-066b, T-077)
    try:
        date_range = build_date_range(args.start_date, args.end_date)
    except InvalidRangeError as exc:
        log.error("%s", exc)  # noqa: TRY400 - the message is the whole story, no stack
        return 1

    db_path = Path(resolve_db_path(args.db))
    queue = URLQueue(db_path)

    async with build_client() as client:
        try:
            companies = await _load_companies(args, client, date_range.start)
        except CompanyLookupError as exc:
            log.error("%s", exc)  # noqa: TRY400 - the message is the whole story, no stack
            return 1

        connectors = build_connectors(client)
        orchestrator = DiscoveryOrchestrator(
            connectors=connectors,
            queue=queue,
            concurrency=args.concurrency,
        )
        pair_domains = args.domains or list(connectors)
        # one tick per (company, domain) pair that runs; `disable=None` turns the bar off when the
        # output is not a terminal (a pipe, a log file, a test), so it never writes into a log
        bar = tqdm(
            total=len(companies) * len(pair_domains),
            unit="pair",
            desc="discover",
            disable=None,
        )
        try:
            stats = await orchestrator.run(
                companies=companies,
                domains=args.domains or None,
                date_range=date_range,
                resume=args.resume,
                refresh_sitemaps=args.refresh_sitemaps,
                progress=lambda _partial: bar.update(1),
            )
        finally:
            bar.total = (
                bar.n
            )  # a pair skipped by --resume or a domain left out: the bar ends where it got
            bar.close()

    # Print summary
    print("\n=== Discovery Summary ===")
    print(f"Date range       : {date_range.start} to {date_range.end}")
    print(f"Total discovered : {stats.total_discovered}")
    print(
        f"Inserted (new)   : {stats.total_inserted} (matcher-rejected, never fetched: {stats.total_rejected})"
    )
    print(f"Sitemap rejects not stored: {stats.total_rejected_not_stored} (read from the cache)")
    print(f"Duplicates       : {stats.duplicate_count}")
    print(f"DDG results      : {stats.ddg_count}")
    print(f"Sitemap/API      : {stats.sitemap_count}")
    if args.resume:
        print(f"Skipped (resume) : {stats.skipped_pairs}")
    print(f"Elapsed          : {stats.elapsed_seconds:.1f}s")
    _print_notes("Errors", stats.errors)

    _print_notes("Warnings", stats.warnings)

    print("\nBy domain:")
    for domain, count in sorted(stats.by_domain.items(), key=lambda x: -x[1]):
        print(f"  {domain:<30} {count}")

    if args.export:
        queue.initialize()
        exported = queue.export_pending_csv(args.export)
        print(f"\nExported {exported} URLs to {args.export}")

    # A run that recorded errors did not do all its work (a batch that failed to store, a
    # task that raised): say so to the shell, not only in the summary above.
    return 1 if stats.errors else 0


async def cmd_reclass_sitemap(args: argparse.Namespace) -> int:
    """Class the sitemap cache again with the current matcher: no crawl, dry run unless --apply."""

    setup_logging(args.log_level, log_dir=args.log_dir, run_name="reclass-sitemap")
    try:
        date_range = build_date_range(args.start_date, args.end_date)
    except InvalidRangeError as exc:
        log.error("%s", exc)  # noqa: TRY400 - the message is the whole story, no stack
        return 1
    db_path = str(resolve_db_path(args.db))
    async with build_client() as client:
        try:
            companies = await _load_companies(args, client, date_range.start)
        except CompanyLookupError as exc:
            log.error("%s", exc)  # noqa: TRY400 - the message is the whole story, no stack
            return 1
        connectors = build_connectors(client)
        domains = args.domains or [d for d, c in connectors.items() if c.config.sitemap_roots]
        bar = tqdm(total=len(companies) * len(domains), unit="pair", desc="re-class")
        try:
            report = await reclass_from_cache(
                db_path,
                connectors,
                companies,
                date_range,
                domains=args.domains or None,
                apply=args.apply,
                full_scan=args.full_scan,
                progress=lambda _result: bar.update(1),
            )
        finally:
            bar.total = bar.n  # a domain without a cache skips its pairs: the bar ends where it got
            bar.close()
    classes = report.classes()
    print("\n=== Re-class of the sitemap cache ===")
    print(
        f"Mode             : {'APPLIED' if report.applied else 'dry run (nothing written; --apply writes)'}"
    )
    print(f"Date range       : {date_range.start} to {date_range.end}")
    print(
        f"Pairs read       : {sum(classes.values())} (accept {classes['accept']}, review {classes['review']}, reject {classes['reject']}: never stored)"
    )
    if report.applied:
        print(
            f"Inserted / promoted: {report.total('inserted')} / {report.total('promoted')} (lifted from skipped: {report.total('lifted')})"
        )
    else:
        print(f"Would insert / promote: {report.total('new')} / {report.total('promotable')}")
    print(f"Elapsed          : {report.elapsed_seconds:.1f}s")
    if report.failed:
        print(f"Tickers failed   : {report.failed} (see the errors below and the log)")
    _print_notes("Errors", report.errors)
    return 1 if report.errors else 0


def cmd_stats(args: argparse.Namespace) -> None:
    setup_logging(args.log_level)
    queue = URLQueue(resolve_db_path(args.db))
    queue.initialize()
    stats = queue.stats()
    print(json.dumps(stats, indent=2))


def cmd_export(args: argparse.Namespace) -> None:
    setup_logging(args.log_level)
    queue = URLQueue(resolve_db_path(args.db))
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
        default=None,
        help="SQLite database path (default: $DATABASE_URL if set and not empty, else data/urls.db)",
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
        type=_parse_date,
        default=default_start_date(),
        help="Start date YYYY-MM-DD (default from DISCOVERY_START_DATE env var, else 2022-01-01)",
    )
    disc.add_argument(
        "--end-date",
        type=_parse_date,
        default=None,
        help=(
            "End date YYYY-MM-DD (default: today, UTC, when the command starts). To continue "
            "a batch on a later day pass the same value: --resume skips only an exact range."
        ),
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
    disc.add_argument(
        "--refresh-sitemaps",
        action="store_true",
        help=(
            "Crawl each domain's sitemap even when its cache is at most 7 days old "
            "(the cache is never narrowed)."
        ),
    )
    disc.add_argument("--export", help="Export pending URLs to CSV after discovery")

    # reclass-sitemap (T-072)
    rec = sub.add_parser(
        "reclass-sitemap",
        help="Class the sitemap cache again with the current matcher (no crawl; dry run by default)",
    )
    rec.add_argument("--sp500", help="Path to S&P 500 CSV (ticker, name, sector), as for discover")
    rec.add_argument(
        "--tickers", nargs="+", help="Tickers to re-class (default: the whole S&P 500)"
    )
    rec.add_argument(
        "--domains", nargs="+", default=None, help="Domains with a sitemap cache (default: all)"
    )
    rec.add_argument("--start-date", type=_parse_date, default=default_start_date())
    rec.add_argument("--end-date", type=_parse_date, default=None)
    rec.add_argument(
        "--apply",
        action="store_true",
        help="Enqueue the pairs that are now accept/review (promote-only); without it nothing is written",
    )
    rec.add_argument(
        "--full-scan",
        action="store_true",
        help="Class every cached entry instead of the token index's candidates (slower; for a "
        "matcher change the index does not know about)",
    )

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
        sys.exit(asyncio.run(cmd_discover(args)))
    elif args.command == "reclass-sitemap":
        sys.exit(asyncio.run(cmd_reclass_sitemap(args)))
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "export":
        cmd_export(args)


if __name__ == "__main__":
    main()
