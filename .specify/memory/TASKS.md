# TASKS.md — `portfolio-data-mining`

Discrete, checkable task breakdown for `.specify/memory/PLAN.md`. Each task
references the plan work item and the `SPEC.md` section it closes. Check a
box only when its acceptance criterion (in `PLAN.md`) is actually met — not
when the code is merely written.

**Closed work items live in `.specify/memory/CHANGELOG.md`**, moved there verbatim
(task IDs unchanged) once every task in them is done, superseded, or moved elsewhere —
see constitution AI behavior #12. This file carries only open work items.

Task IDs are stable, same rule as `SPEC.md`'s `FR-0xx`/`NR-0xx`: don't
renumber; mark a cancelled/superseded task in place instead.

## Work item 1 — Add a CI workflow (code, no blockers)

- [ ] **T-001** Write `.github/workflows/ci.yml`: trigger on `push` to
      `master` and `pull_request`, `runs-on: ubuntu-latest`. → `PLAN.md`
      Work item 1, step 1.
- [ ] **T-002** Add the job steps: checkout → `astral-sh/setup-uv` (pin
      Python 3.12) → `uv sync --group dev` → `uv run ruff check .` →
      `uv run ruff format --check .` → `uv run mypy --config-file=
      .code_quality/mypy.ini src apps cli` → `uv run pytest -q`. → step 2.
- [ ] **T-003** Verify locally first (so the first real CI run isn't a
      surprise): run the same four commands from a clean `uv sync --group
      dev` and confirm all pass. → `PLAN.md` Work item 1 acceptance,
      second bullet.
- [ ] **T-004** Push on a throwaway branch and confirm the workflow
      triggers and completes successfully end to end. → same acceptance
      bullet.
- [ ] **T-005** Deliberately break one gate (e.g. a trivial `ruff`
      violation) on that throwaway branch, confirm the workflow fails, then
      revert the deliberate breakage before merging. → `PLAN.md` Work item
      1, third acceptance bullet ("gate actually gates").
- [ ] **T-006** Add a CI status badge to `README.md` once the workflow has
      a green run on `master`. → `PLAN.md` Work item 1, approach step 4
      (cosmetic, not a hard acceptance criterion).
- [ ] **T-007** Update `SPEC.md` NR-007 and §13 item 5 to note the workflow
      now exists (annotate in place, keep the item number). Also update the
      two architecture artifacts per constitution AI behavior #11
      (Portfolio Thesis + Portfolio Data Mining) — reconcile, never rename.
      → `PLAN.md` Work item 1, fourth acceptance bullet.

## Work item 2 — Make the CI check required (ops, blocked on Work item 1)

- [ ] **T-010** *(maintainer)* Once T-004 has produced at least one
      successful run on `master`, open the branch protection settings for
      `master`. → `PLAN.md` Work item 2, step 1.
- [ ] **T-011** *(maintainer)* Add the CI job as a required status check
      for `master`. → step 2.
- [ ] **T-012** *(maintainer, optional)* Require branches to be up to date
      before merging, if desired. → step 3.
- [ ] **T-013** *(maintainer)* Confirm a PR with a deliberately failing run
      is blocked from merging in the GitHub UI. → `PLAN.md` Work item 2,
      first acceptance criterion.
- [ ] **T-014** Update `SPEC.md` §13 item 5 to mark it fully resolved
      (workflow exists *and* is enforced), distinct from T-007's "exists"
      milestone. → `PLAN.md` Work item 2, second acceptance criterion.

## Work item 5 — Fix `sec_edgar`'s revenue-total contradiction (code, cross-repo origin)

**Open — PR #44, APA revenue instance only. Does not close
`portfolio-financial-analysis`'s `T-117`/`T-118`, which stay open there.**

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
- [ ] **T-041** *(cross-repo)* Hand off findings to `portfolio-financial-analysis`'s
      `T-118` — this fix corrects APA's revenue instance only and does **not** close
      `T-117`/`T-118` there; leave both open until the general synthesis defect (T-042) is
      addressed and that repo's own re-verification against a redeployed `sec_edgar`
      confirms nothing is left to correct on APA. → `PLAN.md` Work item 5 acceptance
      criteria; that repo's own `T-118`. **Not done** — do not check this until that
      repo's own `T-117` guard is confirmed to find nothing left, post-redeploy.
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

## Status

Closed Work items 3 and 4 are in `CHANGELOG.md` (Work item 3's last task,
`T-026`, closed once `portfolio-financial-analysis`'s `T-052` verified the
redeployed gateway live on 2026-09-21).

Work item 1 (CI workflow) was built and then reverted at the maintainer's
request (#35), so T-001–T-007 stay unchecked and T-010–T-014 are moot until
CI is wanted again.

Work item 5 is open: T-036–T-040 (PR #44) and T-042 are done — the general
synthesis-defect fix now exists alongside `T-118`'s APA-revenue-specific
reconstruction. T-041 (cross-repo closure of `portfolio-financial-analysis`'s
`T-117`/`T-118`) stays unchecked until that repo re-verifies against a
redeployed `sec_edgar` itself.
