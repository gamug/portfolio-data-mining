#!/usr/bin/env python
"""CLI entrypoint: run pricing/news/market-data lookups directly, no
FastAPI/uvicorn involved (for that, see apps/pricing_api.py instead).
Same underlying classes as the API (StockPriceFetcher, FinnhubNewsFetcher,
MarketDataClient, common.portfolio) via one subcommand per endpoint. See
docs/modules/pricing.md.

Prints results as JSON to stdout.

Usage:
    .venv\\Scripts\\python.exe cli\\pricing_cli.py universe --sector "Information Technology"
    .venv\\Scripts\\python.exe cli\\pricing_cli.py resolve AAPL
    .venv\\Scripts\\python.exe cli\\pricing_cli.py pricing AAPL --start 2024-01-01 --end 2024-06-01
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

from common.portfolio import list_universe, resolve_symbol
from common.utils import init_repository
from pricing.fetcher import StockPriceFetcher
from pricing.market_data import MarketDataClient
from pricing.news import FinnhubNewsFetcher


def print_json(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def cmd_universe(args, **_clients) -> None:
    print_json(list_universe(sector=args.sector))


def cmd_resolve(args, **_clients) -> None:
    result = resolve_symbol(args.query)
    if result is None:
        print(f"'{args.query}' not found in tracked universe.", file=sys.stderr)
        sys.exit(1)
    print_json(result)


def cmd_pricing(args, price_fetcher, **_clients) -> None:
    print_json(price_fetcher.get_daily_candles(args.ticker.upper(), args.start, args.end))


def cmd_news_company(args, news_fetcher, **_clients) -> None:
    articles = news_fetcher.fetch_ticker_news(args.ticker.upper(), args.start, args.end)
    print_json([news_fetcher._normalize_article(args.ticker.upper(), a) for a in articles])


def cmd_news_market(args, news_fetcher, **_clients) -> None:
    print_json(news_fetcher.fetch_general_news(category=args.category))


def cmd_news_sentiment(args, news_fetcher, **_clients) -> None:
    print_json(news_fetcher.fetch_news_sentiment(args.ticker.upper()))


def cmd_market_profile(args, market_client, **_clients) -> None:
    print_json(market_client.get_company_profile(args.ticker.upper()))


def cmd_market_peers(args, market_client, **_clients) -> None:
    print_json(market_client.get_company_peers(args.ticker.upper()))


def cmd_market_financials(args, market_client, **_clients) -> None:
    print_json(market_client.get_basic_financials(args.ticker.upper(), metric=args.metric))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("universe", help="List the tracked S&P 500 universe")
    p.add_argument("--sector", default=None, help="Filter by GICS Sector, e.g. 'Information Technology'")
    p.set_defaults(func=cmd_universe)

    p = sub.add_parser("resolve", help="Resolve a ticker or company name to its universe row")
    p.add_argument("query")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("pricing", help="Daily OHLCV price history (Finnhub, falls back to yfinance)")
    p.add_argument("ticker")
    p.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (inclusive)")
    p.set_defaults(func=cmd_pricing)

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

    p = sub.add_parser("market-profile", help="Company profile (exchange, market cap, industry, ...)")
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

    # Fail fast if the key is missing, rather than lazily on first call --
    # same convention as apps/pricing_api.py, just deferred past --help.
    FINNHUB_API_KEY = os.environ["FINNHUB_API_KEY"]

    # Idempotent: creates the configured output directories -- same startup
    # step apps/pricing_api.py runs from its FastAPI lifespan.
    init_repository()

    args.func(
        args,
        price_fetcher=StockPriceFetcher(finnhub_api_key=FINNHUB_API_KEY),
        news_fetcher=FinnhubNewsFetcher(api_key=FINNHUB_API_KEY),
        market_client=MarketDataClient(finnhub_api_key=FINNHUB_API_KEY),
    )


if __name__ == "__main__":
    main()
