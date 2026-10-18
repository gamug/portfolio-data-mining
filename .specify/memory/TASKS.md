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

## Work item 14 — Collector and gateway: a discovery batch above SQLite's variable limit, the database path, the `.env` rule (T-064–T-067, T-075, T-076)

Found 2026-10-18 by the T-062 step 6 check and the database validation after the gateway restart. Evidence, line numbers and the
decisions taken are in `PLAN.md` Work item 14. **Order:** `T-064`, then `T-065`, then `T-066a` (which reuses `resolve_db_path()` and its empty-value rule from `T-065`); `T-067` is independent;
`T-066b` is done in PR #83 (design in PR #82); the open choice (11) on `news_collector/config.py` stays with the maintainer.

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
- [x] **T-066b** *(code done 2026-10-18, PR #83: `data_mining/env.py`, nine entry points, `tests/data_mining/test_env.py`, `test_env_entrypoints.py`, `tests/conftest.py`; choice (11) still open; design agreed with the maintainer 2026-10-18; rule 7 amended in the same docs PR, code in the next PR)* `.env` wins over the
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
      (11) **`news_collector/config.py`** *(open choice, pending the maintainer's answer)*: its import-time `load_dotenv(override=False)` is **removed**: library code reads `os.environ` only and the entry points load `.env`
      (`news_collector/main.py` is itself an entry point and calls `load_env()`). A plain `import news_collector.config` in a test or a notebook sees the system environment only. Until the maintainer answers, the code PR does not touch `config.py`'s own read; if the answer is to keep it, it calls `load_env()` instead.
      **Known consequences (accepted with decision 2, no escape hatch):** a one-off value on the command line loses to `.env`: the documented
      `DATABASE_URL=/workspaces/thesis/data/urls.db uv run apps/gateway.py`, `DISCOVERY_END_DATE=...`, `ALLOW_NEW_DB=1` stop working when `.env` sets the same variable. A blank `.env` line cannot switch off an inherited variable (decision 5): a system `HTTP_PROXY` or `HTTPS_PROXY` stays on; unset it in the system or the shell. A one-off run is made by editing `.env`
      or with a flag applied after `load_env()` (`--db`, `--allow-new-db`, a request's `db_path`). The module docs and `.env.example` say so in the code PR.
      Acceptance: tests of precedence (`.env` over inherited), fallback (missing file, absent variable, blank value), root lookup from another working directory,
      the notice (values printed only for the allow-list; a variable not on it is logged by name, with a value that must not appear in the log), the AST test (fails on a file without the call), a flag applied after `load_env()` still wins (`--allow-new-db` takes effect with `ALLOW_NEW_DB` absent from `.env`, and a `.env` value does not undo the flag), and the real gateway start on this machine with the wrong inherited value
      using the `.env` database.
- [x] **T-067** *(collector API; done 2026-10-18, PR #81: `news_collector/companies.py` (shared by `resolve_companies` and the CLI's `--tickers`), tests in `tests/news_collector/test_resolve_companies.py` and `test_main_exit_code.py`; the leaver lookup is the maintainer's to confirm)* `resolve_companies` (`api/routers/_common.py`) does `name = names[i].strip() if i < len(names) else ticker`, so a
      request without `company_names` stores the ticker as the company and the matcher rejects a `kla-corporation` URL. Change: reject a
      `company_names` list whose length differs from `tickers` (422 that says it is positional); when names are omitted, look each ticker up by
      **exact symbol** in `fetch_sp500_companies` (one Wikipedia request, 502 on failure as today). Only for a ticker the current list
      does not contain, look it up by exact `Symbol` in `data_mining.universe_history.query_as_of(start_date)` (never `resolve_as_of`, whose
      partial name match could tag the wrong company), so a missing or un-backfilled `data/universe.db` (it raises `ValueError`) cannot break
      requests for current tickers. `start_date` is the request's, else `config.default_start_date()`. A ticker found in neither, or a
      `start_date` outside the store's coverage, gets a clear 422. Known limit, accepted and documented: a ticker added after `start_date` and removed before
      today is in neither, so it is refused (the caller passes `company_names` for it). The leaver lookup is the maintainer's to confirm.
      Acceptance (hermetic): omitted, mismatched and explicit cases; a leaver ticker; an unknown ticker that is a substring of a company name; an
      out-of-coverage date; a current ticker still resolving when the store is missing. The two stray rows (ids 495519, 495520): on 2026-10-18 the real file already reads `company = 'KLA Corporation'` for both, corrected by hand by the maintainer
      after the 13:14 backup (the 13:14 backup `urls.pre-t064.db` still has them as strays). Acceptance check: `SELECT id, url, ticker, company FROM discovered_urls WHERE company = ticker AND id >= 495519` returns 0 (already true on 2026-10-18, so it guards against a regression, it no longer shows a fix). Re-run read-only after the code change: 0 rows.
      The `id >= 495519` bound is why 939 older rows with `company = ticker` are not counted (measured 2026-10-18: `IBM` 892, `MSCI` 44, `AAPL` 3, all from August 2026). `IBM` and `MSCI` may be correct (the company is known by its ticker);
      the 3 `AAPL` rows (ids 484806, 484810, 484811, all `accept`) are **not investigated and out of scope** here; maintainer decision 2026-10-18: widening the scope waits until the bug tasks of Work item 14 (`T-065`, `T-066a`, `T-067`, `T-076`) are closed or dropped, then the 939 rows are looked at (no new task now; `PLAN.md` Work item 14 carries the reminder).
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

## Work item 15 — Collector: the sitemap once per domain, not once per ticker (T-068–T-074)

Found 2026-10-18 by the `T-064` step 5 run: one ticker's cnbc discovery stored 578,117 rejected rows (+243 MB). Evidence, the decision (option B,
the maintainer) and the guardrails are in `PLAN.md` Work item 15. **Gates (maintainer approvals): G1** the design numbers of `T-068`; **G2** the
DDL, migration and rollback of `T-069` (constitution #10); **G3** the real-data step of `T-073`. Work on a **copy** of `urls.db` until G3.
The criteria of each gate were decided 2026-10-18 (`PLAN.md` Work item 15). Order: `T-068` → G1 → `T-069` → G2 → `T-070` → `T-071` → `T-072` → `T-073` (G3) → `T-074`. Each task's PR states what was run on which copy.

- [ ] **T-068** *(read-only measurement and design; ends at G1)* Measure, on a copy of the real database and a fresh crawl of the sitemaps (no
      write to the real file): for `cnbc.com`, `ft.com` and `nasdaq.com` the number of entries, the share with a title and with a `lastmod`, the
      crawl time, and why `ft`/`nasdaq` store no sitemap rows today; the cost of matching one ticker against the whole cnbc sitemap, and the size
      of a cache row with its indexes. Then compare today's full scan with a **candidate preselection** (the design must name it, for example a token
      index of the URL slug) on **all 503 tickers** (offline, against the crawled file; no time limit) with the short and ordinary-word ones (`ON`, `A`, `IT`, `ARE`, `TTD`, `KLAC`, `NWS`, `NWSA`) reported one by one: report, per ticker, the `accept`/`review` pairs of each path and the recall, and state why the preselection cannot skip a pair the matcher would accept (a superset argument by construction, not only the sample); if that cannot be argued, the full scan stays. A fixture with a `news:title` entry shows that the title evidence is kept. **Result: a design note** (table columns, indexes, the
      preselection with its measured recall, the refresh rule **including what happens to an entry that leaves the sitemap: replace the entries on a complete crawl, or upsert and expire by "last seen", and what `T-072` does with an expired entry**, the estimated size and the time of a 503-ticker run, re-measured with the fixed sitemap date parser (PR #75)) in `PLAN.md`. A preselection that
      loses a pair is rejected (exact equality, no tolerance), or the full scan stays; there is no time limit for the 503-ticker match. **G1:** the maintainer approves the numbers and the design before `T-069`.
- [ ] **T-069** *(schema and migration; ends at G2; constitution AI behavior #10)* Additive tables from the `T-068` design in `data_mining/schema.py`
      (canonical DDL plus an idempotent step in `run_migrations()`, no change to `discovered_urls` or `articles`): the cache of the entries of a domain's
      sitemap (url, **lastmod** (the pair's `pub_date`), **title** when the sitemap carries one (`news:title`; the matcher uses it as evidence, so the cache must not drop it), the keys the preselection needs, first and last seen) and one row per domain for its refresh (crawled at, the date range it covers,
      the entry count, status). **Criteria decided:** the cache covers any domain with `sitemap_roots` (a `domain` column), lives in `urls.db`, and is stale when the range is not covered or the last crawl is older than 7 days (a flag forces it). **Before the code is merged, G2:** the maintainer approves the DDL, the migration and the **rollback** (dropping the two
      tables restores the previous schema; the document says so and a test does it). Acceptance: the migration is idempotent on an old and on an empty database;
      `portfolio-nlp`'s read path is unaffected (a test opens the database as the extractor and the reader do); the old `apply_schema` callers still work.
- [ ] **T-070** *(code; after G2)* The sitemap strategy fills and refreshes the cache **once per domain per run**: a per-domain guard so concurrent
      tickers wait for the one crawl; a refresh only when the cache does not cover the run's date range or is older than the rule of `T-068`; a failed or **partial** crawl
      leaves the previous cache valid (write the new entries in one transaction). Today `_crawl_sitemaps` swallows a failed child fetch or parse and returns what it got, so this task first makes the crawl report whether every root and child was fetched and parsed (the same change as the open failed-sitemap-fetch task of Work item 14, if that is merged; otherwise it is done here), and a cache is replaced only by a complete crawl; the entries keep the `lastmod` filter semantics of today. Acceptance: tests
      for two concurrent tickers causing one crawl, a failed crawl, and one failed child among good ones, keeping the old cache, a wider date range triggering a refresh, a narrower one not; the age rule (7 days) and the force flag; the log says
      what was refreshed and how many entries. `T-076` made `SitemapRSSStrategy.failures` an instance list reset per `discover`; a once-per-domain fetch must carry the failure list to each ticker's pair (so the pair is still reported and not checkpointed).
- [ ] **T-071** *(code)* Per-ticker matching from the cache: the strategy selects the candidates (as designed in `T-068`), classes them with the existing
      matcher, and returns only `accept` and `review` pairs; the number of `reject` pairs that were **not stored** is counted and reported (a new field next to
      `total_rejected`, which stays for the `ddg` rejects and the rows already stored; the CLI summary, the orchestrator log and **both** API responses are updated: `RunAllResponse` and the per-domain `DiscoverResponse` returned by `/cnbc/`, `/ft/` and the other domain routes, which carries no reject count today; a test per response).
      `ddg`, `rss`, wayback and `api` behavior is unchanged. **Acceptance: the equivalence test of guardrail 2** on a fixture corpus (the `accept`/`review`
      pairs, their class, reason and `pub_date`, equal to the full scan for the tickers of `T-068`), a test that a batch of a 578,000-entry cache (a stress size, the real in-range size is about 121,000) writes no reject row, a test that the pair's `pub_date` is the cached `lastmod`, a test of
      the stats, a `tqdm` progress bar in the CLI over the tickers (and entries) being matched, with a test that it does not break a non-tty run, and, on a copy, a 10-ticker cnbc run whose row delta and time are reported against a **fresh baseline measured on the same copy with the current full scan and the fixed sitemap date parser (PR #75)** (the 2026-10-18 figure, 578,117 rows for one ticker, was taken before that fix and is history, not the baseline).
      `SPEC.md` FR-014 and the `T-056c` wording of the module doc say that sitemap rejects are no longer stored.
- [ ] **T-072** *(code)* Re-class from the cache with no network: a command (CLI and the matching API route) that classes the cached entries again for the
      given tickers (or all) and enqueues the pairs whose class is now `accept`/`review`, promote-only, through the same `enqueue_batch` path. Acceptance: idempotent
      (a second run inserts and promotes nothing), a test that a matcher change moves a pair from nothing to `review`, and `scripts/reclass_rejects.py`'s docstring and the
      known gap in `TASKS.md` say what they cover now (stored `ddg` rejects, the 323,864 sitemap rejects that keep a quarantined article, and the rows of before this item; the script is **not retired**), and report how many of the 438 known-gap pairs of Work item 12 sit in the set `T-073` deletes (never fetched, no article) and how many in the kept 323,864; a pair in the deleted set must be re-derivable from the cache, or the gap loses its population and what replaces them. Each of the four scripts that read `reject` rows is listed with what it still covers after `T-071` and `T-073`: `reclass_rejects.py` (the stored rejects that remain: `ddg`, uncovered domains, the kept sample rows), `retag_urls.py` (legacy rows with no `match_reason`, unaffected, but its `--hand-check` sheet draws 40 `reject` pairs), `score_ticker_rules.py` (reads the labeled sample files, so the kept rows matter, and `--diff` scans the remaining corpus) and `ticker_validation_sample.py` (its strata include `(reject, sitemap)`: say what it draws when that stratum is empty or tiny, for example the pairs classed `reject` from the cache by the current `classify`, and a test of that behavior instead of an assumption).
- [ ] **T-073** *(data; ends at G3; maintainer approval, backup, gateway and crawler stopped)* Reclaim the sitemap rejects already stored **that were never fetched** (today: 901,977 `sitemap` rejects, all `cnbc.com`, of which **578,113 are `skipped` with no `articles` row**, the `KLAC` run, and **323,864 have a quarantined article** (statuses `ok` 315,456, `thin_content` 8,144, `failed` 264) and are **not deleted**: `articles.id` is `discovered_urls.id`, the bodies are kept for `portfolio-nlp` and the release path, and `reclass_rejects.py` still works on them; measured read-only 2026-10-18; the 1,115 `ddg` rejects stay). **Before deleting:** fill the cache for cnbc, prove on the copy that `T-072` finds again every **in-range** `accept`/`review`
      pair that is stored today for a sample of tickers (pass condition: **0 in-range pairs missing** in the sample; the cache holds only entries inside the configured range, and about 65% of the stored `accept`/`review` sitemap rows are out of range after the evidence of PR #75, so those are counted and listed by year, they stay stored and are never part of the delete; the stored `accept`/`review` rows are not touched), and list what the `--revert` file of the `T-062` run needs (its rows are
      promoted ones and stay). **Scope decided (the three narrowings below were confirmed by the maintainer 2026-10-18; G3 still approves the evidence):** only source `sitemap`/`rss`, narrowed to never-fetched rows (`status='skipped'`, no article), and to rows whose domain has a filled cache (today `cnbc.com` only: 901,977 `sitemap` rows, **of which 578,113 qualify**, 0 `rss`; `ft` and `nasdaq` have none), so a Wayback or RSS reject of a domain the cache does not cover is never deleted. The PR lists how many of those rows are **not** in the cache (older than the range, so never re-derivable) and why; a stored reject that the cache cannot re-derive is deleted only if it is outside the configured date range or its URL is no longer in the sitemap, and the count of each is stated at G3. **Kept on purpose:** every row whose id is in a committed hand-labeled sample (`data/ticker_*_sample.csv` and `*_manual.csv`: 593 distinct ids, **84 of them are `sitemap` rejects today**, none of them `skipped` without an article, so today the exclusion removes nothing and stays as a safeguard, measured read-only 2026-10-18), because `score_ticker_rules.py`, `ticker_validation_sample.py` (`check_sheet`) and the hand-check refuse or mis-score a sheet whose ids are gone; the PR recomputes that count on the day. **At G3** the maintainer approves the evidence, then: a backup that passes `quick_check`, a row-count fingerprint, one transaction that deletes only
      `match_class='reject' AND status='skipped' AND NOT EXISTS (SELECT 1 FROM articles a WHERE a.id = discovered_urls.id) AND source IN ('sitemap','rss') AND domain IN (<the domains with a filled cache, printed in the PR>) AND id NOT IN (<the ids of the committed hand-labeled samples>)`, the fingerprint checked after, and `VACUUM INTO` a new file with the disk check (free space of the file size) and the swap, the old file kept as the backup until the maintainer deletes it.
      Acceptance: counts before and after in the PR, `quick_check` ok, `articles` unchanged in count and with **no orphan id** (`articles.id` with no `discovered_urls` row: 0 before and after), the `accept`/`review` rows unchanged, and the file size reported before and after: **expected shrink about 243 MB** (578,113 rows at about 420 bytes), the size of the `KLAC` run, because the rest of the file is article bodies, which stay.
- [ ] **T-074** *(close-out)* `docs/modules/news-collector.md` (including its "Auditing ticker tagging" and re-class sections, which describe a `reject` population that shrinks from 903,092 rows to about 1,115 plus the kept sample rows; the class counts of `GET /discovery/stats` and the docs drop **by design**, not as data loss), `SPEC.md`, the architecture artifacts content-only (constitution #11), the interim caution of this item removed, and the item's
      tasks moved to `CHANGELOG.md` (#12) with the test count of the day.

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
