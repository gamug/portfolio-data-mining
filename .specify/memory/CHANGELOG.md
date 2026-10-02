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

## Work item 5 — Fix `sec_edgar`'s revenue-total contradiction (code, cross-repo origin)

**Closed 2026-10-02 — PR #44 (APA revenue instance, T-036–T-040), PR #45/#46 (the general
fix, T-042), and `portfolio-financial-analysis`'s own re-verification (T-041).**

- [x] **T-036** Trace `portfolio-financial-analysis`'s `T-117`/`T-118` root cause live
      against this repo: reproduce APA's (CIK `0001841666`) FY2023-2025 `us-gaap_Revenues`
      defect through `EdgarAgent.get_financials`, and confirm against `data.sec.gov`'s
      `companyconcept`/`companyfacts` APIs that APA has never filed a real
      `us-gaap:Revenues` fact at all. → `PLAN.md` Work item 5, "Why". **Done 2026-09-28**,
      mechanism confirmed and re-verified in PR #44 review: `edgartools==5.44.1`
      synthesizes a non-dimensional "total" row for a concept by summing that concept's
      dimensional axis members, and double-counts when one member is a parent whose value
      already includes its own children — APA FY2023: `8279 (parent) + 7385 (its
      children) + 894 (unrelated member) = 16558` synthesized vs. the correct `8279`;
      FY2024: `9737 + 8196 + 1541 = 19474` vs. `9737`. A full-universe scan of every
      stored filing found this is general, not APA/revenue-specific: 163 mismatched
      values across 105 concept/period pairs, 1,101 synthesized non-dimensional rows
      total.
- [x] **T-037** Add `correct_revenue_totals(income_statement)` to `src/sec_edgar/agent.py`,
      ported from `portfolio-financial-analysis`'s `Statements._label_total_correction`
      (`T-117`): a first aggregate revenue concept contradicted by a later, non-dimensional,
      label-matched, materially smaller "total revenue" row is corrected by subtracting the
      rows between the two, or dropped when those rows are too large to trust. Wire it into
      `get_financials`'s `income_statement`. Scoped to revenue only — **fixes APA's revenue
      instance, not the general synthesis defect**. → `PLAN.md` Work item 5, step 1-2.
      **Done 2026-09-28**.
- [x] **T-038** Tests in `tests/sec_edgar/test_agent.py`: `correct_revenue_totals` unit
      tests (APA's real FY2023 shape recovers $8,279M; FY2022's genuinely larger "and
      other" total left alone; "Total cost of revenues" excluded; an unsafe-to-derive
      contradiction drops the value; a dimensional row never picked as either candidate;
      a no-op with no aggregate concept present; no mutation of the input; the returned
      `corrections` list content for each case) plus one `get_financials` end-to-end test
      asserting `data["corrections"]`. → step 3. **Done 2026-09-28**: 8 tests updated
      (38 → 46 total); `uv run pytest tests/sec_edgar -q` and the full suite (242) both
      green.
- [x] **T-039** Update `SPEC.md` FR-004 with the correction's contract, the confirmed
      parent-vs-summed-children mechanism, and a live-verified APA figure citation; add
      the note to `docs/modules/sec-edgar.md`, explicitly scoped as "APA revenue instance
      only". → step 4, acceptance criteria. **Done 2026-09-28**.
- [x] **T-040** Verify live against real SEC EDGAR data (`NAME`/`EMAIL` set, no mocking,
      no network calls skipped): every available APA 10-K (FY2021-FY2025, filed
      2022-2026) and every 2024 10-Q. → acceptance criteria. **Done 2026-09-28**: FY2023
      10-K → $8,279M/$11,075M/$7,985M (2023/2022/2021 columns); FY2024 10-K (filed 2025)
      → $9,737M/$8,279M/$11,075M; FY2025 10-K (filed 2026) → $8,920M/$9,737M/$8,279M —
      exact match to `portfolio-financial-analysis`'s `T-117` acceptance figures. FY2021's
      own 10-K (filed 2022) is correctly left untouched at $1,082M (a pre-existing,
      differently-shaped too-small defect, that repo's own `T-095`, out of this fix's
      scope). All three 2024 10-Qs (accessions `...-000003`, `...-000013`, `...-000008`)
      resolved correctly, including both YTD columns. `data["corrections"]` recorded each
      correction/drop made, live-verified against the same filings.
      **Superseded in part by `T-042`, 2026-09-30**: FY2021-filed-2022's $1,082M is no
      longer left untouched — `T-042`'s general check also catches it (APA never filed
      `us-gaap:Revenues` non-dimensionally in any period, T-095's defect included) and
      drops it to `None`. This extends correctness to a case this task's own reconstruction
      couldn't reach, not a regression of the figures above, which are unaffected.
