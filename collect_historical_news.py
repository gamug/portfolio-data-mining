import os
import pandas as pd

from src.config import general
from src.news.gdelt_collector import GDELTNewsFetcher


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Use company names (not tickers) for better GDELT recall.
    # You can also use exact-phrase queries, e.g. '"Apple Inc"'
    COMPANIES = pd.read_csv(
        os.path.join(general["paths"]["input"], "s&p500.csv"),
        sep=";"
    )["Security"].tolist()

    fetcher = GDELTNewsFetcher(sleep_between_calls=3.0)

    # Historical backfill: e.g. 4-5 years ago
    output_path = os.path.join(general["paths"]["news_links"], "gdelt_news_full.csv")
    fetcher.fetch_rolling_range(
        tickers_or_queries=COMPANIES,
        start_date="2020-01-01",
        end_date="2020-01-03",
        window_days=1,
        checkpoint_csv=output_path
    )