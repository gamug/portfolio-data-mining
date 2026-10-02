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

## Work item 6 — Expose the cover-page share count (code, cross-repo origin)

Origin: `portfolio-financial-analysis` `T-132(a)` (market cap from a point-in-time share count).

- [x] **T-043** In `sec_edgar`, `get_financials` returns the filing's cover-page share count
      as a new, additive top-level field — the three statements, `corrections` and
      `reconciliation_errors` are unchanged:
      `"cover": {"shares_outstanding": [{"value": <shares>, "as_of_date": "<the dei fact's
      instant date>", "class_member": <null | "us-gaap:CommonClassAMember", ...>}]}`.
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

## Status

Closed Work items 3, 4 and 5 are in `CHANGELOG.md` (Work item 5 closed 2026-10-02, once
`portfolio-financial-analysis` re-verified the redeployed gateway and closed its own
`T-117`/`T-118` in its PR #104).

Work item 1 (CI workflow) was built and then reverted at the maintainer's
request (#35), so T-001–T-007 stay unchecked and T-010–T-014 are moot until
CI is wanted again.

Work item 6 (cover-page share count) — `T-043` is done in this PR.
