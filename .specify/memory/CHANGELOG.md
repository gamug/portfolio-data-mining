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

## Work item 6 — Expose the cover-page share count (code, cross-repo origin)

**Closed 2026-10-02 — PR #48 (`T-043`, the only task).**

Origin: `portfolio-financial-analysis` `T-132(a)` (market cap from a point-in-time share count).

- [x] **T-043** In `sec_edgar`, `get_financials` returns the filing's cover-page share count
      as a new, additive top-level field — the three statements, `corrections` and
      `reconciliation_errors` are unchanged:
      `"cover": {"shares_outstanding": [{"value": <shares>, "as_of_date": "<the dei fact's
      instant date>", "class_member": <null | "us-gaap:CommonClassAMember", ...>}], "error": <null | str>}`.
      Source: the filing's own `dei:EntityCommonStockSharesOutstanding` XBRL facts via
      `xbrl.facts.query()` (T-042's mechanism; no extra network call; never
      `CommonStockSharesIssued` or a weighted average). `as_of_date` is the fact's own
      instant date, returned exactly, never replaced by the period end. A multi-class
      filer gets one entry per class plus a non-dimensional total only if the filer
      actually filed one — never a total summed here (T-042's rule). Missing → empty list,
      never a guess; 10-K and 10-Q alike. Failure isolation (PR #46's pattern): a failed
      read returns `cover` empty plus `cover.error` (`"<ExcType>: <message>"`, `null` on
      success) and never fails the response; it is deliberately **not** put in
      `reconciliation_errors` (PR #48 review — see below). → `SPEC.md` FR-004.
      **Done 2026-10-02**: `cover_shares_outstanding` / `_safe_cover_shares_outstanding`
      in `src/sec_edgar/agent.py`; 15 new hermetic tests in `tests/sec_edgar/test_agent.py`
      (69 → 84 there, 265 → 280 full suite) using facts captured live into
      `tests/sec_edgar/fixtures/cover_shares_facts.json` — single class (PG 10-K/10-Q),
      multi-class (GOOGL 10-K, BRK-B 10-Q), missing, a forced read failure (unit and
      end-to-end), plus edge cases; the dimension filter was mutation-checked (removing
      it fails the NEE/untrusted-fact tests). **Live-verified, no mocks**, latest 10-K
      and 10-Q of each, against SEC's `companyconcept` API where it carries the accession
      and against the filing's own cover-page text otherwise — PG 2,324,433,060 as of
      2026-07-31 (not the ~4.0B issued), XOM 4,166,763,453, PM 1,556,679,579, NEE
      2,083,521,964, HUM 120,595,967, MCD 710,398,642, MSFT 7,425,545,491 (10-K), all
      exact; GOOGL 10-K three entries 5,822M/837M/5,438M (A/B/C) as of 2026-01-28 and
      BRK-B 10-K 511,820 (A) / 1,389,605,139 (B) as of 2026-01-31, matching the cover
      pages (and GOOGL 10-Q 5,868M/835M/5,527M, BRK-B 10-Q 488,450 / 1,408,035,161).
      Findings from live verification: (1) NEE's filings also carry `1,000` shares under
      `dei:LegalEntityAxis` (Florida Power & Light, a co-registrant, listed separately on
      NEE's cover) — **not** a share class, so only a fact that is non-dimensional or
      dimensioned solely by `us-gaap:StatementClassOfStockAxis` is returned; (2)
      `companyconcept` lags — XOM's and NEE's latest 10-Qs were not yet in it, and HUM has
      no `companyconcept` rows at all — those were checked against the cover pages instead
      (all exact).
      **Follow-up, same day (PR #48 review, `eldova1702`)**: the original design reported a
      failed cover read as a `{"statement": "cover", "error"}` entry in
      `reconciliation_errors`, mirroring T-042. That was wrong: the list means "a statement
      came back unvalidated", and `portfolio-financial-analysis` (PR #104) rejects the whole
      filing when it is non-empty — a cover failure leaves every statement valid, so it would
      have discarded an otherwise good filing. The failure now lives inside the cover block
      (`"cover": {"shares_outstanding": [], "error": "<ExcType>: <message>"}`, `error` is
      `null` on success) and `reconciliation_errors` stays about the three statements only.
      Approved and merged as PR #48.

## Work item 7 — Fix point-in-time universe history reconstruction anomalies & data patches (code + data, T-044)

**Closed 2026-10-09 (`T-044`, the only task).**

- [x] **T-044** Implement S&P 500 history rebuild corrections:
  1. Fallback start date for open intervals from roster's "Date added" (clamped to earliest table date).
  2. Process change row addition before removal in `_reconstruct_intervals` so corporate restructurings (e.g. 21CF FOXA/FOX) resolve before opening predecessor intervals.
  3. Versioned data patches in `src/data_mining/universe_patches.py` citing S&P DJI press releases (URL + publication date) covering renames SATS/FLT/RE/FB, combinations WRK/SW with MWV/WRK, spin-offs FTRE/PHIN, and GOOG/GOOGL swap replacement.
  4. Guard in `backfill_from_changes`: check `wikipedia_changes_backfill` row count, so snapshot rows don't falsely block backfill.
  5. Hermetic tests covering all fixes and patch citations.
  **Done 2026-10-09**: Backfilled 894 intervals into `/workspaces/thesis/data/universe_history.db`.
  Dates verified: 2018-01-02 (504), 2022-01-03 (505), 2024-01-02 (503), 2026-10-01 (504).
  Shared pilot file `/workspaces/thesis/data/universe.db` unchanged (`04adf644f892597d5de1b60f11c0cd6cbc271ed1`).

## Work item 9 — GICS sector/sub-industry for the S&P 500 leaver stints (T-046)

**Closed 2026-10-14 (`T-046`, the only task; PR #51, merged `9006cf6`).**

Requested by `portfolio-financial-analysis` (its `T-153` part 3, blocking its
`T-100` full-universe run): the delivered `universe_history.db` has
`gics_sector`/`gics_sub_industry` filled for the 503 `live_snapshot` rows and
empty for every `wikipedia_changes_backfill` row, so no company that left the
index carries a classification. Fill the 90 leaver stints (`valid_to >=
2022-01-01`, CIK with no open stint) from the revision history of Wikipedia's
"List of S&P 500 companies" page, and record each label's revision citation.
Full request: `portfolio-financial-analysis`'s
`docs/upstream_requests/data_mining_leaver_gics.md`.

- [x] **T-046** Add `src/data_mining/universe_gics.py` (MediaWiki revision-list +
      content fetch, header-aware constituents parser, walk-back picker bounded by
      `valid_from`, report-only stability check) and the `queries` citation columns
      (`gics_revision_id`/`gics_revision_timestamp`) with `fetch_gics_targets`/
      `update_gics` (a stint-keyed UPDATE, never `write_intervals`); wire the
      `universe-gics` CLI subcommand (refuses `--out == --source` and the two
      canonical DB names). Hermetic tests in `tests/data_mining/test_universe_gics.py`;
      update `docs/modules/data-mining.md` and `SPEC.md` FR-013/NR-004/§5/§10. Build
      the new `/workspaces/thesis/data/universe_history_gics.db` and report its sha1,
      the 90-row fill table, the unfilled list, and the full-row diff vs the source.
      → `PLAN.md` Work item 9. **Done 2026-10-14** (PR #51): **87 of 90** stints
      filled, each with its revision id + timestamp; **3 unfilled** and reported —
      MBC (MasterBrand, `cik_not_found`: the page never carried a constituents row
      for CIK `0001941365`), FTRE/PHIN (`no revision dated within valid_from..valid_to`:
      2-day spin-off stints with no page revision in their window). Stability check:
      **87 comparable, 0 differences** (all 87 had a same-CIK+symbol row ≥30 days
      older). Parser: **225 revisions parsed, 0 rows dropped**; the builder raises on
      a non-zero drop count and records each skipped walk-back candidate's reason.
      New file `/workspaces/thesis/data/universe_history_gics.db` sha1
      `db5b16290b03a191b55215e8b98fc893a93f0e5b`; source `universe_history.db`
      (`fddbdccb71ab4243a0fb76b3027774474d352b99`) and `universe.db`
      (`04adf644f892597d5de1b60f11c0cd6cbc271ed1`) unchanged. Full-row diff of all
      894 rows: only the four GICS columns changed, only on the 87 filled target rows;
      0 `live_snapshot` rows touched. Review follow-up (PR #51 second commit
      `512505a`): the read path no longer migrates (columns added only to the
      builder's output copy via `migrate_gics_columns`; `_row_to_public_dict` returns
      `None` for an old-schema file), and the parser-drop count is enforced. 317 tests. Post-merge review follow-ups (code/tests/docs only,
      no data change): resumable, atomic `build_gics_db`; real `NAME`/`EMAIL`
      User-Agent; public `queries.connect`. 333 tests.

## Work item 10 — Stop tagging articles with the wrong company (code + data, origin: `portfolio-nlp` T-128)

Requested by `portfolio-nlp` (its Work item 15, decision D3 = fix at the
source, 2026-10-14): the discovery matcher `BaseConnector._company_pattern`
accepts a ticker or the *first word* of the company name appearing anywhere in
a URL slug or title, so tickers that are English words (`A`, `ON`, `IT`,
`ARE`, …) and generic first words (`Trade`, `Bank`, `First`, `Dollar`, …) tag
unrelated articles. → `PLAN.md` Work item 10, `SPEC.md` §13 item 10 and FR-014.

Task IDs: `T-047`–`T-052` are intentionally unused here. `portfolio-nlp` already
cites `T-053`–`T-059` for this work item (its Work item 15, PR
`gamug/portfolio-nlp#102`), so the IDs were kept stable rather than renumbered
after the fact; IDs are never reused, so they stay reserved. `T-060` (content-quality
gate) and `T-062` (Work item 12, the matcher follow-up; `T-061` belongs to Work item 11, the leaver prices) were added later and are data-mining-only, so `portfolio-nlp` does not cite them.

- [x] **T-053** Commit a rerunnable, read-only audit under `scripts/` that
      reproduces the 2026-10-14 counts from `urls.db` per `(ticker,
      connector, strategy/source)`: ticker-only vs name-token-only vs both
      matches, the eight English-word tickers, the generic-first-word
      companies, and the `ddg` rows not yet broken out. Recompute every
      figure quoted in `PLAN.md`/`SPEC.md` §13 item 10 per connector, and
      count separately the rows only the substring shortcut
      (`ticker.lower() in url.lower()`, failure mode 3) would accept. → step 1.
      Done: `scripts/audit_ticker_tagging.py` (every breakdown is derived from
      `(ticker, company, domain, source)` cells, so the word-ticker and
      generic-word tables are split per connector/source); results in
      `PLAN.md` Work item 10.
- [x] **T-054** Measure each failure mode's real error rate. Build **one**
      shared labeled sample of `(url, ticker)` pairs from `urls.db`,
      stratified by connector and match mode (ticker word-boundary / ticker
      substring shortcut / name-token-only / both) and including
      known-correct pairs, and label each pair (is the article about that
      company: `yes`/`no`/`unclear`). **Labeling is done in this repo, by hand;
      it does not depend on `portfolio-nlp`** (data flows from this repo to
      that one, never back). Commit the sample (pairs + labels) so it can be
      reused: `portfolio-nlp` T-117 reads it, and the retention
      measurement of the implemented rule (the T-057 fresh check) is added to the same
      sample's evidence. The committed file carries
      `url`, `ticker`, `company`, `match_mode`, `label`, `labeled_by`
      (`hand`/`assistant`) and the assistant's `assistant_label`. Report; the
      rule is chosen from this, not assumed. → step 1.
      Built: `scripts/ticker_tagging_sample.py` + `data/ticker_tagging_sample.csv`
      (263 pairs, all hand-labeled by the maintainer; an assistant's first pass
      agreed on 91%), `data/ticker_tagging_manual.csv` (the **filled** labeling
      sheet with the maintainer's notes, excerpts removed) and
      `scripts/score_ticker_rules.py` (reproduces the prototype-rule table);
      results and the duplicate/quality analysis are in `PLAN.md` Work item 10.
      The maintainer reviewed the two labels that looked inconsistent with
      their notes, `MGM` id 317362 and `DE` id 180987, and approved them as
      `no` (2026-10-15); known-correct retention of record is 17/20.
- [x] **T-055** Record the matching-rule design (starting direction:
      explicit ticker forms only, full normalized name + curated aliases,
      common-word stoplist for a lone first word; no new dependency) with the
      T-054 numbers behind it, and get it approved before coding. Division of work
      decided (`PLAN.md` Work item 10 step 2): strict deterministic rules here,
      semantic relevance in `portfolio-nlp`, questionable pairs quarantined. Decide, with the
      per-path rates in `PLAN.md` (`ddg` 7% wrong, `sitemap` 73%): use the
      company-name search query as evidence for `ddg` rows (and record the query
      per row), and attribute each `sitemap` article once instead of testing
      every company against every URL. The same
      decision sets the **measurable targets** — a maximum wrong-company rate
      and a minimum retention of known-correct pairs on the shared labeled
      sample — and writes both numbers, plus the final rule, into `SPEC.md`
      FR-014 and the `PLAN.md` acceptance criteria before any code is written.
      **Design approved 2026-10-15 (maintainer: "take care not to exclude").**
      Validation sample (`data/ticker_validation_sample.csv`, 100 fresh pairs,
      labeled by the maintainer) showed the first draft missing its 15% target
      (`accept` 20.7% wrong); fixed (ordinary-word names, `&`/`and`, abbreviation
      list, aliases; `ddg` promotion withdrawn) and re-scored in-sample. **Closed
      2026-10-15:** the targets are written into `SPEC.md` FR-014 and the `PLAN.md`
      acceptance criteria (wrong rate among `accept` at most 15%, correct pairs kept
      at least 95%, known-correct `accept` at least 90%); they are verified by the
      fresh hand-labeled check in T-057's dry run (60 `accept`, 40 `reject`), not
      here. Outcomes of the open questions: the `ddg` query is stored as provenance
      only (it does not change a class), and each sitemap article is **not** decided
      once across companies; the class is per `(url, ticker)` pair and
      `multi_company` is derived (T-056c). All figures reproduce with
      `scripts/score_ticker_rules.py` and `ticker_tagging_sample.py report`;
      details in `PLAN.md` Work item 10. → step 2.
- [x] **T-056** *(done 2026-10-15: `a`, `b`, `c`; split into `T-056a`, `T-056b`, `T-056c` so each piece is
      reviewable and shippable; the IDs stay sub-numbered because `portfolio-nlp`
      cites only `T-053`–`T-059`. Order: `a` (no schema) → `b` (constitution #10
      confirmed 2026-10-15, starts after PR #58 is reviewed) → `c`; T-057 starts after all three.)* Implement the approved
      three-class rule. `T-056` is done when all three are.
- [x] **T-056a** *(no schema change; done 2026-10-15, PR #58)* The matcher and the connectors. Put the pure
      module in the **shared library** (`data_mining/matching.py`; both `news_collector`
      and `extractor` already import `data_mining.db`/`schema`, so the direction stays
      stages → `data_mining` and the two stages import nothing from each other): the logic
      of `scripts/score_ticker_rules.py` `design_class` (alias map, ordinary-word-name
      rule, `&`→`and`, abbreviation list, path-only URL matching, no new dependency),
      returning the class and the deciding signal (`name`, `alias`, `explicit_ticker`,
      `upper_ticker`, `weak_ticker`, `weak_first_word`, `abbreviation`, `none`). Expose it to
      the connectors through one new **concrete** `BaseConnector.classify_match(url, title,
      company, ticker) -> "accept" | "review" | "reject"`; the abstract
      `url_matches_company` keeps its signature and, in each of the **seven** connectors
      that override it (`cnbc`, `ft`, `investing`, `nasdaq`, `seeking_alpha`, `stocktwits`,
      `yahoo_finance`), returns `classify_match(...) != "reject"`; the raw
      `ticker.lower() in url.lower()` shortcut the last four ran first is removed; the
      base-class docstring postcondition ("ticker OR significant company name token is
      sufficient") is rewritten. The rule is monotone (more text can only add evidence), and
      a test locks that. Hermetic parametrized tests **per connector** for all three
      classes (ambiguous tickers `A`/`ON`/`IT`/`ARE`, generic first words `Bank`/`Trade`/
      `First`/`News`, aliases, `&`/`and`, abbreviations → `review`, the substring cases, host
      not matched); only `test_cnbc.py` covers `url_matches_company` today (and asserts the
      permissive rule), so update it deliberately and add the six missing files.
      **Done when:** `ruff check`, `ruff format --check`, `mypy`, `pytest` clean;
      FR-014's tests (per connector) pass; the base-class docstring and
      `docs/modules/news-collector.md` describe the matcher; mergeable without `b`/`c`.
      **`api` feeds are out of the rule's scope:** the `stocktwits` and `yahoo_finance`
      connectors build `DiscoveredURL(source="api")` directly from ticker-keyed feeds
      (message URLs, provider news; no company in the URL), the rule was never measured
      on them and the corpus holds none, so they keep `accept` by construction
      (`match_reason = 'feed'`), stated in FR-014; `rss` entries go through the sitemap
      strategy's filter and **are** classified, with their feed title. → step 3.
      **Results (T-056a, PR #58):** signals as above plus `cashtag`. Exchange forms (`NYSE: X`,
      `Nasdaq: X`, `NYSE American`, `NYSE MKT`, `NYSEARCA`, parenthesised or not) accept; a bare `(X)`
      does not; `$X` accepts, but for an ordinary-word ticker or an abbreviation it is `review`.
      Corpus class sizes: re-tag `accept` 104,614 / `review` 51,295 / `reject` 325,423; discovery
      86,985 / 63,162 / 331,185. `score_ticker_rules.py --check` reproduces the PLAN table (both
      samples, both modes) and exits 1 on drift. Known limitation, measured with `--names`: 59,956
      `accept` rows rest on a one-word name, about 12,400 of them dictionary words (Apple 9,890).
      T-057's fresh check is the acceptance evidence.
- [x] **T-056b** *(done 2026-10-15; shared-schema change; constitution AI behavior #10 **confirmed by the
      maintainer on 2026-10-15** for the exact columns, the `skipped`+`reject` convention, the
      queue upsert and the extractor's promote-only update listed here: they touch the
      two-package DB contract. Starts after PR #58 is reviewed; cite the confirmation and its
      date in this task's PR description.)* Storage, queue
      and API. **Migration via the real mechanism in `data_mining/schema.py`:** add the
      columns to `_DISCOVERY_DDL`, bump `SCHEMA_VERSION` 1 → 2, add a `run_migrations`
      step calling `db.ensure_columns("discovered_urls", {...})` (the pattern of
      `_migrate_legacy_sector_column`; forward-only, idempotent) and an index
      `idx_match_class` on `discovered_urls(match_class)`; a test upgrades an old-schema
      database and leaves a current one untouched. Columns: `match_class TEXT NOT NULL
      DEFAULT 'accept'`, `match_reason TEXT`, `query TEXT` (the `ddg` search query that
      returned the row; provenance only, it never changes the class). **Status
      convention, distinct from the existing `skipped`:** `URLStatus` already has
      `skipped` (paywall / irrelevant after inspection; `mark_skipped`/`mark_skipped_by_id`
      set it and write their reason into `title`). A matcher-rejected pair is
      **`status = 'skipped' AND match_class = 'reject'`**, written at insert time
      (`DiscoveredURL` gains `match_class`/`match_reason`/`query`; the queue's insert path
      writes `status = 'skipped'` for `reject` and never touches `title`); `mark_skipped`
      keeps its meaning and is never used for rejects. It is not the default `'pending'`
      because the extractor (`extractor/db.py` `get_urls_by_status`/`get_pending_urls`) and
      the queue (`storage/queue.py` `get_pending` and the per-ticker counts filtering
      `status='pending'`) select by `status` only. **Upsert, not `INSERT OR IGNORE`:**
      `enqueue_batch` ignores a duplicate `(url, ticker)`, which would discard a promotion
      when a pair first stored as `reject` (sitemap, no title) is rediscovered with a title
      (`ddg`). On conflict keep the higher class (`reject` < `review` < `accept`), fill
      `query`/`match_reason` when null, and reset `status` from `skipped` to `pending` when
      the class rises above `reject`. **API:** `/discovered` responses include
      `match_class` and `match_reason`, the list gains a `match_class` filter, and the
      `status` filter description says `skipped` includes matcher-rejected rows. Tests: the
      migration; a `reject` row is never returned by `get_pending_urls`/`get_pending` and an
      `accept` row is; the insert keeps a `reject` row's `title`; `mark_skipped` leaves
      `match_class` alone; sitemap-then-ddg re-discovery promotes the row and resets
      `status`; the API listing and filter. **Done when:** the same four gates clean, the migration test and the `schema.py` docstring (`SCHEMA_VERSION` 2) updated, `SPEC.md` FR-014 and `docs/modules/news-collector.md` describe the columns, and the maintainer's #10 confirmation is recorded here. → step 3.
      **Results (T-056b):** the promotion runs in `URLQueue._promote_duplicates` (portable SQL, the dialect's
      `upsert` can only overwrite with `excluded.c`), after the insert; a row skipped for another reason (paywall)
      is not reset by a promotion. `match_class` has a `CHECK` constraint and `enqueue_batch` validates it before any write; `stats()` gains `by_match_class`; the promotion UPDATE is conditional in SQL (no stale status write-back, no demotion by a stale read) and counted (`last_promotion`, `promoted=` in the logs); the extractor's `get_status_counts`/`/status`/CLI line leave rejects out; `get_urls_by_status` excludes `reject` rows and the crawler's reset answers 409 for one. The API surface is the collector's `/discovery/query` and `/discovery/pending`
      and the crawler's `/discovered` (the task text's `/discovered` is the crawler's route). Existing rows default to
      `accept` until the T-057 re-tag. The strategies still build `DiscoveredURL` with the default class, so nothing
      is quarantined until T-056c.
- [x] **T-056c** *(done 2026-10-15)* Strategies, fetch policy and re-evaluation (after `a` and `b`).
      **Strategies stop dropping:** `ddg` and `sitemap` (`_dedupe_and_filter`) call
      `classify_match` and keep `review` and `reject` rows with their class and (for `ddg`)
      `query` instead of `continue`, so the quarantine exists for new crawls; `accept`
      and `review` rows stay `pending` (a `review` pair needs its body for
      `portfolio-nlp`'s decision), `reject` rows are `skipped`. **Discovery vs fetch:** the
      class is computed at discovery from the URL (and the title for `ddg`), then
      **re-evaluated after the fetch** with the stored title; monotone, so it can only
      promote, never demote. **Who re-evaluates:** the extractor, through its existing
      post-fetch update of `discovered_urls` (`extractor/db.py`), calling
      `data_mining.matching` (no import of `news_collector`) and writing only
      `match_class`/`match_reason`, guarded in SQL so it can only promote. It applies to
      **fetched** rows, i.e. `accept`/`review` ones (`review`→`accept` when the title
      supplies the evidence). A `reject` row is never fetched, so a `sitemap` `reject` row
      (no title at discovery) stays `reject` unless a feed title was supplied with it
      (`entry.title` of an RSS entry). Accepted cost: about 5,800 pairs that are `reject`
      at discovery but would be `review` or `accept` with a title are never fetched;
      `hidden as reject` was 0.0% in discovery mode on both labeled samples, and the
      T-057 check measures it again. **`multi_company` is derived, not stored:** a URL
      with two or more `accept` pairs is multi-company (108,313 of the 320,763 stored
      URLs carry several tickers); the pair stays one row per `(url, ticker)`. Tests: the
      strategies keep `review`/`reject` rows with their class and `query`; the extractor
      promotes a fetched `review` row and never demotes. **Done when:** the four gates clean, `scripts/score_ticker_rules.py --check` exits 0 (or PLAN table and `EXPECTED_FIGURES` are updated together), the strategy and extractor tests pass, `docs/modules/news-collector.md` and the architecture artifacts describe the fetch policy (AI behavior #11, never renamed). → step 3.
      **Results (T-056c, PR #60):** the strategies keep every class with `match_reason` and, for `ddg`,
      the `query`; `api` feed rows (`stocktwits`, `yahoo_finance`) carry `match_reason = 'feed'`; the
      orchestrator's merge keeps the higher class of a pair found twice (and any `query`); discovery
      counters report `rejected=` (part of `inserted=`, never fetched). `extractor.db.record_fetch`
      (save, status, re-evaluation of a page that was read) is the one post-fetch step used by the
      crawler CLI and both API endpoints; `POST /extract/{id}` answers 409 for a `reject` row.
      `scripts/score_ticker_rules.py --check` exits 0 ("figures match PLAN"; no matcher change since T-056a). Test counts live in the PR description.
- [x] **T-057** *(done 2026-10-15: PR #61 tooling, real run on `urls.db` after the hand check passed)* Re-tag `urls.db`: dry run on a copy (before/after counts per
      ticker and source; count URLs also tagged to a correct ticker), then the
      real run with a restorable backup and a reversible representation
      (quarantine over delete) that `portfolio-nlp`'s pending queries
      (`fetch_status = 'ok'`, non-empty `body_text`) will actually exclude.
      Proposed shape, to agree with that repo first: set the `articles` row of
      every `review`/`reject` pair to `fetch_status = 'quarantined'` (their query
      already filters on `'ok'`, so no change there is needed to stop reading it),
      keeping the previous status in a new `articles.prev_fetch_status` column (the class
      and reason stay on `discovered_urls` as `match_class`/`match_reason`, joined on
      `id`) so clearing the flag restores the row exactly; the `accept` rows are
      untouched, `discovered_urls.status` of already-fetched rows is **left as it is**
      (`fetched`/`failed`; the class is read from `match_class`, and `/discovered`
      now exposes it), and `reject` rows that were never fetched have no `articles` row (their
      `discovered_urls.status` is already `skipped`). The run also writes the list of
      removed `(url, ticker)` / article ids and the `review` quarantine list
      (class and triggering evidence), which T-058 hands over. Before the real
      run, the fresh hand-labeled check must pass: **60 newly `accept` and 40
      `reject` pairs**, drawn after the rule is frozen; pass = at most 5 of the 60
      `accept` wrong and at most 1 of the 40 `reject` correct, otherwise back to T-055
      (FR-014, `PLAN.md`). → step 4.
      **Progress (T-057):** `articles.prev_fetch_status` added (schema version 3, confirmed 2026-10-15,
      recorded in `portfolio-nlp` PR #102 T-132/T-128); `data_mining/quarantine.py`; `record_fetch` quarantines/
      releases after every fetch (a fetched `review` pair would otherwise read `ok`) and a rediscovery promotion
      releases the article too; `scripts/retag_urls.py`
      (dry run by default; `--apply` needs `--backup` and `--hand-check SHEET`, which it scores with the verdict, validates as the 60/40 fresh sheet drawn under the current rule and records, and aborts before writing if the corpus moved since the plan). Dry run and apply on a copy of
      the 481,332-row corpus (2026-10-15): accept 104,614 / review 51,295 / reject 325,423 (the PLAN figures),
      376,718 articles quarantined, no pending rows (every row already has an article), restoring the flag
      reproduces the backup exactly, a second run changes nothing, integrity and foreign-key checks clean;
      26,044 `reject` and 9,343 `review` URLs are also `accept` for another ticker. By previous status: `review` 49,645 `ok` / 1,153 `thin_content` / 497 `failed` (only the `ok` ones are decidable by T-132), `reject` 316,243 / 8,197 / 983. `--apply` verifies the result against the plan and exits 1 (no lists) on a mismatch. **Real run (2026-10-15):** hand check PASS (60 `accept`: 1 wrong, max 5; 40 `reject`: 1 correct, max 1; 1 `unclear`), sheets
      committed (`data/ticker_retag_check_*.csv`). `--apply` on `/workspaces/thesis/data/urls.db`, backup `urls.pre-retag.db`: `verified: true`,
      481,332 rows re-tagged (accept 104,614 / review 51,295 / reject 325,423), 376,718 articles quarantined, lists in
      `data/retag/` (git-ignored: `removed_articles.csv` 325,423, `review_quarantine.csv` 51,295, `retag_summary.json`). Label vocabulary is `yes`/`no`/`unclear`.
      The one `reject` row labeled `yes` (id 302365, KLAC `kla-corporation-…-earnings-call-presentation`) is a rule gap, not a
      one-off, and the check passed at its limit. Three causes: a name that is short or a stopword once the suffix is removed
      (`kla`, `southern`, `news`; no `review` fallback), a possessive written as a trailing `s` in the slug (`bank-of-americas`), and an `&`
      the URL drops. `scripts/known_false_rejects.py` (lower bound: the name as whole words of the URL path) finds **at least 433 pairs
      on 234 URLs** (News Corp 400 as `NWS`+`NWSA`, `KLAC` 15, `SO` 11, `BAC` 4, `DG`/`IFF`/`TTD` 1), committed in
      `data/retag_known_false_rejects.csv`, quarantined not deleted; the hand check estimates 2.5% of the `reject` class (95% interval
      0.4%-12.9%). Accepted as a recall loss in `PLAN.md`, reported to `portfolio-nlp` as "do not purge". Real `urls.db`:
      `PRAGMA integrity_check` ok, `foreign_key_check` empty.
- [x] **T-058** *(done 2026-10-15: hand-off comment on `portfolio-nlp` PR #102 with counts, lists and the selection; follow-up comment 2026-10-16 correcting the known false rejects from 28 to at least 433 pairs, `T-062`)* Hand off: tell `portfolio-nlp` the corrected corpus is ready, with the
      list of removed article ids (it needs them to clean its existing results
      and `sector_summary` — its T-131) and the quarantined `review` pairs
      (its `T-132` decides them; nothing is excluded on doubt there). **One agreed
      selection, cited by both repos:** `portfolio-nlp` T-132 reads
      `SELECT a.id, d.ticker, d.company, d.match_reason FROM articles a JOIN
      discovered_urls d ON d.id = a.id WHERE a.fetch_status = 'quarantined' AND
      d.match_class = 'review'`, while its default pipeline query keeps
      `fetch_status = 'ok'` (so `accept` rows only) and never sees `reject` ones
      (unblocks its Work item 15 development and its re-measurement on the
      cleaned data); close `portfolio-nlp` `T-128`. → step 5.
- [x] **T-060** Content-quality gate on `articles`, so a login wall, bot
      challenge or empty body can never reach `portfolio-nlp`. Today the only
      protection is the extractor's length check (`thin_content`) plus
      `fetch_status`; checked 2026-10-15 every wall seen in the labeling sheet
      is already `failed`/`thin_content` and `nlp.db` holds only `ok` rows, but a
      wall long enough to pass the length check would be marked `ok`. Add a
      detector for known wall/challenge/paywall phrases at extraction (e.g.
      "Security Verification", "Just a moment", "unusual traffic", sign-in and
      subscribe prompts) that leaves such rows non-`ok`, a rerunnable read-only
      audit of `ok` rows per domain (phrases, word-count floor, duplicate
      bodies), and tests on real excerpts including the false positives found
      (CNBC articles that contain "just a moment" or "CAPTCHA" in normal prose).
      Separately report the duplicate-processing finding (302,512 distinct
      bodies among 459,112 `ok` rows) so `T-055`/`T-056c` can decide to attribute
      each URL once. → quality of what step 4 hands over.
      **Done:** `extractor.parse.wall_kind` looks for whole-word wall phrases in the first 300 characters of the
      body only. A bot challenge (`Security Verification`, `Just a moment`, ...) is stored `failed` (transient:
      `--retry-failed` retries it), a login or subscribe prompt `paywalled` (gated: not retried); neither is `ok`.
      `scripts/audit_article_quality.py --by-status` ran the final detector over all 481,332 real bodies
      (2026-10-16): `failed` 578 hits of 9,634, `quarantined` 198 of 376,718, `ok` 0 of 93,224, `thin_content` 0 of
      1,756. It also prints the hits per `source_domain`: all 776 are `ft.com` (`Security Verification`, 38 words
      each). Those pages were already `failed` (HTTP 403) and at fetch time are `thin_content` (under 80 words), so
      they never reach `wall_kind` in the pipeline: on the real corpus the gate changed no stored row. It is
      preventive, for a wall long enough to pass the length check.
      Evidence: the challenge phrases are validated on those 776 pages; the four login/subscribe phrases are
      unvalidated on real data (the corpus has no `paywalled` wall example), only by synthetic tests.
      The scan also found three head false positives that shaped the rules: "just a *momentary* blip",
      "investigating unusual traffic that might be from hackers" and a quarantined CNBC article (id 78699) opening
      "For just a moment, mentally fast-forward...". So phrases are whole-word, `unusual traffic` needs
      `from your`, and `just a moment` / `access denied` count only at the very start of the body, as whole words
      ("Just a momentary..." is not a wall). The real excerpts are fixtures in `test_parse_body.py`.
      The audit (read-only, per domain: walls, under the word floor, hashed duplicate bodies; `--list` prints wall
      ids; exit 1 on a wall or an under-floor row) also covers rows quarantined from `ok` (`held`, `held walls`),
      since a release restores `ok`. Real `urls.db`: 93,224 `ok` + 365,888 held rows, 0 walls, 0 under the floor.
      The duplicate-body finding stays reported, not acted on (it is `T-055`/`T-056c` territory). Tests: 777.
- [x] **T-059** Close-out: `SPEC.md` §13 item 10 annotated and FR-014 moved from planned to done,
      `docs/modules/news-collector.md` updated, both architecture artifacts
      reconciled (never renamed), then move this work item to `CHANGELOG.md`
      (constitution AI behavior #12). Also: decide how long `urls.pre-retag.db` (next to `urls.db` in
      `/workspaces/thesis/data/`) is kept, and who holds the git-ignored `data/retag/` lists
      (regenerate them with `retag_urls.py --out` on a copy of the backup if lost; the known false rejects are committed).
      FR-014 is "implemented with the known recall gap in `T-062`", not "done". `T-062` is its own Work item 12 (PLAN), so item 10 closes
      here and the `CHANGELOG.md` entry names `T-062` as the open successor. → acceptance criteria.
      **Done 2026-10-16** (PR #65): `SPEC.md` FR-014 reads "implemented with the known recall gap in `T-062`" and §13 item 10
      records the outcome; `docs/modules/news-collector.md` and `news-crawler.md` were checked and are already current (the class
      columns, the quarantine and the T-060 gate were documented by PRs #59–#61 and #64). Both architecture artifacts are
      reconciled, content only, titles unchanged: Portfolio Data Mining (v26) and Portfolio Thesis (v35). Item 10 moved here
      with `T-062` (Work item 12) named as its open successor. PR #64 had been merged into the already-merged `T-057` branch,
      so `T-060` reached `master` only with PR #65. **Maintainer decisions, proposed here and for the maintainer to confirm:**
      keep `urls.pre-retag.db` until `T-062`'s targeted re-class pass is checked (it is the only way back to the pre-re-tag
      `fetch_status`), then delete it; the maintainer holds the git-ignored `data/retag/` lists (`retag_urls.py --out` on a copy
      of the backup regenerates them). **Open criterion:** `portfolio-nlp`'s confirmation that wrong-company rows no
      longer reach its pending queries has not arrived (only the `T-058` hand-off on its #102 is posted); it is theirs to
      give (its `T-132`) and does not block closing this item.

## Work item 11 — Fetch the Tiingo prices for the 33 leaver lines into a private local file (T-061)

Requested by `portfolio-financial-analysis` for its `T-100` (full-universe run; raised by `T-153`
parts 2–3): "Fetch the Tiingo prices for the 33 leaver lines into a private local file."

- [x] **T-061** Build `src/data_mining/leaver_prices.py` and the `cli/pricing_cli.py
      leaver-prices` subcommand (like `universe-gics`); port `portfolio-financial-analysis`'s
      rule-based NYSE calendar into `src/data_mining/nyse_calendar.py`; add the `TIINGO_API_KEY`
      note to `.env.example`; amend the constitution (Technological stock #8, 1.8.0) and `SPEC.md`
      FR-015/§2.1/§11/§12; run the fetch into `/workspaces/thesis/data/private/tiingo/`; delete
      `/tmp/tiingo_feasibility/` once the file passes §4. → `PLAN.md` Work item 11. **Done
      2026-10-16** (PR #62, merged): 33 lines, **44,989** rows, **20** trimmed,
      **37** missing sessions, **442** dividend rows, **1** split row; the file's sha256 is
      `d6ef4052aac37fa1c1b584e0653c0e3abc3ff690bd12e64ed40a2ec8a7589179` (unchanged across the
      fix-round re-run); the calendar reproduces report §2's expected count for all 35 lines;
      TTD/PVH/KMX match the gateway, the AAPL dividend formula is `divCash / split_cum`, WBD
      matches production within 1 cent; the gateway serves **0** candles for all 33, production
      stores DISCK **0** / WBD **1,189**; Tiingo requests **42** (feasibility, 2026-10-14) + **37**
      (run 1, 2026-10-15) + **37** (run 2, 2026-10-16) — more than an hour apart, so no rolling hour
      over 37; `/tmp/tiingo_feasibility/` deleted; the four production DB sha1s unchanged; 734 tests.
      PR #62 review follow-ups (second commit `21751b1`): per-row calendar check, one-vendor-per-line
      stop, DISCK's CIK+ticker production count, persisted `request_log.json`, made-up numbers in the
      committed tests, `membership_span` per stint + ANSS `declared_exception` + K's spin-off note in
      the manifest, and a trim-reason-class comparison.

