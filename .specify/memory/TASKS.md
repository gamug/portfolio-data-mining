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

## Work item 14 — Collector: a discovery batch above SQLite's variable limit fails (T-064)

Found 2026-10-18 by the T-062 step 6 check on the real `urls.db`: `POST /collector/discover/cnbc/` for `KLAC` returned
`errors: ["too many SQL variables"]` and inserted nothing (`cnbc.com` holds 465,044 of the 481,332 pairs).

- [ ] **T-064** *(Work item 14; bug in code from `T-056c`, not from `T-062`)* `enqueue_batch` and `_count_new_rejected` in
      `src/news_collector/storage/queue.py` build one `(url, ticker) IN (...)` with two bound variables per pair, so a batch
      above about 16,000 pairs exceeds SQLite's limit (32,766 on 3.40.1) and the whole batch is lost, with the error only in
      the response. Chunk both lookups (about 5,000 pairs per query), keep the results identical, and make a failure of a
      batch loud (log at error level, non-empty `errors`, and the CLI exit code non-zero if it has one). Confirm the cause first:
      log the batch size of the failing `KLAC`/`cnbc` call. Acceptance: a test with a batch above the limit (20,000 pairs,
      rejected and accepted mixed, so the test does not depend on the SQLite build's limit) inserts all rows, returns the right
      `last_inserted_rejected` and `last_promotion`, and a test that the chunked result equals the unchunked one on a small batch;
      then re-run the `KLAC` `cnbc` discovery on the real database and read the new rows with the step 6 query of
      `docs/modules/news-collector.md` (a new full-name URL is `review`/`loose_name`), which also closes the data half of the
      `T-062` step 6 check. Constitution AI behavior #10 does not apply (no schema change).
- [ ] **T-065** *(Work item 14; configuration, found 2026-10-18 while validating the database after the gateway restart)*
      The gateway and the apps read the wrong database. `/etc/environment` sets `DATABASE_URL="../data/urls.db"` for the
      whole container, `load_dotenv()` does not override a variable that is already set, so the `.env` value is ignored (the
      crawler's `/status` answered 500 because `../data/urls.db` does not exist), and the collector API's `db_path` defaults
      are the literal `data/urls.db` (`src/news_collector/api/routers/discovery.py`, `api/schemas.py`), which is an empty
      file in the repo, so `/collector/discovery/stats` showed `total: 0`. Until fixed, start the gateway as
      `DATABASE_URL=/workspaces/thesis/data/urls.db uv run apps/gateway.py` (documented in `docs/modules/news-collector.md`;
      this does not cover the collector's request defaults, pass `db_path` there). Fix: `load_dotenv(override=True)` in the
      apps (decide with the maintainer: a deliberate `.env` should beat an inherited variable), and make the collector API
      defaults come from `data_mining.resolve_db_path()` like the crawler's. Acceptance: a test that the defaults follow
      `$DATABASE_URL`, and with the gateway started plainly `/collector/discovery/stats` and `/crawler/status` report the
      real counts (481,332 pairs on 2026-10-18).

## Status

Closed Work items 3, 4, 5, 6, 7, 9, 10, 11, 12 and 13 are in `CHANGELOG.md` (Work item 5 closed 2026-10-02 once
`portfolio-financial-analysis` re-verified the redeployed gateway and closed its own
`T-117`/`T-118` in its PR #104; Work item 6, `T-043`, closed 2026-10-02 in PR #48;
Work item 7, `T-044`, closed 2026-10-09; Work item 9, `T-046`, closed 2026-10-14 in PR #51;
Work item 10, `T-053`–`T-060`, closed 2026-10-16 (`T-059`; its successor, Work item 12, closed 2026-10-17);
Work item 11, `T-061`, closed 2026-10-16 in PR #62; Work item 12, `T-062`, closed 2026-10-17 after the audited real run; Work item 13, `T-063`, closed 2026-10-17 by decision, no code).

Work item 8 (`T-045`, filer CIK and period of report on filing-list rows) is open and active.

Work item 1 (CI workflow) was built and then reverted at the maintainer's
request (#35), so T-001–T-007 stay unchecked and T-010–T-014 are moot until
CI is wanted again.

## Known gaps (not scheduled; the maintainer decides)

- **438 `reject` pairs** (438 URLs; re-measured 2026-10-18 after the T-062 run) have a distinctive non-first word of the company
  name in the URL, almost all generic words (`service`, `nation`, `estate`); a handful look real (`DIS`, `SCHW`, some `LYV`, `BAC`).
  Below the T-057 hand check's resolution, so not fixed and not a Work item. They are quarantined, not deleted; `portfolio-nlp`'s
  NER check reads only `review`, so it does not see them. Details: `PLAN.md` Work item 12, "Known gap". Only the maintainer promotes it to a Work item; the trigger is a hand check of a sample drawn from these 438 that finds more than 1 of 40 correct (the T-057 threshold, applied to this subset), or a `portfolio-nlp` request.
