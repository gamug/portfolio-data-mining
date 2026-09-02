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
inspectable result. `src/common/` (shared with `sec_edgar`, though `sec_edgar` takes a raw
ticker/CIK string and doesn't actually touch any of this) holds the pieces that aren't
Finnhub-specific: `errors.py` (`UpstreamDataError`) and `portfolio.py` (the
tracked-universe loader backing the `/universe` endpoints — fetched live from Wikipedia
and cached in-process, the same source `news_collector`/`extractor` already use, rather
than a committed CSV).

`portfolio.py`'s live scrape only ever answers "who is tracked today." Point-in-time
membership (`as_of=<date>` on `list_universe`/`resolve_symbol`, and on both `/universe`
routes) is handled by the sibling `src/common/universe_history.py`: it reconstructs
`valid_from`/`valid_to` per ticker from the ["Historical components of the S&P
500"](https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500) Wikipedia
article's change log (back to 1976), and persists it to a small **dedicated** SQLite file
(`data/universe.db`, `$UNIVERSE_DB_PATH`) — not `urls.db`, and not touched at all unless
`as_of` is passed, so `portfolio.py`'s default behavior and DB-independence are unchanged.
Two `cli/pricing_cli.py` subcommands drive it by hand (no scheduler): `universe-backfill`
(one-time reconstruction) and `universe-snapshot` (forward diff against today's roster).

`src/common/` used to also have `config.py` and `utils.py` (a `general` dict of
output/input scratch-directory paths, `check_repository()`/`init_repository()` to create
them at startup, and an unused `FileEnumFactory`) inherited from `finhub`'s original
monolith — none of it was ever written to by any code that actually shipped in this repo
(that was the old GDELT/generic-crawler pipeline's job, dropped when `finhub` split off).
Removed entirely, along with the `lifespan` startup hook in `apps/pricing_api.py` that
existed solely to call `init_repository()`.

## Running

```bash
# API (FastAPI/uvicorn)
.venv\Scripts\python.exe apps\pricing_api.py
# -> http://127.0.0.1:8004/docs

# CLI (direct — no server), one subcommand per endpoint, prints JSON
.venv\Scripts\python.exe cli\pricing_cli.py pricing AAPL --start 2024-01-01 --end 2024-06-01
.venv\Scripts\python.exe cli\pricing_cli.py resolve TWTR --as-of 2022-10-01
.venv\Scripts\python.exe cli\pricing_cli.py universe-backfill   # one-time, run this first
.venv\Scripts\python.exe cli\pricing_cli.py universe-snapshot   # run occasionally by hand
.venv\Scripts\python.exe cli\pricing_cli.py --help   # full subcommand list
```

Requires `FINNHUB_API_KEY` in `.env` (see `.env.example`) for both — the CLI defers
reading it until after argument parsing, so `--help` works without a key configured, but
every subcommand needs one (same as the API needing it to even start).

`cli/pricing_cli.py` is new — `finhub` never had a CLI, only the FastAPI app; this wraps
the exact same `StockPriceFetcher`/`FinnhubNewsFetcher`/`MarketDataClient`/
`common.portfolio` calls the API routes make.

## Endpoints

`GET /universe` and `/universe/resolve/{query}` both take an optional `?as_of=YYYY-MM-DD`
(point-in-time membership; 400 if it predates the backfilled coverage or no backfill has
been run yet). `/pricing/{ticker}`,
`/news/company/{ticker}`, `/news/market`, `/news/sentiment/{ticker}`,
`/market/profile/{ticker}`, `/market/peers/{ticker}`, `/market/basic_financials/{ticker}`.
