import os
from datetime import datetime
import pandas as pd

from src.config import general
from src.news.gnews_collector import SP500NewsFetcher


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Use company names (not tickers) for better GDELT recall.
    # You can also use exact-phrase queries, e.g. '"Apple Inc"'
    sp500 = pd.read_csv(
        os.path.join(general["paths"]["input"], "s&p500.csv"),
        sep=";"
    )
    sp500 = dict(sp500[["Symbol", "Security"]].set_index("Symbol")["Security"].to_dict())
    
    # --- Rolling window over a much larger range ---
    rolling_fetcher = SP500NewsFetcher(
        companies=sp500,   #type: ignore
        domains=general["domains"],
        start_date=datetime(2022, 1, 1),
        end_date=datetime(2026, 8, 5),
        max_results_per_query=50,
        request_delay_seconds=1.0,
        identifier="historical_news",
        window_days=30,  # one independent query plan per 7-day window
    )

    print("Rolling progress before starting:", rolling_fetcher.progress_all_windows())
    rolling_items = rolling_fetcher.run_rolling()
    print("Rolling progress after run:", rolling_fetcher.progress_all_windows())
    print(rolling_fetcher.to_pandas_all_windows().head())