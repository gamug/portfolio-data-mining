# sec_edgar — SEC EDGAR filings & financials

**Source:** `finhub/src/fundamental/edgar_tool.py`, `finhub/edgar_examples.py` →
`src/sec_edgar/` + `apps/sec_edgar_api.py`

The other of the two modules `finhub` was split into (the other is [pricing](pricing.md)).

Agent-facing wrapper (`src/sec_edgar/agent.py`'s `EdgarAgent`) around the `edgar`
(`edgartools`) SEC EDGAR library. Every public method returns a plain JSON-serializable
dict — never a custom SDK object — as either `{"success": True, "data": ...}` or
`{"success": False, "error": "<message>"}`; no method raises during normal use.

> **Naming note:** this module is `sec_edgar`, not `edgar` — `edgartools` itself is
> imported as `edgar` (`from edgar import set_identity, Company`), and a local package
> literally named `edgar` would shadow that import once `src/` is on `sys.path`.

`src/sec_edgar/examples.py` (adapted from `edgar_examples.py`) has runnable example calls
against `EdgarAgent`; `docs/modules/edgar_examples.txt` is the captured output from the
original run, useful as a response-shape reference without needing a live SEC EDGAR call.

## Running

```bash
# API (FastAPI/uvicorn)
.venv\Scripts\python.exe apps\sec_edgar_api.py
# -> http://127.0.0.1:8005/docs

# CLI (direct — no server), one subcommand per endpoint, prints JSON
.venv\Scripts\python.exe cli\sec_edgar_cli.py company-info AAPL
.venv\Scripts\python.exe cli\sec_edgar_cli.py --help   # full subcommand list
```

Requires `NAME` and `EMAIL` in `.env` (SEC EDGAR requires a real identity string for
programmatic access — see `.env.example`) for both.

`cli/sec_edgar_cli.py` is new — `finhub` never had a CLI, only the FastAPI app and the
fixed-ticker demo in `src/sec_edgar/examples.py`; this wraps the same `EdgarAgent` calls
the API routes make, with real ticker/form/year arguments instead of hardcoded "AAPL".

## Endpoints

`GET /edgar/company_info/{ticker}`, `/edgar/filings/{ticker}`,
`/edgar/years_available/{ticker}`, `/edgar/filing_by_year/{ticker}`,
`/edgar/latest_filing/{ticker}`, `/edgar/financials/{ticker}`,
`/edgar/search_filings/{ticker}`.
