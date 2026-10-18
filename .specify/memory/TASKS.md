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

Found 2026-10-18 by the T-062 step 6 check on the real `urls.db`: `POST /collector/discover/cnbc/` for `KLAC` returned
`errors: ["too many SQL variables"]` and inserted nothing (`cnbc.com` holds 465,044 of the 481,332 pairs, counted before the two stray rows of T-067).

- [ ] **T-064** *(Work item 14; bug in code from `T-056c`, not from `T-062`)* `enqueue_batch` and `_count_new_rejected` in
      `src/news_collector/storage/queue.py` build one `(url, ticker) IN (...)` with two bound variables per pair, so a batch
      above about 16,000 pairs exceeds SQLite's limit (32,766 on 3.40.1) and the batch is lost or half-done, with the error only in
      the response: `_count_new_rejected` runs before the insert, but the id lookup runs after the insert and commit, so a batch
      with few rejects stores its rows, then fails with no ids set and no promotion applied (a retry is needed). Chunk both lookups (about 5,000 pairs per query), keep the results identical, and make a failure of a
      batch loud (log at error level, non-empty `errors`, and `news_collector/main.py`, which today prints the first five `stats.errors` and exits 0, exits non-zero when `errors` is non-empty, with a test). Confirm the cause first:
      log the batch size of the failing `KLAC`/`cnbc` call. Acceptance: a test with a batch above the limit (20,000 pairs,
      rejected and accepted mixed) that lowers the limit on its own connection (`queue._conn._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, ...)`:
      `URLQueue._conn` is a `portfolio_common.db.Database` whose private `_conn` is the raw `sqlite3.Connection`; the repo runs Python 3.12) or,
      if that private access is refused in review, monkeypatches the chunk size, so it does not depend on the SQLite build, **and fails on the unfixed code** (run it
      before the fix and say so in the PR); it inserts all rows, returns the right
      `last_inserted_rejected` and `last_promotion`; a second test with more than 16,000 urls and fewer than 100 rejects (the
      path the KLAC run did not take) asserts that every `u.id` is set and the promotion is applied; and a test that the chunked result equals the unchunked one on a small batch;
      then re-run the `KLAC` `cnbc` discovery on the real database and read the new rows with the step 6 query of
      `docs/modules/news-collector.md` (a new full-name URL is `review`/`loose_name`), which also closes the data half of the
      `T-062` step 6 check. Constitution AI behavior #10 does not apply (no schema change).
- [ ] **T-065** *(Work item 14; configuration, found 2026-10-18 while validating the database after the gateway restart)*
      The collector API reads the wrong database by default. `api/deps.py` `get_queue()` already falls back to
      `$DATABASE_URL`, but the routers (`src/news_collector/api/routers/discovery.py`, three `Query` defaults) and the request
      schemas (`api/schemas.py`, two fields) pass the literal `data/urls.db` (an empty file in the repo) as `db_path`, so the
      fallback is never reached and `/collector/discovery/stats` showed `total: 0` while the real database held 481,332 pairs.
      The routers do not go through `get_queue`: seven call sites build `URLQueue` themselves
      (`routers/discovery.py` lines 46, 82, 101 and 150, and `routers/_common.py:152`; `get_queue` is the sixth and `main.py:72`
      the seventh), so `default=None` alone would reach `URLQueue(None)`, which stores the path `"None"` and creates a file with that name.
      Fix: `default=None` in the five `Query`/field places (typed `str | None`, description 'default: $DATABASE_URL'), one
      helper `resolve_db_path(value=None)` in `data_mining/db.py` that every one of the seven sites calls with the request value, and
      `URLQueue.__init__` raising `ValueError` on `None` or an empty path (so a missed site fails loudly instead of creating a file). The same literal fallback is in `src/news_collector/main.py` (CLI `--db`), `apps/news_crawler_api.py` (`DEFAULT_DB`)
      and `cli/news_crawler_cli.py` (`DEFAULT_DB`); move them to `resolve_db_path()` too. An empty `DATABASE_URL`
      (`.env.example` ships `DATABASE_URL=`) counts as unset, decided once inside `resolve_db_path()` (`data_mining/db.py`), which
      `T-066` reuses. Acceptance: a test that the defaults follow `$DATABASE_URL` for each handler, a test that a request without `db_path` creates no file named `None` or `data/urls.db`, a test that `URLQueue(None)` and `URLQueue("")` raise, and a plain
      `/collector/discovery/stats` reports the real counts, and the `db_path` workaround is removed from
      `docs/modules/news-collector.md`. Until fixed, pass `db_path` in the request (documented there). The `.env` precedence part is `T-066`.

