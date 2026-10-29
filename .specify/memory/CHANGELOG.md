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

### Part 2 — the 32 pre-2022 leaver lines (T-061b) — closed 2026-10-29 (PR #102)

Part 1 (`T-061`, the 33-line `leaver-prices` fetch) is closed in `CHANGELOG.md`; this is its next part:
the second private file for reviewer A's GUARD calibration (`FR-016`, brief
`data_mining_tiingo_calibration32_task.md`).

- [x] **T-061b-1** Add `src/data_mining/leaver_calibration.py`: the 32-line table (per-line start/end,
  verified CIK, Tiingo symbol, accepted names), the two phases with the private `.raw/` cache, EDGAR CIK
  verification (submissions JSON + in-span cover page), the gateway counts (FOX/FOXA recorded, the other
  30 must be 0), the trims/split formula reused from `leaver_prices`, `split_cum`, `last_equals_exit`,
  the >25% jump scan, and the CSV + manifest writer. → `PLAN.md` Work item 11, part 2.
- [x] **T-061b-2** Add `cli/pricing_cli.py leaver-prices-calibration --phase a|b` and route it without
  `FINNHUB_API_KEY`. → part 2, step 1.
- [x] **T-061b-3** Add hermetic tests in `tests/data_mining/test_leaver_calibration.py` (synthetic rows
  only; no real price). → part 2 acceptance.
- [x] **T-061b-4** Amend constitution #8 (MINOR 2.1.0), `SPEC.md` FR-016/§2.1/§11 and
  `docs/modules/data-mining.md`. → part 2 approach.
- [x] **T-061b-5** Run phase a (49 requests), wait for the rolling hour, run phase b; record the counts,
  dates and sha256 in the manifest; confirm the four production DB sha1s are unchanged. → part 2
  acceptance. Done 2026-10-29: 29 of 32 lines, 10,174 rows, 69 requests; the 3 reused-ticker lines
  (APC/NFX/STI) reported not delivered. The file and manifest are private.
- [x] **T-061b-6** Open the PR with counts, dates and sha256s only (no price). → part 2.
  Opened 2026-10-29: #102.

