#!/usr/bin/env python
"""CLI entrypoint: run news link discovery directly — pipeline stage 1
(URL discovery), no FastAPI/uvicorn involved (for that, see
apps/news_collector_api.py instead). Thin wrapper around
news_collector.main, which already has a full argparse CLI
(discover/stats/export subcommands) — see docs/modules/news-collector.md.

Usage:
    .venv\\Scripts\\python.exe cli\\news_collector_cli.py discover --tickers AAPL MSFT
    .venv\\Scripts\\python.exe cli\\news_collector_cli.py discover --sp500 data/sp500.csv
    .venv\\Scripts\\python.exe cli\\news_collector_cli.py discover   # entire S&P 500
    .venv\\Scripts\\python.exe cli\\news_collector_cli.py stats
    .venv\\Scripts\\python.exe cli\\news_collector_cli.py export --output data/urls.csv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from news_collector.main import main

if __name__ == "__main__":
    main()
