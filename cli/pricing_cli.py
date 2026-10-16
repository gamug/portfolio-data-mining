#!/usr/bin/env python
"""CLI entrypoint: run pricing/news/market-data lookups directly, no
FastAPI/uvicorn involved (for that, see apps/pricing_api.py instead).
Same underlying classes as the API (StockPriceFetcher, FinnhubNewsFetcher,
MarketDataClient, data_mining.portfolio) via one subcommand per endpoint. See
docs/modules/pricing.md.

Prints results as JSON to stdout.

Usage:
    .venv\\Scripts\\python.exe cli\\pricing_cli.py universe --sector "Information Technology"
    .venv\\Scripts\\python.exe cli\\pricing_cli.py universe --as-of 2019-06-01
    .venv\\Scripts\\python.exe cli\\pricing_cli.py resolve AAPL
    .venv\\Scripts\\python.exe cli\\pricing_cli.py resolve TWTR --as-of 2022-10-01
    .venv\\Scripts\\python.exe cli\\pricing_cli.py universe-backfill
    .venv\\Scripts\\python.exe cli\\pricing_cli.py universe-snapshot
    .venv\\Scripts\\python.exe cli\\pricing_cli.py universe-gics --out ..\\data\\universe_history_gics.db
    .venv\\Scripts\\python.exe cli\\pricing_cli.py leaver-prices
    .venv\\Scripts\\python.exe cli\\pricing_cli.py pricing AAPL --start 2024-01-01 --end 2024-06-01
    .venv\\Scripts\\python.exe cli\\pricing_cli.py actions XOM --start 2022-01-01 --end 2026-08-27
    .venv\\Scripts\\python.exe cli\\pricing_cli.py news-company AAPL --start 2024-01-01 --end 2024-06-01
    .venv\\Scripts\\python.exe cli\\pricing_cli.py news-market --category general
    .venv\\Scripts\\python.exe cli\\pricing_cli.py news-sentiment AAPL
    .venv\\Scripts\\python.exe cli\\pricing_cli.py market-profile AAPL
    .venv\\Scripts\\python.exe cli\\pricing_cli.py market-peers AAPL
    .venv\\Scripts\\python.exe cli\\pricing_cli.py market-financials AAPL --metric all
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

from data_mining.leaver_prices import DEFAULT_OUTPUT_DIR, LeaverPricesError, fetch_leaver_prices
from data_mining.portfolio import list_universe, resolve_symbol
from data_mining.universe_gics import build_gics_db
from data_mining.universe_history import backfill_from_changes, record_snapshot
from pricing.fetcher import StockPriceFetcher
from pricing.market_data import MarketDataClient
from pricing.news import FinnhubNewsFetcher


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def cmd_universe(args: argparse.Namespace, **_clients: Any) -> None:
    try:
        print_json(list_universe(sector=args.sector, as_of=args.as_of))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


def cmd_resolve(args: argparse.Namespace, **_clients: Any) -> None:
    try:
        result = resolve_symbol(args.query, as_of=args.as_of)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    if result is None:
        print(f"'{args.query}' not found in tracked universe.", file=sys.stderr)
        sys.exit(1)
    print_json(result)


def cmd_universe_backfill(args: argparse.Namespace, **_clients: Any) -> None:
    count = backfill_from_changes(force=args.force)
    if count:
        print(f"Backfilled {count} membership intervals into universe_membership.")
    else:
        print("Already backfilled -- pass --force to redo.")


def cmd_universe_snapshot(args: argparse.Namespace, **_clients: Any) -> None:
    print_json(record_snapshot())


def cmd_universe_gics(args: argparse.Namespace, **_clients: Any) -> None:
    try:
        report = build_gics_db(args.source, args.out)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    print_json(report)


def cmd_leaver_prices(args: argparse.Namespace, **_clients: Any) -> None:
    """Fetch the Tiingo prices for the 33 delivered leaver lines into the
    private local file. Reads $TIINGO_API_KEY, sends read-only GETs to the
    shared gateway, and never calls Finnhub."""
    try:
        manifest = fetch_leaver_prices(output_dir=Path(args.out))
    except LeaverPricesError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    # Counts, dates and hashes only -- never a price (brief §7).
    print_json(
        {
            "output_dir": str(Path(args.out)),
            "data_file": manifest["data_file"],
            "data_file_sha256": manifest["data_file_sha256"],
            "request_count": manifest["request_count"],
            "lines": len(manifest["lines"]),
            "not_delivered": [entry["ask"] for entry in manifest["not_delivered"]],
            "checks": manifest["checks"],
        }
    )


def cmd_pricing(
    args: argparse.Namespace, price_fetcher: StockPriceFetcher, **_clients: Any
) -> None:
    print_json(price_fetcher.get_daily_candles(args.ticker.upper(), args.start, args.end))


def cmd_actions(
    args: argparse.Namespace, price_fetcher: StockPriceFetcher, **_clients: Any
) -> None:
    print_json(price_fetcher.get_corporate_actions(args.ticker.upper(), args.start, args.end))


def cmd_news_company(
    args: argparse.Namespace, news_fetcher: FinnhubNewsFetcher, **_clients: Any
) -> None:
    result = news_fetcher.fetch_ticker_news(args.ticker.upper(), args.start, args.end)
    if result["success"]:
        result = {
            "success": True,
            "data": [
                news_fetcher._normalize_article(args.ticker.upper(), a) for a in result["data"]
            ],
        }
    print_json(result)


def cmd_news_market(
    args: argparse.Namespace, news_fetcher: FinnhubNewsFetcher, **_clients: Any
) -> None:
    print_json(news_fetcher.fetch_general_news(category=args.category))


def cmd_news_sentiment(
    args: argparse.Namespace, news_fetcher: FinnhubNewsFetcher, **_clients: Any
) -> None:
    print_json(news_fetcher.fetch_news_sentiment(args.ticker.upper()))


def cmd_market_profile(
    args: argparse.Namespace, market_client: MarketDataClient, **_clients: Any
) -> None:
    print_json(market_client.get_company_profile(args.ticker.upper()))


def cmd_market_peers(
    args: argparse.Namespace, market_client: MarketDataClient, **_clients: Any
) -> None:
    print_json(market_client.get_company_peers(args.ticker.upper()))


def cmd_market_financials(
    args: argparse.Namespace, market_client: MarketDataClient, **_clients: Any
) -> None:
    print_json(market_client.get_basic_financials(args.ticker.upper(), metric=args.metric))


def parse_args() -> argparse.Namespace:  # noqa: PLR0915 -- a flat argparse subcommand table
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("universe", help="List the tracked S&P 500 universe")
    p.add_argument(
        "--sector", default=None, help="Filter by GICS Sector, e.g. 'Information Technology'"
    )
    p.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        dest="as_of",
        help="Point-in-time date, YYYY-MM-DD (omit for today's live snapshot; "
        "requires universe-backfill to have been run once)",
    )
    p.set_defaults(func=cmd_universe)

    p = sub.add_parser("resolve", help="Resolve a ticker or company name to its universe row")
    p.add_argument("query")
    p.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        dest="as_of",
        help="Point-in-time date, YYYY-MM-DD (omit for today's live snapshot)",
    )
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser(
        "universe-backfill",
        help="One-time point-in-time history reconstruction from Wikipedia's change log",
    )
    p.add_argument("--force", action="store_true", help="Redo even if already backfilled")
    p.set_defaults(func=cmd_universe_backfill)

    p = sub.add_parser(
        "universe-snapshot",
        help="Record today's roster as a forward snapshot (diff vs last known state)",
    )
    p.set_defaults(func=cmd_universe_snapshot)

    p = sub.add_parser(
        "universe-gics",
        help="Backfill GICS sector/sub-industry + revision citation for leaver stints "
        "(run after universe-backfill; writes to a new file)",
    )
    p.add_argument(
        "--source",
        default=os.environ.get("UNIVERSE_DB_PATH") or "data/universe.db",
        help="Source universe DB to copy (default: $UNIVERSE_DB_PATH or data/universe.db)",
    )
    p.add_argument(
        "--out",
        required=True,
        help="Destination DB to create/fill (must differ from --source and must not be "
        "named universe_history.db or universe.db)",
    )
    p.set_defaults(func=cmd_universe_gics)

    p = sub.add_parser(
        "leaver-prices",
        help="Fetch the Tiingo daily prices for the 33 delivered S&P 500 leaver lines "
        "into a private local file (never committed; never redistributed)",
    )
    p.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Private output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    p.set_defaults(func=cmd_leaver_prices)

    p = sub.add_parser(
        "pricing", help="Daily OHLCV price history (Finnhub, falls back to yfinance)"
    )
    p.add_argument("ticker")
    p.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (inclusive)")
    p.set_defaults(func=cmd_pricing)

    p = sub.add_parser(
        "actions", help="Corporate actions: dividends and splits with ex-dates (yfinance)"
    )
    p.add_argument("ticker")
    p.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (inclusive)")
    p.set_defaults(func=cmd_actions)

    p = sub.add_parser("news-company", help="Company news over a date range")
    p.add_argument("ticker")
    p.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (inclusive)")
    p.set_defaults(func=cmd_news_company)

    p = sub.add_parser("news-market", help="General market news")
    p.add_argument("--category", default="general", help="general | forex | crypto | merger")
    p.set_defaults(func=cmd_news_market)

    p = sub.add_parser("news-sentiment", help="Finnhub news-sentiment score for a ticker")
    p.add_argument("ticker")
    p.set_defaults(func=cmd_news_sentiment)

    p = sub.add_parser(
        "market-profile", help="Company profile (exchange, market cap, industry, ...)"
    )
    p.add_argument("ticker")
    p.set_defaults(func=cmd_market_profile)

    p = sub.add_parser("market-peers", help="Sector/industry comparable tickers")
    p.add_argument("ticker")
    p.set_defaults(func=cmd_market_peers)

    p = sub.add_parser("market-financials", help="Basic financial metrics")
    p.add_argument("ticker")
    p.add_argument("--metric", default="all")
    p.set_defaults(func=cmd_market_financials)

    return parser.parse_args()


def main() -> None:
    # Parsed before reading FINNHUB_API_KEY so `--help` works without a
    # key configured -- argparse exits on -h/--help before returning here.
    args = parse_args()

    # leaver-prices fetches from Tiingo only (and read-only GETs to the shared
    # gateway) -- it never calls Finnhub, so it must not require that key.
    if args.command == "leaver-prices":
        args.func(args)
        return

    # Fail fast if the key is missing, rather than lazily on first call --
    # same convention as apps/pricing_api.py, just deferred past --help.
    FINNHUB_API_KEY = os.environ["FINNHUB_API_KEY"]

    args.func(
        args,
        price_fetcher=StockPriceFetcher(finnhub_api_key=FINNHUB_API_KEY),
        news_fetcher=FinnhubNewsFetcher(api_key=FINNHUB_API_KEY),
        market_client=MarketDataClient(finnhub_api_key=FINNHUB_API_KEY),
    )


if __name__ == "__main__":
    main()
