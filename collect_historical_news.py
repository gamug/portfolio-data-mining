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
    batch_size = 2
    COMPANIES = [COMPANIES[i:i+batch_size] for i in range(0, len(COMPANIES), batch_size)] # Split into chunks of 5 companies to avoid query length limits
    output_path = os.path.join(general["paths"]["news_links"], "gdelt_news_full.csv")
    
    fetcher = GDELTNewsFetcher(sleep_between_calls=3.0)

    company_q = [fetcher.build_or_query(companies) for companies in COMPANIES]
    domain_q = fetcher.build_domain_filter(general["domains"])
    combined_query = [f"{companies} {domain_q}" for companies in company_q]
    fetcher.fetch_rolling_range(
        tickers_or_queries=combined_query,
        start_date="2020-01-01",
        end_date="2020-03-31",
        window_days=7,
        checkpoint_csv=output_path,
    )