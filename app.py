# app.py
import asyncio, os, traceback
from enum import Enum
import pandas as pd
from fastapi import FastAPI, Query
from fastapi.responses import RedirectResponse
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from crawl4ai import CrawlerRunConfig

from src.config import general
from src.commons.utils import FileEnumFactory
from src.news.gdelt_collector import GDELTNewsFetcher
from src.fundamental.edgar_tool import EdgarAgent
from src.news.finnhub_collector import FinnhubNewsFetcher
from src.news.scraping.crawler import BulkCrawler
from src.news.scraping.scraper import MarkdownExtractor


#defining lists to consume in swagger ui
link_files = FileEnumFactory(general["paths"]["news_links"])
_link_files = link_files.build_enum()
markdown_files = FileEnumFactory(general["paths"]["news_markdown"])
_markdown_files = markdown_files.build_enum()
__markdown_files = markdown_files.build_enum()
class FilingForm(str, Enum):
    ten_k = "10-K"
    ten_q = "10-Q"
    eight_k = "8-K"
    s1 = "S-1"

# Initialize EdgarAgent once (replace with your real identity)
agent = EdgarAgent(name=os.environ.get("NAME", "Jane Doe"), email=os.environ.get("EMAIL", "jane@example.com"))
# Initialize once with your API key
finnhub_fetcher = FinnhubNewsFetcher(api_key=os.environ.get("FINNHUB_API_KEY", "YOUR_API_KEY"))


app = FastAPI(
    title="Portafolio data mining API",
    version="1.0.0",
    openapi_tags=[
        {
            "name": "Edgar",
            "description": "Endpoints for SEC Edgar filings and financial data"
        },
        {
            "name": "Finnhub",
            "description": "Endpoints for Finnhub news collection. Only used to collect less that 1 year old news"
        },
        {
            "name": "Crawler",
            "description": "Endpoints for web crawling and data extraction"
        },
        {
            "name": "Process News",
            "description": "Endpoints for processing news Markdown data to get title and content"
        },
        {
            "name": "GDELT",
            "description": "Endpoints for GDELT news collection"
        }
    ],
    swagger_ui_parameters={"docExpansion": "none"}
)

# ---------------------------------------------------------------------------
# Welcome page redirect
# ---------------------------------------------------------------------------
@app.get("/")
def welcome():
    """Redirect root URL to the interactive docs."""
    return RedirectResponse(url="/docs")

# ---------------------------------------------------------------------------
# Fetch Finnhub news endpoint
# ---------------------------------------------------------------------------
@app.get("/finnhub/backfill_news", tags=["Finnhub"])
def backfill_news(
    api_key: str,
    tickers: list[str] = ["AAPL", "MSFT", "AMZN", "GOOGL", "TSLA"],
    start_date: str = os.environ.get("START_DATE", "2026-01-01"),
    end_date: str = os.environ.get("END_DATE", "2026-01-02"),
    window_days: int = 7,
    checkpoint_csv: str = "sp500_news_checkpoint.csv",
    output_csv: str = "sp500_news_full.csv"
):
    """
    Run the full Finnhub backfill workflow:
    - Initialize fetcher with API key
    - Fetch rolling range of news for tickers
    - Save incremental progress to checkpoint CSV
    - Export final results to output CSV
    """
    fetcher = FinnhubNewsFetcher(api_key=api_key)
    rows = fetcher.fetch_rolling_range(
        tickers=tickers,
        start_date=start_date,
        end_date=end_date,
        window_days=window_days,
        checkpoint_csv=checkpoint_csv,
    )
    fetcher.to_csv(output_csv)

    return JSONResponse(
        content=jsonable_encoder({
            "message": "Finnhub backfill completed",
            "tickers": tickers,
            "rows_fetched": len(rows),
            "checkpoint_file": checkpoint_csv,
            "output_file": output_csv
        })
    )