- [x] **T-041** *(cross-repo)* Hand off findings to `portfolio-financial-analysis`'s
      `T-118` — this fix corrects APA's revenue instance only and does **not** close
      `T-117`/`T-118` there; leave both open until the general synthesis defect (T-042) is
      addressed and that repo's own re-verification against a redeployed `sec_edgar`
      confirms nothing is left to correct on APA. → `PLAN.md` Work item 5 acceptance
      criteria; that repo's own `T-118`. **Done 2026-10-02**: `portfolio-financial-analysis`
      re-verified the redeployed gateway (carrying `T-042`) and closed its own
      `T-117`/`T-118` in its PR #104 on 2026-09-30 — the cross-repo condition this task was
      waiting on.
- [x] **T-042** *(follow-up, still under this work item's `T-118` origin)* General fix:
      validate every synthesized non-dimensional value (not just revenue, not just
      label-pattern detection) against the filer's actually-filed facts
      (`data.sec.gov` `companyconcept`/`companyfacts`, or an equivalent structural check
      within `edgartools`' own output) across all 163 mismatched values/105 pairs found
      in the full-universe scan, and mark or drop whatever doesn't reconcile. → `PLAN.md`
      Work item 5, step 5. **Done 2026-09-30**: `reconcile_with_filed_facts` added to
      `src/sec_edgar/agent.py` — validates every non-dimensional row on all three
      statements against the already-loaded filing's own XBRL facts
      (`xbrl.facts.query()`, no extra `data.sec.gov` call needed), replacing label-pattern
      detection with the filing's own ground truth. Runs after `correct_revenue_totals`,
      skipping whatever it already corrected. 21 new tests (`tests/sec_edgar/test_agent.py`
      — 66 total in that file, 262 full suite), plus 3 live-verification passes that each
      caught and fixed a real bug before landing: (1) a raw-filed-fact sign convention
      (contra accounts like `TreasuryStockCommonValue`, cash-flow decreases) was initially
      flagged as a false "mismatch" and had its sign flipped — fixed by comparing
      magnitude only, matching the original full-universe scan's own methodology
      ("excluding sign-convention flips"); (2) an instant-lookup fallback added to catch a
      cash-flow statement's "beginning/end of period" balance (genuinely an instant fact
      under a duration column) silently substituted the *wrong* endpoint's value for the
      "beginning of period" row — removed rather than fixed with a label heuristic; that
      row now drops instead of guessing wrong, a documented known gap. Live-verified: APA
      FY2021-FY2025 10-Ks + all three 2024 10-Qs (T-118's reconstruction unaffected
      wherever it applies; FY2021-filed-2022 now also drops to `None`, see the note on
      `T-040` above); MSFT's FY2024 10-K (clean filer, only the known cash-roll-forward
      gap flags); SNA's FY2026 10-Qs (the PR #44 review's cited non-revenue example —
      genuinely-filed non-dimensional rows like `OperatingIncomeLoss` pass through,
      never-filed ones like `OperatingExpenses` drop). `SPEC.md` FR-004,
      `docs/modules/sec-edgar.md`, `PLAN.md` Work item 5 all updated. Merged as PR #45.
      **Follow-up, same day**: PR #45's own review (`sourcery-ai`, 2026-09-30) found a
      correctness gap in the wiring, not the algorithm — `reconcile_with_filed_facts`'
      live `xbrl.facts.query()` call could raise for a statement, and `get_financials`'
      one outer `try/except` would convert the *entire* response to `success: False`,
      discarding the other two statements' already-successfully-rendered data along with
      it. Fixed in a follow-up PR (`fix/t042-reconciliation-failure-isolation`, #46): a new
      `_safe_reconcile_with_filed_facts` wrapper at each of the three call sites in
      `get_financials` catches a `reconcile_with_filed_facts` failure per statement and
      returns that statement's rows rendered-but-unvalidated (empty corrections for it)
      instead of failing the whole call — `reconcile_with_filed_facts` itself stays
      exception-raising and unit-testable as before. 4 new tests (69 total in
      `tests/sec_edgar`, 265 full suite).
      **Follow-up to the follow-up, same day**: PR #46's own review (`sourcery-ai`,
      2026-09-30) flagged that the broad `except Exception` also swallows a genuine bug in
      this module's own logic, and that returning empty corrections on failure looks
      identical to "verified, nothing to correct." Addressed without narrowing the catch
      (no fixed, enumerable set of "expected" failure types exists for a live third-party
      library, and narrowing risks reopening the exact PR #45 failure this wrapper
      prevents): failures now log at `error` level with a full traceback, and
      `_safe_reconcile_with_filed_facts` returns a third value (an error message, `None` on
      success) that `get_financials` surfaces in a new top-level `data["reconciliation_
      errors"]` list (`{"statement", "error"}`, empty when nothing failed), so a caller can
      always distinguish the two cases. Same PR #46, additional commit.
