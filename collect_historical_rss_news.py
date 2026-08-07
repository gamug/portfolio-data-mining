"""Run the fast Google News RSS collector for the S&P 500 history range.

This script intentionally collects Google News redirect URLs without
resolving them. Use the crawler as a later, separately bounded stage to
resolve/fetch article pages.
"""

import os
from datetime import datetime

import pandas as pd

from src.config import general
from src.news.gnews_rss_fast_collector import FastSP500NewsFetcher


if __name__ == "__main__":
    sp500_path = os.path.join(general["paths"]["input"], "s&p500.csv")
    sp500_frame = pd.read_csv(sp500_path, sep=";")
    companies = dict(sp500_frame.set_index("Symbol")["Security"])

    fetcher = FastSP500NewsFetcher(
        companies=companies,
        domains=general["domains"],
        start_date=datetime(2022, 1, 1),
        end_date=datetime(2026, 8, 5),
        max_results_per_query=50,
        request_delay_seconds=1.0,
        domain_batch_size=4,
        request_timeout=(10.0, 30.0),
        max_retries=3,
        identifier="historical_news_rss",
        window_days=7,
    )

    print("Rolling progress before starting:", fetcher.progress_all_windows())
    fetcher.run_rolling()
    print("Rolling progress after run:", fetcher.progress_all_windows())
    print(fetcher.to_pandas_all_windows().head())
