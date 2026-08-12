# pricing — Finnhub stock pricing, news & market data

**Source:** `finhub/src/{trading,market,news}/` → `src/pricing/` + `src/common/` +
`apps/pricing_api.py`

One of the two modules `finhub` was split into (the other is [sec_edgar](sec-edgar.md)).
Wraps the Finnhub API (with a `yfinance` fallback for pricing) — no SEC/EDGAR code here.

| File | Class | Does |
|---|---|---|
| `src/pricing/fetcher.py` | `StockPriceFetcher` | Daily OHLCV candles. Tries Finnhub's `stock_candles` first; free-tier Finnhub no longer returns historical candles, so this transparently falls back to `yfinance`. |
| `src/pricing/market_data.py` | `MarketDataClient` | Company profile, peers, basic financials. |
| `src/pricing/news.py` | `FinnhubNewsFetcher` | Company news, general market news, news-sentiment score. Free tier only serves ~12 months of company news. |

Every method returns `{"success": bool, "data"|"error": ...}` — upstream provider
failures (premium-gated endpoints, bad tickers) never raise; they come back as a clean,
inspectable result. `src/common/` (shared with `sec_edgar`) holds the pieces that aren't
Finnhub-specific: `config.py` (paths/`BASE_DIR`), `utils.py` (`init_repository` — creates
the configured output directories), `errors.py` (`UpstreamDataError`), `portfolio.py`
(the tracked-universe loader backing the `/universe` endpoints — fetched live from
Wikipedia and cached in-process, the same source `news_collector`/`extractor` already use,
rather than a committed CSV).

## Running

```bash
# API (FastAPI/uvicorn)
.venv\Scripts\python.exe apps\pricing_api.py
# -> http://127.0.0.1:8004/docs

# CLI (direct — no server), one subcommand per endpoint, prints JSON
.venv\Scripts\python.exe cli\pricing_cli.py pricing AAPL --start 2024-01-01 --end 2024-06-01
.venv\Scripts\python.exe cli\pricing_cli.py --help   # full subcommand list
```

Requires `FINNHUB_API_KEY` in `.env` (see `.env.example`) for both — the CLI defers
reading it until after argument parsing, so `--help` works without a key configured, but
every subcommand needs one (same as the API needing it to even start).

`cli/pricing_cli.py` is new — `finhub` never had a CLI, only the FastAPI app; this wraps
the exact same `StockPriceFetcher`/`FinnhubNewsFetcher`/`MarketDataClient`/
`common.portfolio` calls the API routes make.

## Endpoints

`GET /universe`, `/universe/resolve/{query}`, `/pricing/{ticker}`,
`/news/company/{ticker}`, `/news/market`, `/news/sentiment/{ticker}`,
`/market/profile/{ticker}`, `/market/peers/{ticker}`, `/market/basic_financials/{ticker}`.