**Closed 2026-10-29 (PR #102; reviewer B verified the manifest against the universe spans and
closed L-42).** 29 of the 32 lines, **10,174** rows of **10,200** sessions; sha256
`ae6fcc699062f9c960af0eff74ad144c5d76dcaa75b0f31cdb802e6d0848cd9e`. Every date an NYSE session,
no duplicates, every row inside its line's span, first/last/count equal to the manifest; the PR holds
no data. 10 lines end before their index exit (dated: deal close first, or a carried close trimmed);
13 jumps > 25% labelled (3 corporate events, 10 market-wide with SPY's same-day return); **123**
dividend rows, **1** split-factor row (DWDP 2019-04-02, the Dow Inc. distribution, removed from the
adjusted series); LLL/TSS kept on the documented identity override; APC/NFX/STI not delivered
(356 sessions; the 5 probes found no same-entity series); 69 requests (64 fetch + 5 probes), largest
rolling hour 49; 1,354 tests; the four production DB sha1s unchanged. **L-42 final:** of the 58 lines
absent from the database, 55 are in the calibration files (26 gateway + 29 Tiingo); 3 of 649 stay out
of the calibration, cited with C12.

## Work item 12 — Matcher: stop rejecting pairs whose URL spells the company out (code + data, origin: Work item 10 real run)

- [x] **T-062** *(Work item 12; follow-up found by the T-057 real run; changes the matcher; scheduled by the maintainer 2026-10-16)*
      Fix the full-name false rejects: at least 433 `reject` pairs on 234 URLs whose URL spells the company
      out (`data/retag_known_false_rejects.csv`, `PLAN.md` Work item 10 "Accepted recall loss"). Three causes in
      `matching._name_reason`: a name that is short or a stopword once the suffix is removed (`kla`, `southern`, `news`),
      a possessive written as a trailing `s` (`bank-of-americas`), and an `&` the URL drops. Make a full-name hit at
      least `review` (never `reject`), normalize the possessive and `&`, then re-class the quarantined rows with a
      targeted pass (the re-tag only touches `match_reason IS NULL` rows), audited: dry run, verified backup, audit list,
      `--revert`, a fingerprint guard, post-conditions (rows per class, the sync prediction, the readable `ok` articles) and
      a read-only attach as `portfolio-nlp` does it, all in `scripts/reclass_rejects.py`, rehearsed on a same-mount copy with
      `scripts/rehearse_reclass.py` (removed 2026-10-18, see `4bfa812`); `release_articles` runs for any that become `accept`. Acceptance: the 433 rows are no longer `reject` (update `known_false_rejects.py --check` and
      `tests/scripts/test_known_false_rejects.py`), `score_ticker_rules.py --check` is re-measured and `PLAN.md`'s figures
      re-frozen, a fresh hand check (new 60/40 sheet, drawn from the post-fix classes) passes the T-057 criterion, and `portfolio-nlp` is told what changes for it:
      the `reject` count falls and the `review` count rises by the promoted pairs, while the rows it can read do not change (a
      promoted pair is `review`, so its article stays quarantined) unless the maintainer decides to release a subset to `accept`. Constitution AI behavior #10 does not apply
      (no schema change). Tooling for that sheet: add `data/ticker_retag_check_sample.csv` to `EARLIER_SAMPLES` and to the
      `--final` exclusions in `ticker_validation_sample.py`, and draw with `--final --out <new path>` (the default output is the T-057 sheet and must not be overwritten).
      **Done 2026-10-17** (PRs #66, #67, #69, #70): the matcher reads a full name the URL spells loosely (suffix kept, `&`, possessive `s`,
      camel-case brand, a trailing `Corp` spelled out) as `review`/`loose_name`; the audited real run promoted **448** pairs
      (`reject` 325,423 → 324,975, `review` 51,295 → 51,743, `accept` and the readable `ok` articles unchanged, 93,224), with backup
      `url_cat_backup.db`, audit list and run summary kept; `portfolio-nlp` hand-off prepared, delivery pending with the maintainer. Figures and timings: `PLAN.md` Work item 12.


## Work item 13 — The News Corp pairs wait for the NER check (decision)

- [x] **T-063** *(superseded 2026-10-17 by the maintainer's decision, option B; no code)* The proposal was to release
      the News Corp pairs (`NWS`, `NWSA`; 413 pairs on 210 URLs, 402 `review` and 11 `reject`) to `accept` with an alias,
      a `--from-class` pass and holds for walls, contested URLs and roundups. On the evidence (10 of 10 hand sample,
      lower bound 72%; 42 URLs naming another company; 18 of 20 on the developer's read) the maintainer chose to hold
      them all for `portfolio-nlp`'s future NER check, under the rule that doubtful tags are left to it. Nothing is
      built or released; the measurements are in `PLAN.md` Work item 13. The 11 legal-name pairs (`news-corporation`)
      were then read as `review` by the legal-name form of `T-062` (#70), so all 413 wait for the NER check.

## Work item 14 — Collector and gateway: a discovery batch above SQLite's variable limit, the database path, the `.env` rule (T-064–T-067, T-075–T-077)

Found 2026-10-18 by the T-062 step 6 check and the database validation after the gateway restart. Evidence, line numbers and the
decisions taken are in `PLAN.md` Work item 14. **Order:** `T-064`, then `T-065`, then `T-066a` (which reuses `resolve_db_path()` and its empty-value rule from `T-065`); `T-067` is independent;
`T-066b` is done in PR #83 (design in PR #82); choice (11) on `news_collector/config.py` was answered 2026-10-18 (remove the import-time read) and is done in `T-077`, with the end date.

- [x] **T-064** *(bug from `T-056c`)* Chunk the `(url, ticker) IN (...)` lookups in `enqueue_batch` and `_count_new_rejected`
      (`src/news_collector/storage/queue.py`, about 5,000 pairs per query) so a batch above about 16,000 pairs does not exceed
      SQLite's variable limit (32,766 on 3.40.1). Make a failed batch loud: error log, non-empty `errors`, and a non-zero exit of
      `news_collector/main.py`. First log the batch size of the failing `KLAC`/`cnbc` call to confirm the cause.
      Acceptance: (1) a test with 20,000 mixed pairs that lowers the limit (`queue._conn._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, ...)`,
      or monkeypatches the chunk size) and **fails on the unfixed code** (shown in the PR); it inserts all rows and returns the right
      `last_inserted_rejected` and `last_promotion`; (2) a test with more than 16,000 urls and fewer than 100 rejects: every `u.id` set, promotion applied;
      (3) chunked equals unchunked on a small batch; (4) a CLI test of the non-zero exit; (5) **Redefined by the maintainer 2026-10-18 (option A): read, do not re-run** (a second `KLAC`/`cnbc` discovery would add stored rejects that `T-073` removes again). Closed 2026-10-18, read-only on `/workspaces/thesis/data/urls.db`:
      - *Accepted narrowing:* the original step 5 also proved the chunked path on the real database (a batch above 16,000 pairs going through). That proof is dropped; the logic is covered by the
        tests with a lowered limit, and the first real batch above 16,000 pairs after `T-071` is where to confirm it.
      - *Evidence for the data half of `T-062` step 6 (the deployed matcher classes a full-name URL `review`/`loose_name`):* the matcher prints it for `kla-corporation-q4`. Of the 19 `KLAC`
        `review`/`loose_name` rows, 16 date from 2026-08-11 (before the `T-062` run). **One row is clean evidence: 495526** (`cnbc.com` through search, `source = 'ddg'`, `…kla-corporation-is-a-great-company`,
        new on 2026-10-18 13:20, `pending`; absent from the 13:14 backup `urls.pre-t064.db`, which has no id between 495521 and 495528, and written by the 13:19 cnbc search `site:cnbc.com KLAC 2026`). Rows 495519 and 495520 (`seekingalpha.com`, `ddg`) read `review`/`loose_name` too, but the maintainer corrected them by hand (see the strays below), so they are not matcher evidence and are not counted.
      - *Sitemap path:* no `review` row. The 578,117 `KLAC` `cnbc.com` sitemap rows are all `reject`/`none`/`skipped`; classing them again with `KLA Corporation` as the name leaves all 578,117 as `reject`
        (same matcher, URL only, empty title: a determinism check, not a proof that none was wrongly rejected). No `KLAC` URL spelling `kla-corporation` or `kla-corp-` is `reject`; `kla-tencor` URLs
        (the former name) are `reject`, as expected.
      - *Strays:* in the 13:14 backup `urls.pre-t064.db`, 495519/495520 are `company = 'KLAC'`, `reject`, `skipped`; on the real file they are `company = 'KLA Corporation'`, `review`/`loose_name`, `pending`.
        Their `query` is `site:seekingalpha.com "KLAC" analysis` (the 02:39 run); the 13:19 run searched only `site:cnbc.com …`, so it did not rediscover them and the change was made outside it.
        The promotion code does not write `company`; the maintainer confirmed on 2026-10-18 that the correction was a manual write after the 13:14 backup. A manual write is not matcher output, hence the one-row count above.
      No schema change (constitution #10 N/A).
- [x] **T-065** *(configuration; done 2026-10-18, PR #79; also `news_collector/main.py` `stats`/`export` used `args.db` directly, now resolved; tests in `tests/news_collector/test_db_path_default.py`)* The collector API opens the wrong database by default. Change: (a) `default=None` (typed `str | None`,
      description "default: $DATABASE_URL") for the five `db_path` defaults in `api/routers/discovery.py` (3) and `api/schemas.py` (2);
      (b) `data_mining.resolve_db_path(value=None)` returns the value, else `$DATABASE_URL`, else the default, and treats an empty string as
      unset (`.env.example` ships `DATABASE_URL=`); (c) the seven sites that build a `URLQueue` call it (`routers/discovery.py` 4 sites,
      `routers/_common.py`, `api/deps.py` `get_queue`, `news_collector/main.py`); (d) `URLQueue.__init__` raises `ValueError` on `None` or
      an empty path; (e) the literal fallback in `apps/news_crawler_api.py` and `cli/news_crawler_cli.py` (`DEFAULT_DB`) uses it too.
      Acceptance: each handler follows `$DATABASE_URL`; a request without `db_path` creates no file named `None` or `data/urls.db`;
      `URLQueue(None)` and `URLQueue("")` raise; a plain `/collector/discovery/stats` reports the real counts; the `db_path` workaround
      is removed from `docs/modules/news-collector.md`.
- [x] **T-066a** *(done 2026-10-18, PR #80; `data_mining/startup_check.py`, wired in the lifespans of the gateway, crawler and collector apps; tests in `tests/data_mining/test_startup_check.py`; after `T-065`, whose `resolve_db_path()` decides the empty value; configuration)* Start-up check and visibility, in the apps that open
      `urls.db` (`apps/gateway.py`, `news_collector_api.py`, `news_crawler_api.py`; not `pricing_api.py` or `sec_edgar_api.py`, which never touch it).
      (1) Log the effective `DATABASE_URL`: its source (`.env`, inherited, default), the **resolved absolute path**, whether the file exists, and its
      size and `discovered_urls` row count. (2) Warn when an inherited value differs from `.env`, naming the variable and both values; read the
      `.env` value with `dotenv_values()` (`load_dotenv(override=False)` leaves no trace of the value that lost). (3) Refuse to start when the
      database is unusable, where **empty means the `discovered_urls` table is missing or has no rows, not a zero-byte file**: the literal default
      `data/urls.db` is 49,152 bytes, a schema with no rows, and is exactly the file that served `total: 0`. The gateway and `news_crawler_api` refuse on a
      missing file, a missing table or no rows; `news_collector_api` creates the database on first use, so it refuses only on a missing parent
      directory or an unreadable file and logs a notice for a new one. `--allow-new-db` or `ALLOW_NEW_DB=1` lets the gateway and the crawler
      start on an empty database. Precedence (maintainer, 2026-10-18, option B): the inherited environment stays above `.env`. Acceptance: tests of the
      log line, the warning, the refusal for a missing file, a schema-only file (the case that happened) and a populated file, and the flag; a plain
      gateway start on this machine serves the real counts or stops with the message; `.env.example` lists `ALLOW_NEW_DB` (unset by default).
- [x] **T-066b** *(code done 2026-10-18, PR #83: `data_mining/env.py`, ten entry-point files (the 9 `apps/`+`cli/` files and `news_collector/main.py`), `tests/data_mining/test_env.py`, `test_env_entrypoints.py`, `tests/conftest.py`; choice (11) answered and done in `T-077`; design agreed with the maintainer 2026-10-18; rule 7 amended in the same docs PR, code in the next PR)* `.env` wins over the
      inherited environment (this machine's `/etc/environment` sets `DATABASE_URL="../data/urls.db"`); the system value is the fallback.
      Design, in the order the maintainer decided it:
      (1) **Goal:** `.env` first; if the file or the variable is missing, the system variable. This supersedes the 2026-10-18 option B
      (inherited above `.env`).
      (2) **Scope:** every variable in `.env`, not only `DATABASE_URL`.
      (3) **Mechanism (rule 7 option a):** `data_mining/env.py` with `load_env(path=None)`: finds the repo-root `.env` (not the current directory),
      and sets each non-empty value in it over the inherited one.
      (4) **Tests:** `load_env(path)` takes a tmp file; an autouse fixture in `tests/conftest.py` points the root lookup at a missing file so no test reads the real `.env`.
      (5) **Empty value:** a blank `.env` value counts as absent (the system value stays), as an empty `DATABASE_URL` means unset in `T-065`.
      (6) **Notice:** one log line per overridden variable. The values (inherited and used) are printed only for the allow-list of (8), which the maintainer confirmed
      2026-10-18; every other variable is logged by name only. The `T-066a` warning that an inherited value hides `.env` is reworded to this (the check's source label stays `.env`/`inherited`/`default`).
      (7) **Enforcement:** an AST test over `apps/` and `cli/` (5 apps, 4 CLIs, the test globs both) **and `src/news_collector/main.py`** (run as `python -m news_collector.main`, listed explicitly) fails when a file has no top-level `load_env()` before its first
      project import that reads the environment (`news_collector…`, `data_mining…`, `pricing…`, `sec_edgar…`; an import side effect does not count). `scripts/` out of scope (they take `--db`).
      Also: replace the `load_dotenv()` calls with `load_env()` in the 4 sub-apps (`news_collector_api`, `news_crawler_api`, `pricing_api`, `sec_edgar_api`), the 3 CLIs
      (`news_crawler_cli`, `sec_edgar_cli`, `pricing_cli`) and `news_collector/main.py`; **add** the call to the two entry points that have none today, `apps/gateway.py` and
      `cli/news_collector_cli.py`; `news_collector/config.py` is left to the open choice in (11). Update `.env.example`, the module docs and the `T-066a` tests that assert the inherited value wins.
      **Details fixed after the manager's review (PR #82):**
      (8) **Secrets:** an **allow-list**, not a name pattern. The log line prints both values only for `DATABASE_URL`, `UNIVERSE_DB_PATH`, `DISCOVERY_START_DATE`,
      `DISCOVERY_END_DATE`, `ALLOW_NEW_DB`; for every other variable, including any added later, only the name (a proxy URL or another engine's `DATABASE_URL` can hold credentials).
      (9) **Root:** walk up from `data_mining/env.py` to the first directory with `pyproject.toml`; none found means no file is read, the system values stand and one notice is logged.
      (10) **Order in an entry point:** the `sys.path` bootstrap of `src/` and the import of `load_env` itself are allowed before the call; every other project import that reads the
      environment must come after it. The AST test skips `apps/__init__.py` and covers `apps/gateway.py` (it reads the environment itself in its lifespan) and every other `apps/*.py` and `cli/*.py`.
      (11) **`news_collector/config.py`** *(answered by the maintainer 2026-10-18: option 1, remove; the change is made in `T-077`)*: its import-time `load_dotenv(override=False)` is **removed**: library code reads `os.environ` only and the entry points load `.env`
      (`news_collector/main.py` is itself an entry point and calls `load_env()`). A plain `import news_collector.config` in a test or a notebook sees the system environment only. PR #83 did not touch `config.py`'s own read; `T-077` removes it.
      **Known consequences (accepted with decision 2, no escape hatch):** a one-off value on the command line loses to `.env`: the documented
      `DATABASE_URL=/workspaces/thesis/data/urls.db uv run apps/gateway.py`, `DISCOVERY_END_DATE=...`, `ALLOW_NEW_DB=1` stop working when `.env` sets the same variable. A blank `.env` line cannot switch off an inherited variable (decision 5): a system `HTTP_PROXY` or `HTTPS_PROXY` stays on; unset it in the system or the shell. A one-off run is made by editing `.env`
      or with a flag applied after `load_env()` (`--db`, `--allow-new-db`, a request's `db_path`). The module docs and `.env.example` say so in the code PR.
      Acceptance: tests of precedence (`.env` over inherited), fallback (missing file, absent variable, blank value), root lookup from another working directory,
      the notice (values printed only for the allow-list; a variable not on it is logged by name, with a value that must not appear in the log), the AST test (fails on a file without the call), a flag applied after `load_env()` still wins (`--allow-new-db` takes effect with `ALLOW_NEW_DB` absent from `.env`, and a `.env` value does not undo the flag), and the real gateway start on this machine with the wrong inherited value
      using the `.env` database.
- [x] **T-067** *(collector API; done 2026-10-18, PR #81: `news_collector/companies.py` (shared by `resolve_companies` and the CLI's `--tickers`), tests in `tests/news_collector/test_resolve_companies.py` and `test_main_exit_code.py`; the leaver lookup was confirmed by the maintainer 2026-10-18)* `resolve_companies` (`api/routers/_common.py`) does `name = names[i].strip() if i < len(names) else ticker`, so a
      request without `company_names` stores the ticker as the company and the matcher rejects a `kla-corporation` URL. Change: reject a
      `company_names` list whose length differs from `tickers` (422 that says it is positional); when names are omitted, look each ticker up by
      **exact symbol** in `fetch_sp500_companies` (one Wikipedia request, 502 on failure as today). Only for a ticker the current list
      does not contain, look it up by exact `Symbol` in `data_mining.universe_history.query_as_of(start_date)` (never `resolve_as_of`, whose
      partial name match could tag the wrong company), so a missing or un-backfilled `data/universe.db` (it raises `ValueError`) cannot break
      requests for current tickers. `start_date` is the request's, else `config.default_start_date()`. A ticker found in neither, or a
      `start_date` outside the store's coverage, gets a clear 422. Known limit, accepted and documented: a ticker added after `start_date` and removed before
      today is in neither, so it is refused (the caller passes `company_names` for it). The leaver lookup was confirmed by the maintainer 2026-10-18 (the 422 for a ticker in neither source stays; no fallback to the ticker, no fuzzy match, no mandatory `company_names`).
      Acceptance (hermetic): omitted, mismatched and explicit cases; a leaver ticker; an unknown ticker that is a substring of a company name; an
      out-of-coverage date; a current ticker still resolving when the store is missing. The two stray rows (ids 495519, 495520): on 2026-10-18 the real file already reads `company = 'KLA Corporation'` for both, corrected by hand by the maintainer
      after the 13:14 backup (the 13:14 backup `urls.pre-t064.db` still has them as strays). Acceptance check: `SELECT id, url, ticker, company FROM discovered_urls WHERE company = ticker AND id >= 495519` returns 0 (already true on 2026-10-18, so it guards against a regression, it no longer shows a fix). Re-run read-only after the code change: 0 rows.
      The `id >= 495519` bound is why 939 older rows with `company = ticker` are not counted (measured 2026-10-18: `IBM` 892, `MSCI` 44, `AAPL` 3, all from August 2026). `IBM` and `MSCI` may be correct (the company is known by its ticker);
      the 3 `AAPL` rows (ids 484806, 484810, 484811, all `accept`) were **not investigated and out of scope** in this PR *(superseded 2026-10-18: investigated and written, see "The 939 rows" below)*; maintainer decision 2026-10-18: widening the scope waits until the bug tasks of Work item 14 (`T-065`, `T-066a`, `T-067`, `T-076`) are closed or dropped, then the 939 rows are looked at (no new task now; `PLAN.md` Work item 14 carries the reminder).
      **The 939 rows, looked at read-only 2026-10-18:** `IBM` (892) is correct, the company's name is `IBM`; no change. `MSCI` (44, other rows `MSCI Inc.`) and `AAPL` (3, other rows `Apple Inc.`) are label inconsistencies: all 47 are `accept`. Classed again with the full name, the 44 `MSCI` rows and `AAPL` 484806 stay `accept`/`name`; `AAPL` 484810 and 484811 (CNBC market-movers lists accepted only because the name text was `AAPL`) become `review`/`weak_ticker`. Maintainer choice 2026-10-18: option B (normalize the labels), and the two class changes confirmed. **Written 2026-10-18** (gateway and crawler stopped; backup `/workspaces/thesis/data/urls.pre-wi14-labels.db`, integrity `ok`, 1,059,452 rows, counts equal to the source before the write; after the write a row-by-row `EXCEPT` comparison against the backup shows exactly 47 `discovered_urls` rows changed and 0 `articles` rows changed): one transaction, 44 `MSCI` rows to `MSCI Inc.`, 3 `AAPL` rows to `Apple Inc.`, rows 484810 and 484811 to `review`/`weak_ticker` (counts asserted, rolled back otherwise). After: integrity `ok`, `company = ticker` leaves only `IBM` (892, correct). No code change.
      **`articles` (manager review of PR #86; the proposal, written 2026-10-19):** `articles.id` is a foreign key to `discovered_urls.id`; 47 `articles` rows (44 `MSCI`, 3 `AAPL`) still carry `company = ticker`, and the articles of 484810 and 484811 are still `fetch_status = 'ok'`. Proposed *(superseded 2026-10-19: approved and written, see "`articles` step written" below)* (new write on the real database; the backup was still valid for `articles`): 47 `articles.company` updates by id, and `sync_quarantine` for ids 484810 and 484811 (reversible, `prev_fetch_status` kept, nothing deleted). The 5 `AAPL` rows labelled `Apple` read `Apple` in both tables; the matcher accepts both; accepted as they are.
      **`articles` step written 2026-10-19** (maintainer approval 2026-10-19; gateway and crawler stopped). Basis for "no service running": a process listing for `gateway`, `news_crawler_api`, `news_collector_api` and `uvicorn` returned nothing, and `urls.db` had last been modified at 2026-10-18 23:56 UTC (the `discovered_urls` write), with no writer since. Step 0: the `articles` `EXCEPT` against `urls.pre-wi14-labels.db` was 0 in both directions; baseline `fetch_status` counts: `ok` 93,226, `quarantined` 376,716, `failed` 9,634, `thin_content` 1,756 (total 481,332). Step 1: 47 `articles.company` updates, asserted 47, committed. Step 2: `sync_quarantine` returned `(1, 0)` for 484810 and for 484811. Checks after: (1) 0 rows with `articles.company != discovered_urls.company`; (2) counts `ok` 93,224, `quarantined` 376,718, the rest unchanged; (3) both rows `quarantined`, `prev_fetch_status = 'ok'`; (4) `EXCEPT` against the backup: 47 rows in each direction (the 47 ids); `integrity_check` `ok`; `company = ticker` in `articles` leaves only `IBM` (892). The write was run as a one-off script from the scratchpad (not committed); this PR records it.
      *The `articles` write, as run (a one-off script, not committed):* step 1, with `ids` = 326702–326745 plus 484806, 484810, 484811: `BEGIN IMMEDIATE; UPDATE articles SET company = (SELECT company FROM discovered_urls d WHERE d.id = articles.id) WHERE id IN (the 47 ids) AND company = ticker;` asserted 47 rows, else `ROLLBACK`; then `COMMIT`. Step 2: `sync_quarantine(db, 484810)` and `sync_quarantine(db, 484811)` from `data_mining.quarantine` (connection from `extractor.db.connect`), each asserted `(1, 0)` while the row was `ok`. The `AND company = ticker` guard is not in the planned statement below; it makes the 47 assertion fail on a second run instead of re-writing.
      *Procedure and checks for the `articles` step (as planned):* **Step 0:** confirm the gateway and crawler are stopped, and re-run the read-only `EXCEPT` of `articles` (`id, ticker, company, fetch_status, prev_fetch_status`) against `urls.pre-wi14-labels.db`, both directions; it must be 0 (it was 0 on 2026-10-19 with no service running; a non-zero result means a fresh verified backup before the write). Also record the baseline for check (2): `SELECT fetch_status, COUNT(*) FROM articles GROUP BY 1` and the total, in the PR or the run log. **Step 1** (its own transaction): `UPDATE articles SET company = (SELECT company FROM discovered_urls d WHERE d.id = articles.id) WHERE id IN (the 47 ids below)`; assert 47 rows, then commit; any other count rolls back. **Step 2** (a second transaction, because `quarantine_articles` and `release_articles` commit by themselves, so one transaction across both is not possible with `sync_quarantine`): `sync_quarantine(db, 484810)` and `sync_quarantine(db, 484811)`; the call returns `(quarantined, released)` and must return `(1, 0)` for each id (a `released` above 0 means that row's class is not what this plan assumes: stop). Read the two rows first: a row already `quarantined` with `prev_fetch_status = 'ok'` is expected to return `(0, 0)` (the call is idempotent) and is not an error, so a retry runs the call only for a row still `ok`. Never the form without an id, which quarantines every non-`accept` article in the database. Restart the gateway and crawler only after checks (1) to (4) pass. If step 2 fails, step 1 stays (a label fix is correct on its own) and the quarantine is retried. Read-only checks after: (1) `articles.company != discovered_urls.company` joined on `id` returns 0 rows; (2) the `fetch_status` counts are unchanged except two rows `ok` to `quarantined`; (3) those two rows have `prev_fetch_status = 'ok'`; (4) an `EXCEPT` against `urls.pre-wi14-labels.db` returns exactly the 47 `articles` rows (the 2 quarantined are among them). *Rollback:* restoring `urls.pre-wi14-labels.db` is the complete way back. Row by row: the labels go back with `UPDATE articles SET company = ticker WHERE id IN (the 47 ids)`; the quarantine goes back with `UPDATE articles SET fetch_status = prev_fetch_status, prev_fetch_status = NULL WHERE id IN (484810, 484811)` (`prev_fetch_status` is nullable and is `NULL` on a row that was never quarantined, so the restored row is as it was), **and** those two rows must also be back to `match_class = 'accept'`, `match_reason = 'name'` in `discovered_urls`, otherwise the next crawler `sync_quarantine` quarantines them again. `release_articles` does not restore them (class `review`).
      *The 47 ids changed on 2026-10-18 (from the `EXCEPT` against the backup):* the 44 `MSCI` ids are every id from 326702 to 326745; the 3 `AAPL` ids are 484806, 484810, 484811 (484810 and 484811 also changed class).
      *The `discovered_urls` write, as run (one transaction, counts asserted 44, 3, 2):* `UPDATE discovered_urls SET company='MSCI Inc.' WHERE company=ticker AND ticker='MSCI' AND match_class='accept'`; `UPDATE discovered_urls SET company='Apple Inc.' WHERE company=ticker AND ticker='AAPL' AND match_class='accept'`; `UPDATE discovered_urls SET match_class='review', match_reason='weak_ticker' WHERE id IN (484810, 484811) AND match_class='accept'`. Reverse: restore from the backup, or set `company = ticker` for the 47 ids and `match_class='accept'`, `match_reason='name'` for the two.
- [x] **T-075** *(bug, found 2026-10-18; code and tests in PR #75)* `_parse_date` in `src/news_collector/strategies/sitemap.py` returned `None` for every
      value (it cut the text to the length of the *format string*), so no sitemap entry was ever filtered by the run's date range and no sitemap row got a `pub_date`
      (0 of 1,040,939). Effect on the real database: the `KLAC` `cnbc.com` run took every URL back to 2006 for a 2022-01-01 to 2026-08-10 range (only 18.7% of its 578,117 rows are
      in range by the year in the URL), and about 65% of the `accept`/`review` sitemap rows are out of range. They were already fetched, none is `pending`: 62,379 of the 93,226 `ok` articles (66.9%) were published before 2022-01-01 (62,138 of them from sitemap rows), 67 after 2026-08-10. Fixed with `datetime.fromisoformat` (plus `YYYY-MM`); an entry with a
      `lastmod` that still cannot be read is kept and a warning counts them. Measured on the live cnbc sitemap (618,898 entries): `lastmod` in range for 121,233, the date in the URL in range for
      120,351, both for 120,179, so `lastmod` is a faithful filter (range membership and `discovered_urls.pub_date` are the sitemap's `lastmod`, last modified, not the publication date; see `docs/modules/news-collector.md`) and a cnbc match per ticker drops from about 619,000 entries to about 121,000. Acceptance: tests of the formats, of
      the leaf filter, of the index filter and of the warning (they fail on the old code). **Not done here:** the articles and rows already stored (out of range, empty `pub_date`) are
      the maintainer's decision; they are not deleted.
- [x] **T-076** *(bug, found 2026-10-18; done 2026-10-18, PR #78)* A failed sitemap fetch is silent and counts as done. `_crawl_sitemaps` catches the error, logs a WARNING and continues, so the strategy returns 0 urls
      and the orchestrator records the `(ticker, domain)` pair as completed (`orchestrator.py`, `mark_pair_completed` after `enqueue_batch`, unconditionally): `--resume` never retries it and the summary shows no error. This is why `ft.com` (`sitemap-index.xml` answers 403, the Wayback fallback also failed) and
      `nasdaq.com` (the `/articles/sitemap.xml` child answers 403, `/education/sitemap.xml` 404) have 504 completed pairs and no sitemap rows. Change: the strategy reports the roots and children that failed;
      the orchestrator adds them to `stats.errors` (so the summary lists them and, since `T-064`, the CLI exits non-zero) and does **not** record the pair as completed. No schema change (a flag column on `discovery_progress`
      would be one, constitution #10). **Started 2026-10-18 on the maintainer's go-ahead with the recommended option (the maintainer can still change it):** a source that is blocked for good (`ft`, `nasdaq` today) would then fail on every run and make `--resume` redo its search queries each time;
      the `ft` and `nasdaq` connectors drop their dead `sitemap_roots` (with a comment: 403 on 2026-10-18) so a loud failure is left for a real regression (cnbc). Work item 15's sitemap cache (`T-070`) moves the sitemap fetch: **this task owns the error reporting and the checkpoint rule, `T-070` owns where the fetch happens**, and whichever lands second carries the other's tests; the cache should also log an unparseable `lastmod` once per fetch, not once per ticker (`discover` runs per ticker, so the leaf warning repeats about 503 times per run). Acceptance: tests of a failed root,
      a failed child and a fully failed domain (error listed, pair not checkpointed, a later run retries it), and that a domain without `sitemap_roots` is unaffected.
      **Done (PR #78):** `SitemapRSSStrategy.failures` lists failed roots, children and parse errors; `summarize_failures` bounds the message (3 named, then "and N more");
      `_run_one` puts it in `PartialStats.error` and skips `mark_pair_completed` (a raised sitemap strategy too); the API route `_discover_one_ticker` lists it in `errors`;
      `ft`/`nasdaq` have `sitemap_roots=[]`; tests in `tests/news_collector/test_sitemap_failures.py`. **Out of scope, on purpose:** RSS and Wayback CDX failures stay warnings
      (Wayback is an optional source and the same call per ticker, so making it fatal would put `ft`, whose only other source is DDG, in a permanent failure). The unparseable-`lastmod`
      once-per-fetch log is left to `T-070`.

- [x] **T-077** *(code done 2026-10-18, PR #85; collector, CLI and API; asked by the maintainer 2026-10-18; also closes choice (11) of `T-066b`; design session with the maintainer 2026-10-18)* The discovery
      end date is an **input** whose default is **today (UTC)**, not a configured value. Today `default_end_date()` reads `DISCOVERY_END_DATE` and falls back to today, and `.env`
      on this machine has `DISCOVERY_END_DATE=2026-08-10`; since `T-066b` the `.env` wins, so every run that does not pass an end date silently stops at 2026-08-10.
      Both entry points already accept the date (`news_collector.main discover --end-date`, and `end_date` in the discovery request bodies, optional); what is missing is a
      default that is today and that nothing can pin. Decisions (maintainer, 2026-10-18, one question at a time):
      (1) **Default:** `default_end_date()` returns the UTC date (`datetime.now(UTC).date()`) and no longer reads the environment; `ENV_END_DATE` goes away. The date is taken when the
      command or the request runs, not when the module is imported (a long-lived API gets the day of each request). One clock function, `today_utc()` in `news_collector/config.py`, used by `default_end_date()` and `build_date_range()`'s callers, so the 23:59/00:01 UTC test patches one symbol. **Scope:** the collector only; `universe_history.py` and `universe_gics.py` keep the host-local `date.today()` and do not feed the end date.
      (2) **Cost accepted:** `--resume` matches the exact `(start_date, end_date)` of a prior run (`discovery_progress` is keyed by both), and "today" changes every day, so a run
      on a new day re-runs every `(ticker, domain)` pair from the start date. No duplicated rows: `UNIQUE(url, ticker)` rejects a repeat at insert time (a test runs the same pairs twice over the same range with `--no-resume`/`resume: false` and shows the second run inserts 0; a run over a different `end_date` with resume on re-runs the pairs). The cost is time and network; Work item 15 (`T-068`–`T-074`) cuts the sitemap part. To continue a batch the next day, pass the same `--end-date`.
      Not chosen: an incremental start (last completed end date) and a resume that ignores the end date; either would be its own task.
      (3) **API echoes the range:** `RunAllResponse` and `DiscoverResponse` gain `start_date` and `end_date` (ISO dates, the values actually used), so a caller copies them into the next
      request to resume the same range. Additive, no database change. The CLI summary prints the same two dates.
      (4) **Retired variable:** `data_mining/env.py` gets a retired-variable list with `DISCOVERY_END_DATE`. When the name is set to a non-blank value in `.env` or in the system environment (a blank `DISCOVERY_END_DATE=`, which `.env.example` ships today, does not trigger it: same blank rule as the rest of `load_env()`), `load_env()`
      logs one line, by name only ("DISCOVERY_END_DATE is no longer read; pass --end-date or end_date"). It replaces the override line for that name (one line, not two), is replayed into
      the file trace by the same path as the override notices, and covers every entry point. The value is ignored: a retired name from `.env` is **not applied** to `os.environ` at all. The name leaves the value allow-list (`LOGGED_VALUES`). **One line per process is enforced, not assumed:** `load_env()` runs once per entry-point module and the gateway imports the collector and crawler apps, and a retired name is never applied to `os.environ`, so every call would find it again; `env.py` keeps a set of retired names already noticed (reset by the `conftest.py` fixture that resets `_notices`), and a test calls `load_env()` three times with the stale line and expects one notice and one trace line.
      (5) **`news_collector/config.py`** (**first step of the code PR, run alone with the full suite before the rest**, since it changes what `import news_collector.config` sees in tests) drops its import-time `load_dotenv(override=False)` (choice (11) of `T-066b`, option 1); its docstring and the module docs say that a script
      importing it without an entry point must call `load_env()`. `DISCOVERY_START_DATE` is unchanged.
      (6) **Governance, done in this docs PR (#84):** constitution rule 7 drops `DISCOVERY_END_DATE` from its variable list, version 2.0.1 (PATCH, wording), "Last Amended" 2026-10-18;
      `SPEC.md` setup step 2 says the end date is an input. The code PR touches no constitution.
      (7) **Text the code PR must change** (so none is missed): `.env.example` (remove `DISCOVERY_END_DATE`, comment says "end date: pass it, default today UTC"); `docs/modules/news-collector.md`
      (the paragraph that says the run requests "`$DISCOVERY_START_DATE`–`$DISCOVERY_END_DATE`" and the universe-refresh "needs no code" claim: say "from the start date through today, and
      every pair re-runs on a new day"); `api/schemas.py` (`_DATE_DEFAULT_NOTE`); the `--end-date` help in `news_collector/main.py`;
      the `orchestrator.py` docstring ("Defaults to 2022-01-01 to today"); `data_mining/env.py` (allow-list, retired list); `tests/data_mining/test_env.py` (the allow-list test).
      (7b) **An invalid range is refused, not a crash** *(round 2)*: `DateRange` raises `ValueError` when start > end, and with the new default a request that sets only a future `start_date`
      reaches it. One function, `build_date_range(start, end) -> DateRange`, lives in `news_collector/config.py` next to the clock (not in the `api` package, which the CLI must not import) and raises `ValueError` with the message. Its own unit test covers start > end, start == end and a future end. Both API call sites (`api/routers/_common.py` and `discovery.py`) use it, so a third call site cannot bring back the unguarded `DateRange`. The API turns its error into **422** with the message; the CLI (`main.py`, which builds `DateRange` itself today) turns it into exit **1**
      with one line (no traceback). **A future end date is allowed** (no clamp): the range echoed in the response is the one used. **A malformed `--end-date`** (`2026/10/18`) is rejected by argparse (`type=` on the date; usage error, exit 2) with a test; the API already answers 422 through pydantic. A bad `DISCOVERY_START_DATE` in the environment (it fails when the parser is built) is **out of scope** for `T-077`.
      (7c) **Notice reaches the CLI trace** *(round 2)*: `data_mining/env.py` gets `replay_notices()` (logs the cached override and retired lines, each with its own label: the `.env override:` prefix belongs to the override notice, a retired line reads "`DISCOVERY_END_DATE` is no longer read" with no override prefix; tests check the text of both kinds in the trace); `startup_check.log_again()` calls it instead of its own loop (one path); `news_collector.main discover` calls it right after
      `setup_logging`, as the collector API already does through `log_again()`, so the CLI's file trace has the line too. The crawler API and the gateway keep no file trace (as decided in `T-066b`), so for them the notice is console-only; the acceptance lines on the file trace name the collector API and the CLI only.
      **The notice is logged by every entry point** (one line per process, by name): one mechanism in `load_env()`, no per-entry list; a stale line is a configuration error worth one line at each start.
      **Merge order:** #84 (docs) then the T-077 code PR straight after, with nothing else merged between; `SPEC.md` says "from T-077 on" so the text is true once the code lands.
      (8) **Real `.env`:** it is git-ignored, so no PR edits it; the maintainer deletes the stale `DISCOVERY_END_DATE=2026-08-10` line after the code PR merges. The acceptance run on this
      machine keeps the line, to show it is ignored and logged.
      Acceptance: tests that the default is the UTC date (patched clock; two calls on two days give two dates; a run at 23:59 and at 00:01 UTC); that an explicit `--end-date` and a request
      `end_date` win; that a start after the end is refused: API 422 with the message on both call sites (also a request with only a future `start_date`), CLI exit code 1 with one line and no traceback; that a future `end_date` is accepted and echoed; that `DISCOVERY_END_DATE` set in `.env` or in the system environment is ignored and
      logged once by name, with no value, also in the file trace of the collector API and of `news_collector.main discover` (`replay_notices()`); that `import news_collector.config` reads no `.env`; that both API responses and the CLI summary carry the
      range used; that a second run over the same pairs and range with resume off (`--no-resume`/`resume: false`) inserts 0 rows, and that a run over a different `end_date` with resume on re-runs the pairs; that a blank `DISCOVERY_END_DATE=` logs nothing and a retired name from `.env` is not in `os.environ`. On this machine with the stale line still in `.env`: the run uses today's date and the log names the variable.
      No database schema change.

## Work item 15 — Collector: the sitemap once per domain, not once per ticker (T-068–T-074)

Found 2026-10-18 by the `T-064` step 5 run: one ticker's cnbc discovery stored 578,117 rejected rows (+243 MB). Evidence, the decision (option B,
the maintainer) and the guardrails are in `PLAN.md` Work item 15. **Gates (maintainer approvals): G1** the design numbers of `T-068`; **G2** the
DDL, migration and rollback of `T-069` (constitution #10); **G3** the real-data step of `T-073`. Work on a **copy** of `urls.db` until G3.
The criteria of each gate were decided 2026-10-18 (`PLAN.md` Work item 15). Order: `T-068` → G1 → `T-069` → G2 → `T-070a` → `T-070b` → `T-071` → `T-072` → `T-073` (G3) → `T-074`. Each task's PR states what was run on which copy.

- [x] **T-068** *(read-only measurement and design; ends at G1; **done 2026-10-19, PR #88 merged**: the result is in `PLAN.md` Work item 15 "`T-068` result"; G1 decided by the maintainer 2026-10-19, freshness is 7 days for every run, the other five items of the G1 summary as proposed (confirmed by the maintainer's instruction to close the task); next is `T-069`; the script is `scripts/sitemap_preselect_check.py`)* Measure, on a copy of the real database and a fresh crawl of the sitemaps (no
      write to the real file): for `cnbc.com`, `ft.com` and `nasdaq.com` the number of entries, the share with a title and with a `lastmod`, the
      crawl time, and why `ft`/`nasdaq` store no sitemap rows today; the cost of matching one ticker against the whole cnbc sitemap, and the size
      of a cache row with its indexes. Then compare today's full scan with a **candidate preselection** (the design must name it, for example a token
      index of the URL slug) on **all 503 tickers** (offline, against the crawled file; no time limit) with the short and ordinary-word ones (`ON`, `A`, `IT`, `ARE`, `TTD`, `KLAC`, `NWS`, `NWSA`) reported one by one: report, per ticker, the `accept`/`review` pairs of each path and the recall, and state why the preselection cannot skip a pair the matcher would accept (a superset argument by construction, not only the sample); if that cannot be argued, the full scan stays. A fixture with a `news:title` entry shows that the title evidence is kept. **Result: a design note** (table columns, indexes, the
      preselection with its measured recall, the refresh rule **including what happens to an entry that leaves the sitemap: replace the entries on a complete crawl, or upsert and expire by "last seen", and what `T-072` does with an expired entry**, the estimated size and the time of a 503-ticker run, re-measured with the fixed sitemap date parser (PR #75)) in `PLAN.md`. A preselection that
      loses a pair is rejected (exact equality, no tolerance), or the full scan stays; there is no time limit for the 503-ticker match. **G1:** the maintainer approves the numbers and the design before `T-069`.
- [x] **T-069** *(schema and migration; ends at G2; constitution AI behavior #10; **done 2026-10-19**: PR #90 merged, G2 approved (option A); the real file is at `user_version` 4 since 2026-10-19, backup `urls.pre-t069.db`, evidence in PR #91; `SCHEMA_VERSION` 4, `SITEMAP_CACHE_DDL`, `rollback_sitemap_cache()`, `scripts/rehearse_sitemap_cache_migration.py`; next is `T-070a`)* Additive tables from the `T-068` design in `data_mining/schema.py`
      (canonical DDL plus an idempotent step in `run_migrations()`, no change to `discovered_urls` or `articles`): the cache of the entries of a domain's
      sitemap (url, **lastmod** (the pair's `pub_date`), **title** when the sitemap carries one (`news:title`; the matcher uses it as evidence, so the cache must not drop it), first and last seen) and **one row per crawl** for its refresh (`sitemap_crawls`: a `signature` (hash of the roots and `CACHE_VERSION`), started and finished at, UTC ISO strings, complete, the range start it covers, entries, failures; a row with no `finished_at` is a crawl in progress, the cross-process guard), with the indexes `(domain, lastmod)` on the entries, `(domain, complete, finished_at)` for the latest complete crawl (since `T-070b` the lookup orders by `started_at` and the primary key serves it; this index is no longer used, dropping it is a G2 decision) and a partial `(domain, started_at) WHERE finished_at IS NULL` for the in-progress lookup (the token index is in memory and not stored). **Criteria decided:** the cache covers any domain with `sitemap_roots` (a `domain` column), lives in `urls.db`, and is stale when the range is not covered or the last crawl is older than 7 days (a flag forces it). **G2 (approved 2026-10-19, before the merge):** the maintainer approved the DDL, the migration and the **rollback** (dropping the two
      tables restores the previous schema; the document says so and a test does it). Acceptance: **the complete DDL and rollback are the block in `PLAN.md` (`data_mining.schema.SITEMAP_CACHE_DDL`, the only copy), the migration runs exactly it, and the three design queries use an index (`EXPLAIN QUERY PLAN` test)**; the rollback drops both tables; the in-progress row and the stale-timeout query run on the migrated schema; the migration is idempotent on an old and on an empty database;
      `portfolio-nlp`'s read path is unaffected (a test opens the database as the extractor and the reader do); the old `apply_schema` callers still work.
- [x] **T-070a** *(code; after G2; the cache in one process; **done 2026-10-20**: PR #92 merged (`news_collector/sitemap_cache.py`, `crawl_sitemaps()` in the strategy, `--refresh-sitemaps` / `refresh_sitemaps`, `SITEMAP_RETRY_MINUTES`; the in-process guard is an `asyncio.Lock`, the cross-process one is `T-070b`); **first runtime evidence at version 4, 2026-10-20:** the gateway was restarted on `master` (`fcac3fe`, after the merge) and the real file still reads `user_version` 4 with the five cache objects, `discovered_urls` 1,059,452, `articles` 481,332 and `discovery_progress` 3,534 rows unchanged, the cache tables empty (no discovery has run), `quick_check` ok)* A `SitemapCache` object per domain (owner, injection and lifetime: `PLAN.md` Work item 15, "Who owns the cache in the code") fills and refreshes the cache **once per domain per run**, injected into `SitemapRSSStrategy` as an optional constructor argument (default `None` = today's live fetch, nothing else changes); the in-process guard makes concurrent tickers (and API requests) wait for the one crawl. A failed or **partial** crawl leaves the previous cache valid (the new entries are written in one transaction, run in `asyncio.to_thread`, through `data_mining.db.connect()` whose `busy_timeout` is already 30 s). Today `_crawl_sitemaps` swallows a failed child fetch or parse and returns what it got, so this task first makes the crawl report whether every root and child was fetched and parsed (the same change as the open failed-sitemap-fetch task of Work item 14, if that is merged; otherwise it is done here), and a cache is **updated** only by a complete crawl, never replaced (**complete = every root and child fetched and parsed, with no exception list (maintainer, 2026-10-19); a domain whose child always fails is unsupported; a refresh crawls from `min(the run's start, the latest complete crawl's range_start)`, so it never narrows the cache; the cache upserts and expires by "last seen", see `PLAN.md` "`T-068` result"**); the entries keep the `lastmod` filter semantics of today. Acceptance: tests
      for two concurrent tickers causing one crawl, **two API requests for two tickers causing one crawl**, the orchestrator and an API route sharing the guard in one process, no cache argument keeping the old behavior, a failed crawl, and one failed child among good ones, keeping the old cache, a wider date range triggering a refresh, a narrower one not (and a forced or 7-day refresh after a narrower run keeping the wider start), a child that fails every time keeping the crawl incomplete (no exception list); the age rule and the force flag; **freshness (G1 decided, 7 days for every run)**: evaluated once per domain at the start of a run from the age of the latest complete crawl's `started_at` (UTC); a crawl at most 7 days old is used whatever the run's end, an older one refreshes, and the clock never refreshes in the middle of a run; **`last_seen` is the crawl's `started_at`** and "current" means equal to the latest complete crawl's; **`CACHE_VERSION` is pinned by a fixture** (about a dozen URLs with the expected `normalize_url` and `is_article_url` result and a digest per version, `tests/scripts/test_sitemap_preselect_check.py`, moved with the code): a change of result without a new version fails; **the signature**: a changed or added `sitemap_roots` entry, or a bumped `CACHE_VERSION`, refreshes (and a failed refresh fails the pair); **the force flag** (`--refresh-sitemaps`, `refresh_sitemaps=true` on the routes) forces a crawl, honors the in-progress row and never narrows; **a refresh by a second connection makes the first process rebuild its index on the next run**; the purge also keeps the latest complete `sitemap_crawls` row and 90 days of history; **a failed refresh**: a cache that covers the range and is fresh enough is used, an older one is used with a warning carrying its age, one that does not cover the range (or none) fails the pair, not checkpointed; **"last seen"**: an entry missing from a complete crawl stops being current but stays in the table, an incomplete crawl changes no `last_seen`, the 90-day purge deletes cache rows only (a stored `discovered_urls` row is untouched); the cache opens its connection through `connect()` (a test), a write attempted during the refresh upsert succeeds, and a pair on another domain progresses during a refresh (the upsert does not block the event loop); the log says
      what was refreshed and how many entries. `T-076` made `SitemapRSSStrategy.failures` an instance list reset per `discover`; a once-per-domain fetch must carry the failure list to each ticker's pair (so the pair is still reported and not checkpointed).
- [x] **T-070b** *(code; after `T-070a`; the cache across processes; **done 2026-10-20 in PR #93**: `SitemapCache._claim`/`_wait_for`, `SITEMAP_STALE_MINUTES`, two operating-system processes tested, and run on the real `cnbc.com` sitemap on a scratch database)* The gateway and the crawler are two processes on one `urls.db`: the in-progress row of `sitemap_crawls` (`finished_at IS NULL`, `BEGIN IMMEDIATE`, the partial index) makes one process crawl and the other wait. Rules (`PLAN.md` "Who crawls"): the stale timeout is a setting, 30 minutes; a waiter polls every 5 s; a waiter whose winner **failed** crawls nothing and applies the failed-refresh cases of `T-070a`; one that waited the timeout takes over. Acceptance: two processes (two connections), one crawl; a crashed in-progress row older than the timeout lets a new crawl start; a failed winner; a takeover; the stale-timeout query runs on the migrated schema.
- [x] **T-071** *(code; **done 2026-10-21 in PR #99**: `data_mining/token_index.py` (moved from the script, which imports it), `SitemapRun.select`/`holds` and the index built once per crawl by `SitemapCache`; the strategy classes only the candidates and returns and stores only `accept`/`review`, counting `rejected_not_stored` (`PartialStats`, `DiscoveryStats.total_rejected_not_stored`, the orchestrator logs, the CLI summary, `RunAllResponse.total_rejected_not_stored`, `DiscoverResponse.rejected_not_stored`); a `tqdm` bar over the (company, domain) pairs, off when not a terminal, and the orchestrator's `progress` callback (the bar does not count entries: matching them takes milliseconds now); `reclass-sitemap` on the same index with `--full-scan` / `"full_scan": true` as the fallback for a matcher change the index does not know about; **evidence on a copy of the real file, 10 tickers: the full scan 266.2 s and +1,060,682 rows (1,060,484 rejects) against 4.0 s and +198 rows (0 rejects), the same 17,235 `accept`/`review` pairs; with `--refresh-sitemaps` the discovery wrote its crawl row, the first evidence of the `T-070b` guard on a copy; the 1,090,441 counted rejects reconcile exactly (1,060,484 + 29,957 of the 34,818 pairs already stored, of which 16 `accept`/`review` rows the matcher now rejects: 13 `sitemap`, 3 `ddg`); **equality on the whole universe, 2026-10-21, on the real `cnbc.com` cache (crawl of 2026-10-20 19:15, 109,550 entries, read-only): 504 tickers, 38,104 `accept`/`review` pairs by the index and by the full scan, 0 mismatching tickers (`scripts/sitemap_preselect_check.py production`)**; the first run on the real file, with its crawl row, is the maintainer's after the merge and a backup**)* Per-ticker matching from the cache: **`TokenIndex` moves from `scripts/sitemap_preselect_check.py` into `src/` and the script imports it**; the strategy selects the candidates (as designed in `T-068`), **applies the run's end (`lastmod <= end`, no `lastmod` kept) when it reads them**, feeds the cached entries (read in URL order; the winner of a URL does not depend on it) before the live RSS/Wayback ones (first URL wins, as today; a test with a URL in the cache and in an RSS entry), classes them with the existing
      matcher, and returns only `accept` and `review` pairs; the number of `reject` pairs that were **not stored** is counted and reported (a new field next to
      `total_rejected`, which stays for the `ddg` rejects and the rows already stored; the CLI summary, the orchestrator log and **both** API responses are updated: `RunAllResponse` and the per-domain `DiscoverResponse` returned by `/cnbc/`, `/ft/` and the other domain routes, which carries no reject count today; a test per response).
      `ddg`, `rss`, wayback and `api` behavior is unchanged. **Acceptance: the equivalence test of guardrail 2** on a fixture corpus (the `accept`/`review`
      pairs, their class, reason and `pub_date`, equal to the full scan for the tickers of `T-068`), a test that a batch of a 578,000-entry cache (a stress size, the real in-range size is 109,493 article URLs, measured by `T-068`) writes no reject row, a test that the pair's `pub_date` is the cached `lastmod` (and that a URL listed twice keeps the first entry's, as today: 23 such URLs, 10 with different `lastmod` values), a run with an end date in the past equal to the full scan with that end, the equality test of `scripts/sitemap_preselect_check.py` kept as a test (its fixture, anchor cases and mutation checks are in `tests/scripts/test_sitemap_preselect_check.py`), a test of
      the stats, a `tqdm` progress bar in the CLI over the tickers (and entries) being matched, with a test that it does not break a non-tty run, and, on a copy, a 10-ticker cnbc run whose row delta and time are reported against a **fresh baseline measured on the same copy with the current full scan and the fixed sitemap date parser (PR #75)** (the 2026-10-18 figure, 578,117 rows for one ticker, was taken before that fix and is history, not the baseline).
      `SPEC.md` FR-014 and the `T-056c` wording of the module doc say that sitemap rejects are no longer stored.
- [x] **T-072** *(code; **done 2026-10-20 in PR #94**: `news_collector/reclass_cache.py`, `reclass-sitemap` on the CLI (dry run by default, `--apply` writes, a `tqdm` bar) and `POST /discovery/reclass-sitemap` (`apply` false by default, `tickers` required and at most 50, a per-ticker INFO log); `SitemapCache.begin(allow_crawl=False)` reads the cache with no network; the 438 known-gap pairs are all in the kept 323,864 and none in the set `T-073` deletes, measured read-only on the real file; the four scripts are listed in `docs/modules/news-collector.md`, "Re-class from the sitemap cache")* Re-class from the cache with no network (**current entries only**, those seen by the latest complete crawl; an expired entry is not re-classed, and a stored row is never derived from the cache): a command (CLI and the matching API route) that classes the cached entries again for the
      given tickers (or all) and enqueues the pairs whose class is now `accept`/`review`, promote-only, through the same `enqueue_batch` path. Acceptance: idempotent
      (a second run inserts and promotes nothing), a test that a matcher change moves a pair from nothing to `review`, and `scripts/reclass_rejects.py`'s docstring and the
      known gap in `TASKS.md` say what they cover now (stored `ddg` rejects, the 323,864 sitemap rejects that keep a quarantined article, and the rows of before this item; the script is **not retired**), and report how many of the 438 known-gap pairs of Work item 12 sit in the set `T-073` deletes (never fetched, no article) and how many in the kept 323,864; a pair in the deleted set must be re-derivable from the cache, or the gap loses its population and what replaces them. Each of the four scripts that read `reject` rows is listed with what it still covers after `T-071` and `T-073`: `reclass_rejects.py` (the stored rejects that remain: `ddg`, uncovered domains, the kept sample rows), `retag_urls.py` (legacy rows with no `match_reason`, unaffected, but its `--hand-check` sheet draws 40 `reject` pairs), `score_ticker_rules.py` (reads the labeled sample files, so the kept rows matter, and `--diff` scans the remaining corpus) and `ticker_validation_sample.py` (its strata include `(reject, sitemap)`: say what it draws when that stratum is empty or tiny, for example the pairs classed `reject` from the cache by the current `classify`, and a test of that behavior instead of an assumption).
- [x] **T-073** *(data; ends at G3; **done 2026-10-20**: the tools are PR #95 (`fill_sitemap_cache.py`; `reclaim_sitemap_rejects.py` with `evidence`, `reclaim`, `compact`), the index drop is PR #96 (schema version 5); **G3 approved by the maintainer 2026-10-20 with N = 578,113** (backup `urls_t_073.db`, the cnbc cache filled on the real file with 109,550 entries, `evidence` on the real file equal to the rehearsals, the 17 accepted pairs); **the real `reclaim` ran 19:33 to 19:58 UTC** (25 minutes on this disk) with the gateway and the crawler stopped: `discovered_urls` 1,059,452 → 481,339, `articles` 481,332 unchanged with 0 orphans, `accept` 104,614 and `review` 51,746 unchanged, rejects 903,092 → 324,979, file 2,570,055,680 → 2,289,635,328 bytes (280 MB), `quick_check` ok on the file in place (238 s), the backup kept until the maintainer deletes it; **the `--revert` list of the `T-062` run (`data/retag/reclass_promoted.csv`, 448 ids) is intact**: all 448 rows are still present as `review` after the reclaim (and were in the backup), checked 2026-10-20; the freed space is 280 MB against the **expected shrink of about 243 MB** (the `KLAC` run's growth): the 37 MB difference was not measured (compaction also releases free pages, which is the likely cause); the stored `accept`/`review` pairs that are not in the cache are counted (13,686 before the range, 909 left the sitemap), not listed by year (the evidence prints their sum); **the schema-version-5 migration of the real file is not applied yet**: it runs at the first open of the queue with the merged code, and the gateway restarted at 20:08 UTC had not opened it (the file still read `user_version` 4 with the five cache names); `T-074` records it. See `docs/modules/news-collector.md`, "Reclaim the never-fetched sitemap rejects")* Reclaim the sitemap rejects already stored **that were never fetched** (today: 901,977 `sitemap` rejects, all `cnbc.com`, of which **578,113 are `skipped` with no `articles` row**, the `KLAC` run, and **323,864 have a quarantined article** (statuses `ok` 315,456, `thin_content` 8,144, `failed` 264) and are **not deleted**: `articles.id` is `discovered_urls.id`, the bodies are kept for `portfolio-nlp` and the release path, and `reclass_rejects.py` still works on them; measured read-only 2026-10-18; the 1,115 `ddg` rejects stay). **Before deleting:** fill the cache for cnbc, prove on the copy that `T-072` finds again every **in-range** `accept`/`review`
      pair that is stored today for a sample of tickers (pass condition: **0 in-range pairs missing** in the sample; the cache holds only entries inside the configured range, and about 65% of the stored `accept`/`review` sitemap rows are out of range after the evidence of PR #75 (**`T-068` measured 72.1% on 2026-10-19: 100,210 of 138,962 by the crawl's `lastmod`, 73.8% by the year in the URL; the 65% was read from PR #75's 62,379 of 93,226 `ok` articles, 66.9%, which counts every source, and the same count over the sitemap rows alone is 62,140 of 86,724, 71.7%: same numerator, a different denominator, so the figures agree**), so those are counted and listed by year, they stay stored and are never part of the delete; **a third kind, measured by `T-068`: 6,390 of the 138,962 (4.6%, 5,197 URLs) are of URLs that left the sitemap, so they are not in the cache and are not "missing": they are counted and listed too, stay stored and are never part of the delete;** the stored `accept`/`review` rows are not touched), and list what the `--revert` file of the `T-062` run needs (its rows are
      promoted ones and stay). **Scope decided (the three narrowings below were confirmed by the maintainer 2026-10-18; G3 still approves the evidence):** only source `sitemap` (**narrowed from `sitemap`/`rss` in the PR #95 review, 2026-10-20**: the proof re-derives stored pairs from the sitemap cache and no `rss` pair can be; 0 `rss` rows qualify today; `rss` returns only with a proof that covers it), narrowed to never-fetched rows (`status='skipped'`, no article), and to rows whose domain has a filled cache (today `cnbc.com` only: 901,977 `sitemap` rows, **of which 578,113 qualify**, 0 `rss`; `ft` and `nasdaq` have none), so a Wayback or RSS reject of a domain the cache does not cover is never deleted. The PR lists how many of those rows are **not** in the cache (older than the range, so never re-derivable) and why; a stored reject that the cache cannot re-derive is deleted only if it is outside the configured date range or its URL is no longer in the sitemap, and the count of each is stated at G3 (**`T-068` measured it 2026-10-19 with `stored`: of the 578,113 (the `KLAC` run of 2026-10-18, one day old), 0 are of URLs that left the sitemap, a count that grows with age and that G3 recounts, 468,661 (81.1%) are before 2022 by the crawl's `lastmod`, and 109,452 (18.9%) are in range and re-derivable, with the default range start 2022-01-01**; the run was before the PR #75 date fix, which is why most are out of range). **Kept on purpose:** every row whose id is in a committed hand-labeled sample (`data/ticker_*_sample.csv` and `*_manual.csv`: 593 distinct ids, **84 of them are `sitemap` rejects today**, none of them `skipped` without an article, so today the exclusion removes nothing and stays as a safeguard, measured read-only 2026-10-18), because `score_ticker_rules.py`, `ticker_validation_sample.py` (`check_sheet`) and the hand-check refuse or mis-score a sheet whose ids are gone; the PR recomputes that count on the day. **At G3** the maintainer approves the evidence, then: a backup that passes `quick_check`, a row-count fingerprint, one transaction that deletes only
      `match_class='reject' AND status='skipped' AND NOT EXISTS (SELECT 1 FROM articles a WHERE a.id = discovered_urls.id) AND source = 'sitemap' AND domain IN (<the domains with a filled cache, printed in the PR>) AND id NOT IN (<the ids of the committed hand-labeled samples>)`, the fingerprint checked after, and `VACUUM INTO` a new file with the disk check (free space of the file size plus the WAL headroom), the exclusive lock on the file (it fails while any connection is open on the host), the swap keeping the mode, the owner and the journal mode, the old file kept as the backup until the maintainer deletes it.
      Acceptance: counts before and after in the PR, `quick_check` ok, `articles` unchanged in count and with **no orphan id** (`articles.id` with no `discovered_urls` row: 0 before and after), the `accept`/`review` rows unchanged, and the file size reported before and after: **expected shrink about 243 MB** (578,113 rows at about 420 bytes), the size of the `KLAC` run, because the rest of the file is article bodies, which stay.
      **B3 decision (maintainer, 2026-10-20, answer A): the real cnbc cache is filled before G3 by a tracked, no-discovery command (`scripts/fill_sitemap_cache.py`, **delivered by PR #95** together with `compact`, one shared approval gate and the `evidence --expect-now-rejected 17` pin), not by waiting for `T-071`. It is a real-file write of about 109,000 rows in the two cache tables only: it needs the `urls_t_073.db` backup and the maintainer's approval first, and `evidence` is re-run on the real file after it, so G3 judges the real numbers.**
      **Proof decision (maintainer, 2026-10-20): the 17 in-range pairs now classed `reject` (a matcher change, not a cache loss) count as passing the proof; they stay stored; `evidence --expect-now-rejected 17` exits 0 only for exactly that count.**
      **Unused index `ix_sitemap_crawls_latest`: DROPPED (maintainer, 2026-10-20), schema version 5, in this task's PR.** Since `T-070b` (PR #93) the code finds the latest complete crawl by the primary key `(domain, started_at)` and never used this index `(domain, complete, finished_at)`. Done in code: removed from `SITEMAP_CACHE_DDL`, `DROP INDEX IF EXISTS` in `run_migrations()` (`DROP_UNUSED_CRAWLS_INDEX`), `SCHEMA_VERSION` 5, `rollback_sitemap_cache()` and its guard follow (it expects the current version), `EXPECTED_CACHE_OBJECTS` of the rehearsal script, the schema tests (a version-4 file loses the index and keeps its cache and pipeline rows), the `PLAN.md` DDL block, `docs/modules/data-mining.md` (the reader contract: `portfolio-nlp` accepts a higher version, only a private index was removed) and the runbook ("After merging the schema-version-5 change"). Rehearsed on a copy of the real file 2026-10-20: 0.005 s, pipeline fingerprint identical, 4 → 5, rollback to 3 clean. **The real file is migrated at the first open of the queue with the merged code (a request such as `GET /discovery/stats`, a collector or extractor run, `reclass-sitemap --apply`); a restart alone does not (the gateway opens the queue when a request needs it): backup first (`urls_t_073.db` covers it, constitution #10), then check the four names and the three counts.**
- [x] **T-074** *(close-out; **done 2026-10-21**)* `docs/modules/news-collector.md` (including its "Auditing ticker tagging" and re-class sections, which describe a `reject` population that shrinks from 903,092 rows to 328,075 today (323,860 `sitemap` that keep a quarantined article, 4,215 `ddg`); the class counts of `GET /discovery/stats` and the docs drop **by design**, not as data loss), `SPEC.md` FR-014, the architecture artifacts content-only (constitution #11), the interim caution of this item removed, and the item's tasks moved to `CHANGELOG.md` (#12) with the test count of the day.
      **Closure evidence (2026-10-21, read-only on the real file):** `user_version` 5 and `ix_sitemap_crawls_latest` gone (remaining: `ix_sitemap_crawls_open`, `ix_sitemap_entries_lastmod`), `quick_check` ok; the first real discovery (`cnbc.com`, 503 tickers, +8,757 rows, +7.7 MB, 55,065,566 sitemap rejects counted and not stored, backup `urls_t_071.db`) and the same discovery twice on a copy (198 rows, then 0) are in the runbook ("The real file after Work item 15 (T-074)"); the two architecture artifacts were reconciled with their titles unchanged; **1314 passed, 1 skipped**. Not exercised on the real file: the `T-070b` guard (the cache was fresh, no crawl row), which first runs when the cache is older than 7 days or with `--refresh-sitemaps`.
