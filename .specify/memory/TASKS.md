# TASKS.md — `portfolio-data-mining`

Discrete, checkable task breakdown for `.specify/memory/PLAN.md`. Each task
references the plan work item and the `SPEC.md` section it closes. Check a
box only when its acceptance criterion (in `PLAN.md`) is actually met — not
when the code is merely written.

Task IDs are stable, same rule as `SPEC.md`'s `FR-0xx`/`NR-0xx`: don't
renumber; mark a cancelled/superseded task in place instead.

## Work item 1 — Add a CI workflow (code, no blockers)

- [x] **T-001** Write `.github/workflows/ci.yml`: trigger on `push` to
      `master` and `pull_request`, `runs-on: ubuntu-latest`. → `PLAN.md` Work
      item 1, step 1. **Done 2026-09-19** (PR #32).
- [x] **T-002** Add the job steps: checkout → `astral-sh/setup-uv` (pin Python
      3.12) → `uv sync --group dev` → `uv run ruff check .` → `uv run ruff
      format --check .` → `uv run mypy --config-file= .code_quality/mypy.ini
      src apps cli` → `uv run pytest -q`. → step 2. **Done 2026-09-19** (PR
      #32) — with `uv sync --locked --group dev` rather than bare `uv sync`,
      so a stale `uv.lock` fails the run.
- [x] **T-003** Verify locally first (so the first real CI run isn't a
      surprise): run the same four commands from a clean `uv sync --group dev`
      and confirm all pass. → `PLAN.md` Work item 1 acceptance, second bullet.
      **Done 2026-09-19**: from a clean `git archive` checkout with no
      `.env`/`.venv` and API-key variables unset — ruff, format, mypy clean,
      **216 passed** (the specs said 213; corrected).
- [x] **T-004** Push on a throwaway branch and confirm the workflow triggers
      and completes successfully end to end. → same acceptance bullet. **Done
      2026-09-19**: PR #32's own run green (a bare branch push does not
      trigger the workflow — only `pull_request` and push-to-`master`); all
      nine steps ran, 216 passed.
- [x] **T-005** Deliberately break one gate (e.g. a trivial `ruff` violation)
      on that throwaway branch, confirm the workflow fails, then revert the
      deliberate breakage before merging. → `PLAN.md` Work item 1, third
      acceptance bullet ("gate actually gates"). **Done 2026-09-19**:
      throwaway draft PR #33 (unused import, `F401`) failed at "Ruff lint"
      with format/mypy/tests skipped; closed unmerged, branch deleted.
      Committed with `--no-verify` on purpose — the repo's own
      pre-commit/pre-push hooks auto-fix or reject the violation before it
      reaches CI.
- [ ] **T-006** Add a CI status badge to `README.md` once the workflow has
      a green run on `master`. → `PLAN.md` Work item 1, approach step 4
      (cosmetic, not a hard acceptance criterion).
- [x] **T-007** Update `SPEC.md` NR-007 and §13 item 5 to note the workflow
      now exists (annotate in place, keep the item number). Also update the
      two architecture artifacts per constitution AI behavior #11 (Portfolio
      Thesis + Portfolio Data Mining) — reconcile, never rename. → `PLAN.md`
      Work item 1, fourth acceptance bullet. **Done 2026-09-19** (PR #32):
      `SPEC.md` NR-007, §11 item 4, §13 item 5, §14 row 5, and the test count
      (213 → 216); the constitution's Executable cmds #1 and Code & Git #7
      (v1.4.1); `PLAN.md` Work item 1 status. The two architecture artifacts
      are reconciled too (content only, titles untouched): Portfolio Data
      Mining and Portfolio Thesis, both 2026-09-19.

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

## Work item 3 — yfinance corporate-actions endpoint (code, moved from `portfolio-financial-analysis`)

- [ ] **T-020** Add `StockPriceFetcher.get_corporate_actions(ticker,
      start_date, end_date)` to `src/pricing/fetcher.py`: `yf.Ticker(t).
      dividends`/`.splits`, filtered to the inclusive range by the series'
      own index date (tz-aware index normalized to `YYYY-MM-DD`), returning
      `{ticker, start_date, end_date, source: "yfinance", dividends:
      [{date, value}], splits: [{date, value}], warning}`; never raises — a
      yfinance failure yields empty lists plus `warning`. Tests in
      `tests/pricing/test_fetcher.py` mocking `yf.Ticker`: in-range
      dividends + splits, out-of-range rows excluded, empty range, tz-aware
      index, yfinance raising, the `1900-01-01`–`1900-01-02` probe range. →
      `PLAN.md` Work item 3, steps 1–2.
- [ ] **T-021** Add `GET /pricing/{ticker}/actions?start_date=&end_date=` to
      `apps/pricing_api.py` (tag `Pricing`, `start_date > end_date` → 400,
      empty range → 200 with empty lists, never 404). → step 3.
- [ ] **T-022** Add an `actions` subcommand to `cli/pricing_cli.py`
      mirroring the route. → step 4.
- [ ] **T-023** Update `docs/modules/pricing.md`, and the endpoint/subcommand
      lists in the `apps/pricing_api.py` and `cli/pricing_cli.py` docstrings
      and `README.md` wherever they enumerate pricing routes. → step 5.
- [ ] **T-024** Finalize the specs once the code exists: `FR-012`,
      `SPEC.md` §2.1 and §12 were written ahead of the code marked
      "planned" — drop that marker, and update the §10/NR-004 test count
      (216 today) and the constitution's "Executable cmds" test-count line.
      Separately, as its own reviewed change per the constitution's
      Governance section, amend Technological stock #2 and AI behavior #1
      so `yfinance` is listed under `pricing` (MINOR bump). → `PLAN.md`
      Work item 3, "Constitution notes".
- [ ] **T-025** Verify live: run `uv run apps/pricing_api.py`, `curl` the XOM
      range and the `1900-01-01` probe range from `PLAN.md` Work item 3's
      acceptance criteria, and run the `actions` CLI subcommand. State
      yfinance's unofficial/no-SLA posture in the PR. → acceptance criteria.
- [ ] **T-026** *(cross-repo)* After this lands and the pricing service is
      redeployed, hand off to `portfolio-financial-analysis`'s `T-052`
      (`QuantPricingClient.probe('XOM')` → `True`, then `quant
      backfill-actions` → `corpact-v1` rows). That check lives in PFA, not
      here. → acceptance criteria, PFA bullet.
- [ ] **T-027** Reconcile both architecture artifacts (Portfolio Thesis +
      Portfolio Data Mining) per constitution AI behavior #11 — content
      only, never the title. → `PLAN.md` Work item 3, last acceptance
      bullet.

## Status

Work item 1 is built (PR #32) except **T-006** (README badge — needs a green
run on `master`, so it follows the merge as its own small PR). Work item 2
(T-010–T-014) is maintainer-only and now unblocked once a run exists on
`master`: require the check `CI / lint / type-check / test`. The two
architecture artifacts named in T-007 already reflect the workflow and say
plainly it is not yet a required check. T-020–T-027 (Work item 3)
are independent of all of it — they touch only `pricing` code, tests and docs
— and can begin in either order; `portfolio-financial-analysis`'s `T-052` is
downstream of them.