# ---------------------------------------------------------------------------
# Fetch GDELT news endpoint
# ---------------------------------------------------------------------------
@app.get("/fetch_news", tags=["GDELT"])
def gdelt_fetch_news(
    start_date: str = Query(os.environ.get("START_DATE", "2023-01-01"), description="Start date in YYYY-MM-DD format"),
    end_date: str = Query(os.environ.get("END_DATE", "2025-12-31"), description="End date in YYYY-MM-DD format"),
    window_days: int = Query(1, description="Window size in days"),
    sleep_between_calls: float = Query(3.0, description="Sleep time between API calls")
):
    """
    Fetch GDELT news for given companies and date range.
    This endpoint is designed to retrive news with more of 1 year old,
    since Finnhub only allows to fetch news with less than 1 year old.
    """

    companies = pd.read_csv(
        os.path.join(general["paths"]["input"], "s&p500.csv"),
        sep=";"
    )["Security"].tolist()

    fetcher = GDELTNewsFetcher(sleep_between_calls=sleep_between_calls)

    output_path = os.path.join(general["paths"]["news_links"], "gdelt_news_full.csv")

    fetcher.fetch_rolling_range(
        tickers_or_queries=companies,
        start_date=start_date,
        end_date=end_date,
        window_days=window_days,
        checkpoint_csv=output_path
    )

    return {
        "message": "Fetch completed",
        "companies_count": len(companies),
        "output_file": output_path
    }

#----------------------------------------------------------------------------
# Crawler endpoints
#----------------------------------------------------------------------------
@app.get("/crawler", tags=["Crawler"])
def crawl_links(file: _link_files): #type: ignore
    """
    Crawl links from a specified file and return the results.
    """
    urls = pd.read_csv(os.path.join(general["paths"]["news_links"], file.value))
    urls = urls.url.tolist()[:100]
    try:
        crawler_config = CrawlerRunConfig(
            # user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            magic=True
        )
        crawler = BulkCrawler(
            chunk_size=50,
            max_concurrent=10,
            date_identifier=file.value.split('.')[0],
            run_config=crawler_config
        )
        summary = asyncio.run(crawler.run(urls))
        return {'run_status': 'success', 'summary': summary}
    except Exception as e:
        tb_str = traceback.format_exc()
        return {"error": str(e), "traceback": tb_str}

#----------------------------------------------------------------------------
# Extract content endpoints
#----------------------------------------------------------------------------
@app.get("/get_available_files", tags=["Process News"])
def get_available_files(folder: __markdown_files): #type: ignore
    return os.listdir(os.path.join(general["paths"]["news_markdown"], folder.value))

@app.get("/process_markdown", tags=["Process News"])
def process_markdown(
    folder: __markdown_files = Query(..., description="Markdown folder to process"), #type: ignore
    file: str = Query(..., description="Markdown available file in the folder got from get_available_files")
):
    """
    Process a markdown file and extract its title and content.
    """
    extractor = MarkdownExtractor()

    # Extract the title and content
    path = os.path.join(general["paths"]["news_markdown"], folder.value, file)
    extractor = MarkdownExtractor(max_title_line_length=50000)
    article = extractor.extract_from_text(path)

    return {
        "title": article.title,
        "content": article.content,
        "source_url": article.source_url,
        "crawled_at": article.crawled_at
    }

#----------------------------------------------------------------------------
# Edgar api endpoints
#----------------------------------------------------------------------------
@app.get("/company_info/{ticker}", tags=["Edgar"])
def company_info(ticker: str):
    return agent.get_company_info(ticker)

@app.get("/filings/{ticker}", tags=["Edgar"])
def filings(ticker: str, form: FilingForm, limit: int = 5):
    return agent.get_filings(ticker, form=form._value_, limit=limit)

@app.get("/years_available/{ticker}", tags=["Edgar"])
def years_available(ticker: str, form: FilingForm):
    return agent.list_years_available(ticker, form=form._value_)

@app.get("/filing_by_year/{ticker}", tags=["Edgar"])
def filing_by_year(ticker: str, form: FilingForm, year: int):
    return agent.get_filing_by_year(ticker, form=form._value_, year=year)

@app.get("/latest_filing/{ticker}", tags=["Edgar"])
def latest_filing(ticker: str, form: FilingForm):
    return agent.get_latest_filing(ticker, form=form._value_)

@app.get("/financials/{ticker}", tags=["Edgar"])
def financials(ticker: str, form: FilingForm, year: int):
    result = agent.get_financials(ticker, form=form._value_, year=year)
    safe_result = jsonable_encoder(result)
    return JSONResponse(content=safe_result)

@app.get("/search_filings/{ticker}", tags=["Edgar"])
def search_filings(
    ticker: str,
    keyword: str,
    form: FilingForm,
    max_filings_to_search: int = 5
):
    return agent.search_filings(
        ticker,
        keyword=keyword,
        form=form._value_,
        max_filings_to_search=max_filings_to_search
    )
