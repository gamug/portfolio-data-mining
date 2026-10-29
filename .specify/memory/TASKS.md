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
      .code_quality/mypy.ini src apps cli scripts` → `uv run pytest -q`. → step 2.
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

## Work item 16 — The gateway keeps an untagged duration column, and the financials payload carries the filing's own filed facts (T-159b)

Requested by `portfolio-financial-analysis`'s T-143 pilot (part A, the reviewer's empty-values
finding, §3 "F0" and §2 "the filed facts in the payload"): the gateway drops every
non-dimensional row of an untagged income-statement or cash-flow column (a 16/24-week filer's
quarter or year-to-date column, which `edgartools` renders as a bare date), and the pilot's
downstream fix needs the filing's own facts in the payload.

- [ ] **T-159b** In `src/sec_edgar/agent.py`, look an untagged column on a duration statement
      up as a period of unknown length (the filed non-dimensional facts of the concept that end
      on the column's date, at most 371 days long, plus the instants at that date): a unique
      matching period length keeps the value, two matching lengths leave the cell alone, no
      matching fact drops it as before; a tagged column and a balance-sheet row are unchanged.
      Add `filed_facts` (the filing's own non-dimensional, numeric facts for the statements'
      dates, each with its own value) and its `_safe_filed_facts` wrapper, and return them
      additively from `get_financials` as `data["filed_facts"]` / `data["filed_facts_error"]`.
      Update the docstrings, the OpenAPI route description in `apps/sec_edgar_api.py`,
      `SPEC.md` FR-004 and `docs/modules/sec-edgar.md`. Add hermetic unit tests in
      `tests/sec_edgar/test_agent.py` with facts captured live in
      `tests/sec_edgar/fixtures/`. → `PLAN.md` Work item 16.

## Status

Closed Work items 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14 and 15 are in `CHANGELOG.md` (Work item 5 closed 2026-10-02 once
`portfolio-financial-analysis` re-verified the redeployed gateway and closed its own
`T-117`/`T-118` in its PR #104; Work item 6, `T-043`, closed 2026-10-02 in PR #48;
Work item 7, `T-044`, closed 2026-10-09; Work item 9, `T-046`, closed 2026-10-14 in PR #51;
Work item 10, `T-053`–`T-060`, closed 2026-10-16 (`T-059`; its successor, Work item 12, closed 2026-10-17);
Work item 11, `T-061`, closed 2026-10-16 in PR #62; Work item 12, `T-062`, closed 2026-10-17 after the audited real run; Work item 13, `T-063`, closed 2026-10-17 by decision, no code; Work item 14, `T-064`–`T-067` and `T-075`–`T-077`, closed 2026-10-19: `T-064` in PR #73, `T-075` in #75, `T-076` in #78, `T-065` in #79, `T-066a` in #80, `T-067` in #81, `T-066b` in #83 (design #82), `T-077` in #85 (design #84), and the `articles` label and quarantine step of the 939 rows written 2026-10-19 (PR #87)); Work item 15, `T-068`–`T-074`, closed 2026-10-21 (the sitemap cache, PRs in `PLAN.md` Work item 15; the real file at schema version 5, `G3` N = 578,113, the first real discovery 2026-10-21).

Work item 11, part 2 (`T-061b`, the Tiingo calibration file) closed 2026-10-29 in PR #102; reviewer B verified the manifest against the universe spans and closed L-42 — see `CHANGELOG.md`.

Work item 8 (`T-045`, filer CIK and period of report on filing-list rows) is open and active.

Work item 1 (CI workflow) was built and then reverted at the maintainer's
request (#35), so T-001–T-007 stay unchecked and T-010–T-014 are moot until
CI is wanted again.

## Known gaps (not scheduled; the maintainer decides)

- **438 `reject` pairs** (438 URLs; re-measured 2026-10-18 after the T-062 run) have a distinctive non-first word of the company
  name in the URL, almost all generic words (`service`, `nation`, `estate`); a handful look real (`DIS`, `SCHW`, some `LYV`, `BAC`).
  Below the T-057 hand check's resolution, so not fixed and not a Work item. They are quarantined, not deleted; `portfolio-nlp`'s
  NER check reads only `review`, so it does not see them. **T-072 (2026-10-20): all 438 are among the 323,864 sitemap rejects that keep a quarantined article, none in the never-fetched set `T-073` deletes, so the gap keeps its population and `scripts/reclass_rejects.py` still covers it.** Details: `PLAN.md` Work item 12, "Known gap". Only the maintainer promotes it to a Work item; the trigger is a hand check of a sample drawn from these 438 that finds more than 1 of 40 correct (the T-057 threshold, applied to this subset), or a `portfolio-nlp` request.
