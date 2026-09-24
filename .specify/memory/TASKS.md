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

## Status

Work item 4 (sec_edgar multi-filing bug fix) is built, unit-tested, and
verified live (`T-028`–`T-035` all done 2026-09-21). This was made the top
priority per the bug report, ahead of the rest of the backlog. **Merged**
2026-09-21 (PR #39).

Work item 3 is built and merged (PR #36), its artifacts are reconciled
(`T-027`) and its constitution wording is amended (`T-024`). One item remains
open on purpose: `T-026`'s redeploy hand-off — the operator redeploys, then
`portfolio-financial-analysis`'s `T-052` runs `quant backfill-actions`. The
downstream client's `probe('XOM')` has already been shown to return `True`
against the merged code. Work item 1 (CI workflow) was built and then reverted
at the maintainer's request (#35), so T-001–T-007 stay unchecked and
T-010–T-014 are moot until CI is wanted again.
