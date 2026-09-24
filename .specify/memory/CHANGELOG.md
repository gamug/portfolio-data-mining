# CHANGELOG.md — `portfolio-data-mining`

Legacy record of **closed** work items, moved here verbatim from
`.specify/memory/TASKS.md` so that file carries only open work. A work item is
closed once every task in it is checked, or explicitly superseded/moved elsewhere.
Task IDs are stable and never reused; `PLAN.md` keeps each work item's plan and
acceptance criteria. Ordered by work item number.

## Work item 3 — yfinance corporate-actions endpoint (code, moved from `portfolio-financial-analysis`)

- [x] **T-020** Add `StockPriceFetcher.get_corporate_actions(ticker,
      start_date, end_date)` to `src/pricing/fetcher.py`: `yf.Ticker(t).
      dividends`/`.splits`, filtered to the inclusive range by the series' own
      index date (tz-aware index normalized to `YYYY-MM-DD`), returning
      `{ticker, start_date, end_date, source: "yfinance", dividends: [{date,
      value}], splits: [{date, value}], warning}`; never raises — a yfinance
      failure yields empty lists plus `warning`. Tests in
      `tests/pricing/test_fetcher.py` mocking `yf.Ticker`: in-range dividends
      + splits, out-of-range rows excluded, empty range, tz-aware index,
      yfinance raising, the `1900-01-01`–`1900-01-02` probe range. → `PLAN.md`
      Work item 3, steps 1–2. **Done 2026-09-19** (PR #36): 8 tests, hermetic;
      the timezone test was mutation-checked (a UTC conversion makes it fail).
- [x] **T-021** Add `GET /pricing/{ticker}/actions?start_date=&end_date=` to
      `apps/pricing_api.py` (tag `Pricing`, `start_date > end_date` → 400,
      empty range → 200 with empty lists, never 404). → step 3. **Done
      2026-09-19** (PR #36): 6 API tests (`tests/pricing/test_pricing_api.py`)
      cover 200 on empty, 400 on a reversed range, 422 on a malformed date,
      and the candles route unaffected.
- [x] **T-022** Add an `actions` subcommand to `cli/pricing_cli.py` mirroring
      the route. → step 4. **Done 2026-09-19** (PR #36).
- [x] **T-023** Update `docs/modules/pricing.md`, and the endpoint/subcommand
      lists in the `apps/pricing_api.py` and `cli/pricing_cli.py` docstrings
      and `README.md` wherever they enumerate pricing routes. → step 5. **Done
      2026-09-19** (PR #36): `docs/modules/pricing.md` only — `README.md`
      gives one example per service and does not enumerate pricing routes, so
      it needed no change.
- [x] **T-024** Finalize the specs once the code exists: `FR-012`, `SPEC.md`
      §2.1 and §12 were written ahead of the code marked "planned" — drop that
      marker, and update the §10/NR-004 test count (213 today) and the
      constitution's "Executable cmds" test-count line. Separately, as its own
      reviewed change per the constitution's Governance section, amend
      Technological stock #2 and AI behavior #1 so `yfinance` is listed under
      `pricing` (MINOR bump). → `PLAN.md` Work item 3, "Constitution notes".
      **Partly done 2026-09-19** (PR #36): `SPEC.md` FR-012 ("planned" marker
      dropped), §2.1, §12 and the test count (→ 230). **Constitution half done
      2026-09-19**, on an explicit go-ahead and as its own PR: Tech stock #2
      (yfinance is used by both `news_collector` and `pricing`, named as the
      one exception to per-service scoping), AI behavior #1 (`pricing` pulls
      from Finnhub and yfinance), and the "Executable cmds" test count (213 →
      230). Constitution 1.5.0.
- [x] **T-025** Verify live: run `uv run apps/pricing_api.py`, `curl` the XOM
      range and the `1900-01-01` probe range from `PLAN.md` Work item 3's
      acceptance criteria, and run the `actions` CLI subcommand. State
      yfinance's unofficial/no-SLA posture in the PR. → acceptance criteria.
      **Done 2026-09-19** (PR #36), live against yfinance: XOM's four 2024
      quarterly dividends, NVDA's 10-for-1 split (`10.0`, 2024-06-10), the
      1900 probe range over real HTTP → 200 with empty lists, reversed range →
      400. The yfinance unofficial/no-SLA posture is stated in the PR.
- [x] **T-026** *(cross-repo)* After this lands and the pricing service is
      redeployed, hand off to `portfolio-financial-analysis`'s `T-052`
      (`QuantPricingClient.probe('XOM')` → `True`, then `quant
      backfill-actions` → `corpact-v1` rows). That check lives in PFA, not
      here. → acceptance criteria, PFA bullet. **Progress 2026-09-19:** landed
      (PR #36), and verified against the real client —
      `portfolio-financial-analysis`'s own `QuantPricingClient.probe('XOM')`
      returned `True` against this repo's merged code running locally (before:
      always `False`), and `.actions()` parsed XOM's four 2024 dividends and
      NVDA's `10.0` split. **Still open:** the redeploy of the service PFA's
      `PRICING_BASE_URL` points at, which is the operator's step, and PFA's
      `T-052` (`quant backfill-actions` on its production DB), which lives in
      that repo.
      **Done 2026-09-21** (checked off 2026-09-24): PFA's `T-052` ran live against
      the *deployed* gateway (`http://host.docker.internal:8000/pricing`) —
      `quant backfill-actions` fetched 503/503 assets, 0 errored, 7,481
      `corpact-v1` rows — so the redeploy happened and the handoff is complete.
- [x] **T-027** Reconcile both architecture artifacts (Portfolio Thesis +
      Portfolio Data Mining) per constitution AI behavior #11 — content only,
      never the title. → `PLAN.md` Work item 3, last acceptance bullet. **Done
      2026-09-19** (after PR #36 merged): Portfolio Data Mining (v23) and
      Portfolio Thesis (v24) now describe the endpoint as built — the pricing
      node/row, its route list, the pricing test count (38 → 55), and the
      yfinance contract — and the "queued" wording is gone. Content only,
      titles unchanged.

## Work item 4 — Fix sec_edgar filing-by-year/financials for multi-filing-per-year forms (code, priority)

- [x] **T-028** Add failing tests reproducing the bug (multiple same-year
      filings for one form) in `tests/sec_edgar/test_agent.py`, confirming
      today's `next()`-based code only returns one. → `PLAN.md` Work item
      4, step 1. **Done 2026-09-21**: `test_get_filing_by_year_returns_all_matches_for_form_and_year`
      (three 10-Qs, one different-year filing) plus three new
      `get_financials` disambiguation tests.
- [x] **T-029** Fix `EdgarAgent.get_filing_by_year` in `src/sec_edgar/agent.py`
      to return all matching filings as a list; update its docstring/
      response shape. → step 2. **Done 2026-09-21**: returns `{"success":
      True, "data": [...]}` (list comprehension over the year-filtered
      matches, most-recent-first); no-match is now an empty list, not an
      error.
- [x] **T-030** Fix `EdgarAgent.get_financials` to accept an optional
      `accession_number` disambiguator, per the design above; update its
      docstring. → step 3. **Done 2026-09-21**: 0/1/>1-match branches per
      `PLAN.md`; ambiguous-without-`accession_number` and
      unmatched-`accession_number` both error listing the candidates.
- [x] **T-031** Update `apps/sec_edgar_api.py` (`edgar_financials`) and
      `cli/sec_edgar_cli.py` (`financials` subcommand + usage docstring)
      for the new `accession_number` parameter. → step 4. **Done
      2026-09-21**.
- [x] **T-032** Update `src/sec_edgar/examples.py` and regenerate
      `docs/modules/edgar_examples.txt`; add the note to
      `docs/modules/sec-edgar.md`. → step 5. **Done 2026-09-21**:
      `examples.py` and `sec-edgar.md` updated; `edgar_examples.txt`
      regenerated from a real run against SEC EDGAR (`NAME`/`EMAIL` from
      `.env`) — network access turned out to be available in this
      environment. The captured output shows AAPL's three real 2025 10-Qs
      coming back from `get_filing_by_year`, confirming the fix live.
- [x] **T-033** Update `.specify/memory/SPEC.md` FR-004's requirement text/
      acceptance criteria to describe the corrected multi-filing behavior
      and `accession_number` disambiguation, and update the §10/NR-004
      test count. → `PLAN.md` Work item 4, acceptance criteria. **Done
      2026-09-21**: FR-004 text/acceptance criteria updated; test count
      230 → 234, `tests/sec_edgar/test_agent.py` 34 → 38.
- [x] **T-034** Reconcile the two architecture artifacts (Portfolio Thesis +
      Portfolio Data Mining) per constitution AI behavior #11 — content
      only, never the title. → same acceptance criteria. **Done
      2026-09-21**: both artifacts updated (test counts, the fix itself,
      footer changelog entries); titles unchanged.
- [x] **T-035** Verify live: run `apps/sec_edgar_api.py`, `curl`
      `filing_by_year` for a real ticker/10-Q/year with 3 filings and
      confirm all 3 come back; call `financials` with and without
      `accession_number` to confirm the disambiguation error and the
      success path; run the CLI equivalents. → acceptance criteria.
      **Done 2026-09-21**: `GET /edgar/filing_by_year/AAPL?form=10-Q&year=2023`
      returned all three real filings (accessions ending `-000077`,
      `-000064`, `-000006`); `GET /edgar/financials/AAPL?form=10-Q&year=2023`
      with no `accession_number` returned the ambiguity error listing all
      three; with `accession_number=...-000064` it returned a real income
      statement; the `10-K` (single-match) path on both routes was
      unaffected. `cli/sec_edgar_cli.py filing-by-year`/`financials
      --accession-number` mirrored the API exactly.
