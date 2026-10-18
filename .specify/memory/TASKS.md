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

## Work item 14 — Collector and gateway: a discovery batch above SQLite's variable limit, the database path, the `.env` rule (T-064–T-067)

Found 2026-10-18 by the T-062 step 6 check and the database validation after the gateway restart. Evidence, line numbers and the
decisions taken are in `PLAN.md` Work item 14. **Order:** `T-064`, then `T-065`, then `T-066a` (which reuses `resolve_db_path()` and its empty-value rule from `T-065`); `T-067` is independent;
`T-066b` waits for the maintainer's constitution rule 7 decision.

- [ ] **T-064** *(bug from `T-056c`)* Chunk the `(url, ticker) IN (...)` lookups in `enqueue_batch` and `_count_new_rejected`
      (`src/news_collector/storage/queue.py`, about 5,000 pairs per query) so a batch above about 16,000 pairs does not exceed
      SQLite's variable limit (32,766 on 3.40.1). Make a failed batch loud: error log, non-empty `errors`, and a non-zero exit of
      `news_collector/main.py`. First log the batch size of the failing `KLAC`/`cnbc` call to confirm the cause.
      Acceptance: (1) a test with 20,000 mixed pairs that lowers the limit (`queue._conn._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, ...)`,
      or monkeypatches the chunk size) and **fails on the unfixed code** (shown in the PR); it inserts all rows and returns the right
      `last_inserted_rejected` and `last_promotion`; (2) a test with more than 16,000 urls and fewer than 100 rejects: every `u.id` set, promotion applied;
      (3) chunked equals unchunked on a small batch; (4) a CLI test of the non-zero exit; (5) re-run `KLAC` `cnbc` on the real
      database, only with the maintainer's explicit go-ahead, after a backup copy that passes `quick_check` (`data/url_cat_backup.db` is older than the
      stray rows and the T-062 run), with `company_names` passed explicitly (T-067 is not fixed yet; without it the run repeats the 2026-10-18
      mistake), and with the two stray rows corrected first or excluded from the check; then read the new rows with the step 6 query of `docs/modules/news-collector.md` (full-name URL is `review`/`loose_name`),
      which closes the data half of `T-062` step 6. No schema change (constitution #10 N/A).
- [ ] **T-065** *(configuration)* The collector API opens the wrong database by default. Change: (a) `default=None` (typed `str | None`,
      description "default: $DATABASE_URL") for the five `db_path` defaults in `api/routers/discovery.py` (3) and `api/schemas.py` (2);
      (b) `data_mining.resolve_db_path(value=None)` returns the value, else `$DATABASE_URL`, else the default, and treats an empty string as
      unset (`.env.example` ships `DATABASE_URL=`); (c) the seven sites that build a `URLQueue` call it (`routers/discovery.py` 4 sites,
      `routers/_common.py`, `api/deps.py` `get_queue`, `news_collector/main.py`); (d) `URLQueue.__init__` raises `ValueError` on `None` or
      an empty path; (e) the literal fallback in `apps/news_crawler_api.py` and `cli/news_crawler_cli.py` (`DEFAULT_DB`) uses it too.
      Acceptance: each handler follows `$DATABASE_URL`; a request without `db_path` creates no file named `None` or `data/urls.db`;
      `URLQueue(None)` and `URLQueue("")` raise; a plain `/collector/discovery/stats` reports the real counts; the `db_path` workaround
      is removed from `docs/modules/news-collector.md`.
- [ ] **T-066a** *(after `T-065`; configuration)* Start-up check and visibility, in the apps that open `urls.db` (`apps/gateway.py`,
      `news_collector_api.py`, `news_crawler_api.py`; not `pricing_api.py` or `sec_edgar_api.py`, which never touch it): log the effective
      `DATABASE_URL` and its source (`.env`, inherited, default); warn when an inherited value differs from `.env`, naming the variable and both
      values; refuse to start when the database file is missing or empty, unless `--allow-new-db` or `ALLOW_NEW_DB=1` (the collector creates
      the file on first use). Precedence (maintainer, 2026-10-18, option B): the inherited environment stays above `.env`. Acceptance: tests of
      the warning, the refusal and the flag; a plain gateway start on this machine serves the real counts or stops with the message;
      `.env.example` lists `ALLOW_NEW_DB` (unset by default).
- [ ] **T-066b** *(blocked on the maintainer's constitution rule 7 decision)* Rule 7 says each service calls `load_dotenv()` itself,
      "deliberate, not debt", with no shared bootstrap module. The maintainer chooses (a) amend rule 7 to name a shared `data_mining.env.load_env()`,
      the AST test and the new variables, or (b) keep each service's own call and enforce the same behavior with the AST test plus a shared
      behavior test. Then, for all five apps and four CLIs: an explicit top-level call first (repo-root `.env` lookup, `override=False`,
      `config.py` unchanged in behavior). Acceptance: tests of precedence, root lookup and a missing file; an AST test that fails when a file
      under `apps/` or `cli/` has no top-level call before its first project import that reads the environment (`news_collector…`, `data_mining…`,
      `pricing…`, `sec_edgar…`; an import side effect does not count; the test globs both directories); module docs state the rule.
      `scripts/` is out of scope (they take `--db`).
- [ ] **T-067** *(collector API)* `resolve_companies` (`api/routers/_common.py`) does `name = names[i].strip() if i < len(names) else ticker`, so a
      request without `company_names` stores the ticker as the company and the matcher rejects a `kla-corporation` URL. Change: reject a
      `company_names` list whose length differs from `tickers` (422 that says it is positional); when names are omitted, look each ticker up by
      **exact symbol** in `fetch_sp500_companies` (one Wikipedia request, 502 on failure as today). Only for a ticker the current list
      does not contain, look it up by exact `Symbol` in `data_mining.universe_history.query_as_of(start_date)` (never `resolve_as_of`, whose
      partial name match could tag the wrong company), so a missing or un-backfilled `data/universe.db` (it raises `ValueError`) cannot break
      requests for current tickers. `start_date` is the request's, else `config.default_start_date()`. A ticker found in neither, or a
      `start_date` outside the store's coverage, gets a clear 422. Known limit, accepted and documented: a ticker added after `start_date` and removed before
      today is in neither, so it is refused (the caller passes `company_names` for it). The leaver lookup is the maintainer's to confirm.
      Acceptance (hermetic): omitted, mismatched and explicit cases; a leaver ticker; an unknown ticker that is a substring of a company name; an
      out-of-coverage date; a current ticker still resolving when the store is missing. The two stray rows (ids 495519, 495520; find them with
      `SELECT id, url, ticker, company FROM discovered_urls WHERE company = ticker AND id >= 495519`, expect 2) are corrected or removed with the maintainer's approval.

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
