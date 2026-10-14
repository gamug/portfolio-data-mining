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

## Work item 8 — Add filer CIK and period of report to EDGAR filing-list rows (T-045)

Requested by `portfolio-financial-analysis` for its `T-149` (filing identity):
"Task: add the filer CIK and the period of report to the EDGAR filing-list rows. Requested by portfolio-financial-analysis for its T-149 (filing identity)."

- [ ] **T-045** Add filer CIK (10-digit zero-padded string) and period of report ("YYYY-MM-DD" or null) to `_filing_to_dict` in `src/sec_edgar/agent.py`. Update docstrings in `agent.py`, OpenAPI route descriptions in `apps/sec_edgar_api.py`, `SPEC.md` FR-004, and `docs/modules/sec-edgar.md` documenting the new fields and that `{ticker}` accepts ticker or CIK. Add hermetic unit tests in `tests/sec_edgar/test_agent.py`. → `PLAN.md` Work item 8.

## Work item 9 — Stop tagging articles with the wrong company (code + data, origin: `portfolio-nlp` T-128)

Requested by `portfolio-nlp` (its Work item 15, decision D3 = fix at the
source, 2026-10-14): the discovery matcher `BaseConnector._company_pattern`
accepts a ticker or the *first word* of the company name appearing anywhere in
a URL slug or title, so tickers that are English words (`A`, `ON`, `IT`,
`ARE`, …) and generic first words (`Trade`, `Bank`, `First`, `Dollar`, …) tag
unrelated articles. → `PLAN.md` Work item 9, `SPEC.md` §13 item 10.

- [ ] **T-053** Commit a rerunnable, read-only audit under `scripts/` that
      reproduces the 2026-10-14 counts from `urls.db` per `(ticker,
      strategy/source)`: ticker-only vs name-token-only vs both matches, the
      eight English-word tickers, the generic-first-word companies, and the
      `ddg` rows not yet broken out. → step 1.
- [ ] **T-054** Measure each failure mode's real error rate. Build **one**
      shared labeled sample of `(url, ticker)` pairs from `urls.db`,
      stratified by match mode (ticker-only / name-token-only / both) and
      including known-correct pairs; it is labeled with `portfolio-nlp`'s
      relevance judge (its T-130; this repo has no ML/judge) and spot-checked
      by hand on a small subset to validate that judge. Commit the sample
      (pairs + labels) so it can be reused: `portfolio-nlp` T-117 reuses it,
      and T-056's retention measurement is computed on the same pairs rather
      than a second set. **Dependency:** the judge is `portfolio-nlp` T-130
      (not built yet; portfolio-nlp PR #102), the critical-path item for both
      repos — order: its T-130 → this T-054. **Fallback so this work item is
      never gated on that repo:** if T-130 has not landed when T-054 is ready
      to start, hand-label the sample here (a smaller sample, re-labeled with
      the judge later to validate it). The committed file carries `url`,
      `ticker`, `company`, `match_mode`, `label` (about the company: yes/no)
      and `labeled_by` (judge/hand), which is what `portfolio-nlp` T-117
      consumes. Report; the rule is chosen from this, not assumed. → step 1.
- [ ] **T-055** Record the matching-rule design (explicit ticker forms only,
      full normalized name + curated aliases, common-word stoplist for a lone
      first word; no new dependency) with the T-054 numbers behind it, and get
      it approved before coding. → step 2.
- [ ] **T-056** Implement the rule in `_company_pattern` /
      `url_matches_company`, rewrite the base-class docstring postcondition,
      and add hermetic parametrized tests (ambiguous tickers, generic first
      words, alias cases, must-still-match regressions). No `BaseConnector`
      signature change. → step 3.
- [ ] **T-057** Re-tag `urls.db`: dry run on a copy (before/after counts per
      ticker and source; count URLs also tagged to a correct ticker), then the
      real run with a restorable backup and a reversible representation
      (quarantine over delete) that `portfolio-nlp`'s pending queries
      (`fetch_status = 'ok'`, non-empty `body_text`) will actually exclude —
      agree the shape with that repo first. The run also writes the list of
      removed `(url, ticker)` / article ids, which T-058 hands over. → step 4.
- [ ] **T-058** Hand off: tell `portfolio-nlp` the corrected corpus is ready, with the
      list of removed article ids (it needs them to clean its existing results
      and `sector_summary` — its T-131)
      (unblocks its Work item 15 development and its re-measurement on the
      cleaned data); close `portfolio-nlp` `T-128`. → step 5.
- [ ] **T-059** Close-out: `SPEC.md` §13 item 10 / §14 annotated,
      `docs/modules/news-collector.md` updated, both architecture artifacts
      reconciled (never renamed), then move this work item to `CHANGELOG.md`
      (constitution AI behavior #12). → acceptance criteria.

## Status

Closed Work items 3, 4, 5, 6 and 7 are in `CHANGELOG.md` (Work item 5 closed 2026-10-02 once
`portfolio-financial-analysis` re-verified the redeployed gateway and closed its own
`T-117`/`T-118` in its PR #104; Work item 6, `T-043`, closed 2026-10-02 in PR #48;
Work item 7, `T-044`, closed 2026-10-09).

Work item 8 (`T-045`, filer CIK and period of report on filing-list rows) is open and active.

Work item 9 (`T-053`–`T-059`, wrong-company tagging, requested by `portfolio-nlp`) is open;
`portfolio-nlp` waits on it before starting its own category-stage development.

Work item 1 (CI workflow) was built and then reverted at the maintainer's
request (#35), so T-001–T-007 stay unchecked and T-010–T-014 are moot until
CI is wanted again. No other work item is open.
