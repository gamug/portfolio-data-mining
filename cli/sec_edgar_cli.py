#!/usr/bin/env python
"""CLI entrypoint: run SEC EDGAR filings/financials lookups directly, no
FastAPI/uvicorn involved (for that, see apps/sec_edgar_api.py instead).
Same underlying EdgarAgent as the API, via one subcommand per endpoint.
See docs/modules/sec-edgar.md.

Prints results as JSON to stdout.

Usage:
    .venv\\Scripts\\python.exe cli\\sec_edgar_cli.py company-info AAPL
    .venv\\Scripts\\python.exe cli\\sec_edgar_cli.py filings AAPL --form 10-K --limit 5
    .venv\\Scripts\\python.exe cli\\sec_edgar_cli.py years-available AAPL --form 10-K
    .venv\\Scripts\\python.exe cli\\sec_edgar_cli.py filing-by-year AAPL --form 10-K --year 2023
    .venv\\Scripts\\python.exe cli\\sec_edgar_cli.py latest-filing AAPL --form 8-K
    .venv\\Scripts\\python.exe cli\\sec_edgar_cli.py financials AAPL --form 10-K --year 2023
    .venv\\Scripts\\python.exe cli\\sec_edgar_cli.py search-filings AAPL --form 10-K --keyword climate
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

from common.utils import init_repository
from sec_edgar.agent import EdgarAgent

NAME = os.environ.get("NAME", "Your Name")
EMAIL = os.environ.get("EMAIL", "your.email@example.com")

FILING_FORMS = ("10-K", "10-Q", "8-K", "S-1")


def print_json(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def cmd_company_info(args, agent: EdgarAgent) -> None:
    print_json(agent.get_company_info(args.ticker))


def cmd_filings(args, agent: EdgarAgent) -> None:
    print_json(agent.get_filings(args.ticker, form=args.form, limit=args.limit))


def cmd_years_available(args, agent: EdgarAgent) -> None:
    print_json(agent.list_years_available(args.ticker, form=args.form))


def cmd_filing_by_year(args, agent: EdgarAgent) -> None:
    print_json(agent.get_filing_by_year(args.ticker, form=args.form, year=args.year))


def cmd_latest_filing(args, agent: EdgarAgent) -> None:
    print_json(agent.get_latest_filing(args.ticker, form=args.form))


def cmd_financials(args, agent: EdgarAgent) -> None:
    print_json(agent.get_financials(args.ticker, form=args.form, year=args.year))


def cmd_search_filings(args, agent: EdgarAgent) -> None:
    print_json(
        agent.search_filings(
            args.ticker,
            keyword=args.keyword,
            form=args.form,
            max_filings_to_search=args.max_filings,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("company-info", help="Basic identifying info for a company")
    p.add_argument("ticker")
    p.set_defaults(func=cmd_company_info)

    p = sub.add_parser("filings", help="List recent filings, optionally by form type")
    p.add_argument("ticker")
    p.add_argument("--form", choices=FILING_FORMS, required=True)
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=cmd_filings)

    p = sub.add_parser("years-available", help="Which years have a filing of a given form type")
    p.add_argument("ticker")
    p.add_argument("--form", choices=FILING_FORMS, required=True)
    p.set_defaults(func=cmd_years_available)

    p = sub.add_parser("filing-by-year", help="A specific filing's metadata, by form + year")
    p.add_argument("ticker")
    p.add_argument("--form", choices=FILING_FORMS, required=True)
    p.add_argument("--year", type=int, required=True)
    p.set_defaults(func=cmd_filing_by_year)

    p = sub.add_parser("latest-filing", help="Most recent filing of a given form type")
    p.add_argument("ticker")
    p.add_argument("--form", choices=FILING_FORMS, required=True)
    p.set_defaults(func=cmd_latest_filing)

    p = sub.add_parser("financials", help="Income statement, balance sheet, cash flow for a filing")
    p.add_argument("ticker")
    p.add_argument("--form", choices=FILING_FORMS, required=True)
    p.add_argument("--year", type=int, required=True)
    p.set_defaults(func=cmd_financials)

    p = sub.add_parser("search-filings", help="Full-text keyword search across recent filings")
    p.add_argument("ticker")
    p.add_argument("--form", choices=FILING_FORMS, required=True)
    p.add_argument("--keyword", required=True)
    p.add_argument("--max-filings", type=int, default=5, dest="max_filings")
    p.set_defaults(func=cmd_search_filings)

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Idempotent: creates the configured output directories (including
    # sec_filings/) -- same startup step apps/sec_edgar_api.py runs from
    # its FastAPI lifespan.
    init_repository()

    args.func(args, agent=EdgarAgent(name=NAME, email=EMAIL))


if __name__ == "__main__":
    main()
