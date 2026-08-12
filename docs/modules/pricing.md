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
.venv\Scripts\python.exe apps\pricing_api.py
# -> http://127.0.0.1:8004/docs
```

Requires `FINNHUB_API_KEY` in `.env` (see `.env.example`).

## Endpoints

`GET /universe`, `/universe/resolve/{query}`, `/pricing/{ticker}`,
`/news/company/{ticker}`, `/news/market`, `/news/sentiment/{ticker}`,
`/market/profile/{ticker}`, `/market/peers/{ticker}`, `/market/basic_financials/{ticker}`.