- [ ] **T-066** *(Work item 14; configuration rule, asked by the maintainer 2026-10-18: reading `.env` is mandatory for every service)*
      Every entry point, the five apps (`apps/gateway.py`, `news_collector_api.py`, `news_crawler_api.py`, `pricing_api.py`,
      `sec_edgar_api.py`) and the four CLIs (`cli/news_collector_cli.py`, `news_crawler_cli.py`, `pricing_cli.py`,
      `sec_edgar_cli.py`), must read the repository's `.env` the same way, explicitly, before anything reads the environment. Today the reading is
      implicit and scattered: `apps/gateway.py` and `cli/news_collector_cli.py` have no `load_dotenv()` call of their own and get
      `.env` only as an import side effect (the gateway through the sub-apps it imports, the CLI through
      `src/news_collector/config.py`); the other apps and CLIs each call it with the default `override=False`, and `config.py`
      does it again with `override=False` on purpose (a real variable such as `DISCOVERY_END_DATE` wins), so an inherited value
      always beats `.env` and nothing says so. A variable inherited from the container beat `.env` on
      2026-10-18: `/etc/environment` set `DATABASE_URL="../data/urls.db"`, the gateway answered `/crawler/status` with 500 and
      nothing said why. Do: one function (for example `data_mining.env.load_env()`) that every app and CLI calls first,
      finds `.env` from the repo root rather than the working directory, applies the precedence rule (decided by the maintainer 2026-10-18, below), treats an empty `DATABASE_URL` (as in `.env.example`) as unset (through `resolve_db_path()`, `T-065`), logs the effective
      `DATABASE_URL` and where it came from (`.env`, inherited, default), warns when an inherited value differs from `.env` (naming the variable and both values, for example
      `DATABASE_URL: inherited "../data/urls.db" differs from .env "/workspaces/thesis/data/urls.db"; using the inherited one`),
      and, in the apps that open `urls.db` (`apps/gateway.py`, `news_collector_api.py`, `news_crawler_api.py`; not `pricing_api.py` or
      `sec_edgar_api.py`, which never touch it), refuses to start when the database file is missing or empty, unless the
      collector is started with an explicit `--allow-new-db` (or `ALLOW_NEW_DB=1`) because it creates the file on first use
      (`queue.initialize()`); the other entry points only call `load_env`, log and warn. **Precedence, decided by the maintainer 2026-10-18 (option B):** the inherited environment
      stays above `.env` (`override=False`, so a shell or container can still override a value for one run, as
      `config.py` documents for `DISCOVERY_END_DATE`), and a conflict is never silent: the warning above plus the start-up
      refusal on a missing or empty database. `config.py` keeps `override=False`; it moves to the shared `load_env`
      with no change of behavior. **Blocked on a constitution amendment (maintainer):** rule 7 says each service calls `load_dotenv()` itself,
      'deliberate, not debt', with 'no shared bootstrap module to import first'. A shared `load_env()` reverses that reason, so the
      maintainer decides first between (a) amend rule 7 to name `load_env()`, the AST test and the new variables, or (b) keep each
      service's own `load_dotenv()` call and enforce the same behavior with the AST test plus a shared behavior test; the build
      starts after that decision. Acceptance: tests of `load_env` (precedence, root lookup, warning, missing file, empty value) and of the start-up check;
      **an AST test that fails when a file under `apps/` or `cli/` has no top-level `load_env()` call before its first project import that
      reads the environment (`news_collector…`, `data_mining…`, `pricing…`, `sec_edgar…`)** (an import side effect does not count; the test globs both
      directories, so a new entry point fails until it calls it; so the rule stays mandatory); a plain start of
      the gateway on this machine serves the real counts or stops with the message; `.env.example` lists `ALLOW_NEW_DB` (default unset: the collector refuses a missing database; `1` lets it create one) and the module docs
      state the rule. Scripts under `scripts/` that read `DATABASE_URL` take `--db`, so they are out of scope unless the
      maintainer adds them.

- [ ] **T-067** *(Work item 14; collector API, found 2026-10-18)*
      `POST /collector/discover/{domain}/` with `tickers` and no `company_names` stores the ticker as the company
      (`company = 'KLAC'`), and the matcher then classes a URL that spells `kla-corporation` as `reject`. The test run of
      2026-10-18 left two such rows (ids 495519 and 495520) in the real `urls.db` (find them with
      `SELECT id, url, ticker, company FROM discovered_urls WHERE company = ticker AND id >= 495519`, expect 2 rows). The defect is one line in `resolve_companies`
      (`src/news_collector/api/routers/_common.py`): `name = names[i].strip() if i < len(names) else ticker`. Resolve the name by
      looking the ticker up in the list `news_collector.sp500.fetch_sp500_companies` already returns when `tickers` is omitted
      (one Wikipedia request per call, the 502 on failure as today; a ticker not in the list gets a 422), or refuse the request. **Recommended, for the maintainer to confirm:** use the current list first, then the point-in-time universe
      (`data_mining.universe_history.resolve_as_of`, for a leaver ticker; the pipeline tracks leavers, Work item 11), and only a ticker found in neither gets the 422, so
      the check does not block leaver discovery; also reject a `company_names`
      list whose length differs from `tickers` (it is positional; a dict gives a validation error that does not say so).
      Acceptance: tests for the omitted, the mismatched and the explicit cases; the two stray rows are corrected or removed
      with the maintainer's approval.

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
