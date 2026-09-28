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

> **Note:** `/edgar/filing_by_year` returns a *list* of matching filings — a
> form/year can have more than one (e.g. a company typically files three
> "10-Q"s per year, one per fiscal quarter), and an empty list means no
> match, not an error. `/edgar/financials` takes an optional
> `accession_number` query param, required to disambiguate when form+year
> matches more than one filing; get it from `/edgar/filing_by_year` first.

> **Note (T-118, APA revenue instance only):** `/edgar/financials`' `income_statement`
> runs through `correct_revenue_totals` (`src/sec_edgar/agent.py`) before it's returned.
> The actual mechanism, confirmed against live SEC data (CIK `0001841666`, APA's FY2023
> and FY2024 10-Ks): `edgar.xbrl.statements.income_statement().to_dataframe()`
> *synthesizes* a non-dimensional "total" row for a concept by summing that concept's
> dimensional members (e.g. `srt:ProductOrServiceAxis` breakdown rows) whenever the filer
> didn't tag a non-dimensional fact for it directly. That summation double-counts when one
> of the members is itself a parent whose value already includes its own children — APA's
> case: FY2023 `8279 (parent "Oil") + 7385 (children rolled into it) + 894 (unrelated
> member) = 16558`, a row edgartools then surfaces as if it were APA's own consolidated
> `us-gaap:Revenues`, when APA has never filed that concept at all (`data.sec.gov`
> `companyconcept` → 404) and the correct figure is `8279`. Same shape in FY2024:
> `9737 + 8196 + 1541 = 19474` vs. the correct `9737`. `correct_revenue_totals` detects
> this structurally for revenue only (a later, non-dimensional row whose label also reads
> as a revenue total and is materially smaller than the synthesized one — never a filer's
> own concept name), corrects it by subtracting the rows between the two when they're
> individually small enough to trust, or drops it (`None`, not guessed) otherwise, and
> now records every correction/drop it makes as a `{"concept", "column", "original",
> "corrected", "rule": "T-118"}` entry in a new top-level `data["corrections"]` list
> returned by `get_financials` — so callers can see exactly what was touched instead of
> trusting the numbers silently.
>
> **This is confirmed to be a general `edgartools` synthesis defect, not an APA/revenue
> quirk.** A full-universe scan of every stored filing found 163 mismatched
> parent-vs-summed-children values across 105 concept/period pairs, spanning 1,101
> synthesized non-dimensional rows total — revenue is only the one concept this PR
> corrects. `correct_revenue_totals` fixes **APA's revenue instance only**; it does not
> close `portfolio-financial-analysis`'s `T-117`/`T-118` (that repo's own local guard in
> `docs/model_fixes.md` stays in place as the load-bearing backstop, not a redundant one).
> A follow-up PR, still under `T-118`, is planned to validate every synthesized
> non-dimensional value generally against the filer's actually-filed facts (not just
> revenue, not just label-pattern detection) and mark or drop whatever doesn't
> reconcile.
