# Project Constitution

Governing principles for `portfolio-data-mining` under a spec-driven ("spec
coding") workflow: specs and plans are written before implementation, and
this document is the fixed reference they must not contradict. A spec or
plan that conflicts with a rule below must change the rule here first (see
Governance) rather than override it silently.

## Technological stock

`portfolio-data-mining` is a headless Python acquisition layer — four
independently-runnable FastAPI services plus one shared in-repo library;
there is no ML/inference component and no UI. Every rule below assumes the
stack actually pinned in `pyproject.toml`.

1. **Runtime**: Python `>=3.12,<3.13`, dependency-managed with `uv`
   (lockfile `uv.lock`, version pinned via `.python-version`). Do not add a
   second package manager (pip/poetry/conda) — all installs go through
   `uv sync` / `uv add`.
2. **Web/service layer**: FastAPI (`==0.141.1`) + `uvicorn[standard]`
   (`==0.52.1`) for all four `apps/*_api.py` services; `httpx[http2]` for
   outbound calls. Pin exact versions for FastAPI/uvicorn/ruff (reproducible
   CI-equivalent local runs); range-pin libraries that are
   additive/stable (`pandas`, `numpy`, `tqdm`, `requests`).
3. **Per-service acquisition libraries** are scoped to the one service that
   needs them, not shared: `news_collector` (`ddgs`, `yfinance`, `tenacity`,
   `feedparser`, `lxml`, `pyyaml`, `rich`); `extractor` (`beautifulsoup4`,
   `trafilatura`, `langdetect`); `pricing` (`finnhub-python==2.4.29`);
   `sec_edgar` (`edgartools==5.44.1`, `defusedxml`). A new acquisition
   source is added to the one service's dependency block, not to the shared
   top-level group.
4. **Storage**: SQLite, accessed exclusively through
   `portfolio_common.db.Database` and its `Dialect` seam (`in_clause()` /
   `Allowlist` for dynamic SQL; `create_schema` / `table_columns` /
   `ensure_columns` / `schema_version` for schema work; `conn.dialect.upsert`
   /`.insert_or_ignore` for writes) — git-tag-pinned (`portfolio-common @
   tag v1.2.1`) rather than a floating version, so a DB-engine-contract
   change is an explicit, reviewed re-pin
   (`docs/portfolio-common-v1.2-engine-agnostic.md`). No raw `import
   sqlite3` in non-test `src/`/`apps/`/`cli/` code — the one remaining raw
   `PRAGMA foreign_keys = ON` (`extractor.db.enable_foreign_keys`) is
   documented as the deliberate exception, for test fixtures that bypass
   `connect()`. Chosen for zero-ops single-file deployment at this corpus
   scale; a different engine is a `portfolio-common` `Dialect`
   implementation plus a re-pin here, not a rewrite of this repo's stage
   logic.
5. **Third-party data-provider terms take the place licensing normally
   would.** This repo has no ML model checkpoints to license — its
   equivalent risk is API terms-of-service and fair-access compliance:
   Finnhub's rate limits, SEC EDGAR's requirement to self-identify via a
   real `NAME`/`EMAIL` user agent (`edgartools`, `.env.example`), and
   scraping Wikipedia's S&P 500 tables politely (cached in-process, not
   re-fetched per request). Flag any new external source's ToS/rate-limit
   posture in the PR that introduces it.
6. **Adopting a new library, framework, or external data source is a
   constitution-level change**: add it to `pyproject.toml` (in the correct
   service's dependency block, see #3) with a rationale in the PR, and if it
   changes a rule above, amend this section (see Governance).

## Project structure

1. **`src/` is one package per source project, not flat.** Five packages —
   `news_collector`, `extractor`, `pricing`, `sec_edgar`, `data_mining` —
   each kept its own originating project's internal import style (see
   `CLAUDE.md`'s "Architecture" table for the source mapping). A new
   top-level package needs the same justification `data_mining` has — a
   genuinely independent domain concern with its own schema/queries, not
   just "this file is getting long" inside an existing package.
2. **Entrypoints live by kind, each with the same bootstrap.** `apps/*.py`
   (FastAPI/uvicorn servers) and `cli/*.py` (argparse batch drivers, one per
   service except the gateway) both prepend `src/` to `sys.path`
   (`sys.path.insert(0, str(Path(__file__).resolve().parent.parent /
   "src"))`) before importing — copy that pattern, don't invent a second one
   (e.g. an installed console-script entry point). `apps/gateway.py` is the
   one entrypoint with no `cli/` counterpart — it only mounts the other
   four's routers.
3. **`cli/*.py` mirrors its service's API routes 1:1**, one subcommand per
   route where routes exist (`pricing_cli.py pricing AAPL --start … --end
   …`, `sec_edgar_cli.py filings AAPL --form 10-K`), and prints JSON to
   stdout. `news_collector_cli.py` wraps `news_collector.main`'s own
   pre-existing argparse CLI rather than reimplementing it.
4. **Tests mirror `src/` under `tests/<package>/`** (`tests/news_collector`,
   `tests/extractor`, `tests/pricing`, `tests/sec_edgar`,
   `tests/data_mining`). `pytest.ini`'s `pythonpath = src .` is what makes
   both `import news_collector` and `import apps.pricing_api` work in
   tests — don't add `sys.path` hacks inside test files to route around it.
   Model-free acquisition code is hermetic via mocking the outbound
   boundary (`respx` for HTTP, `hypothesis` for property tests on parsing/
   queueing logic, monkeypatched `finnhub.Client`/`edgar.Company`) rather
   than needing GPU-style fixtures.
5. **Docs live under `docs/`**, one topic per file: one file per service
   under `docs/modules/*.md`, cross-cutting migration notes named by
   topic/date (`portfolio-common-v1-migration-plan.md`,
   `portfolio-common-v1.2-engine-agnostic.md`). `docs/superpowers/` holds
   earlier, pre-spec-kit planning artifacts (the original monorepo-
   integration plan/spec) — historical record, not the canonical spec going
   forward. A spec-kit artifact (this constitution, `SPEC.md`, `PLAN.md`,
   `TASKS.md`) goes under `.specify/memory/` instead.
6. **Config lives where its tool expects it, not duplicated.** Ruff:
   `.code_quality/ruff.toml` (root `ruff.toml` only `extend`s it so plain
   `ruff check .` from the repo root resolves the same config `pre-commit`
   uses). Mypy: `.code_quality/mypy.ini`. Pytest: root `pytest.ini`. Don't
   fork a second config file for a tool that already has one.
7. **Environment**: `.env` (git-ignored) holds `DATABASE_URL`,
   `UNIVERSE_DB_PATH`, `FINNHUB_API_KEY`, `NAME`/`EMAIL`,
   `DISCOVERY_START_DATE`/`DISCOVERY_END_DATE`, `SEEKINGALPHA_RAPIDAPI_KEY`,
   and the optional proxy vars; `.env.example` is the committed template —
   keep it in sync with every env var a new feature reads. Unlike a
   single-service repo, **every** `apps/*_api.py` calls `load_dotenv()`
   itself (plus `news_collector.config`/`news_collector.main`) — that
   repetition is deliberate, not debt, so each service stays independently
   runnable with no shared bootstrap module to import first.
8. **Naming**: modules and functions describe the pipeline stage or
   Finnhub/EDGAR domain they implement, matching the stage/table pairing
   (`discover` → `discovered_urls`, `extract` → `articles`) and each
   `finhub` split's original domain (`trading`/`market`/`news` → `pricing`;
   `fundamental` → `sec_edgar`) rather than inventing a new term for the
   same concept.

## AI behavior

*Data-acquisition behavior (this repo's own automated components — there
are no ML/inference models here; "AI behavior" below governs how the
acquisition stages themselves must behave, plus coding-agent conduct):*

1. **The source set is fixed, not dynamically discovered.** `news_collector`
   discovers URLs from exactly seven named domains (CNBC, Yahoo Finance,
   Financial Times, Investing.com, Nasdaq, Seeking Alpha, StockTwits);
   `pricing`/`sec_edgar` pull from exactly Finnhub and SEC EDGAR. Adding an
   eighth news domain or a new provider is a constitution-level dependency
   change (see Technological stock #6), not a runtime option.
2. **Every stage is idempotent and resumable by construction, not by
   convention.** `discover --resume` (default on) skips only a
   `(ticker, domain)` pair with an exact-range `discovery_progress` row
   already recorded; `extract` processes only `discovered_urls` rows not
   yet in `articles`. A newly-added S&P 500 member is never special-cased —
   it simply has no checkpoint yet, so the next full-universe/full-range run
   backfills it like any other ticker's first run (`docs/modules/news-
   collector.md`).
3. **Upstream failure is an inspectable result, never a silent gap or an
   uncaught exception reaching the caller.** Every public method in
   `pricing/` and `sec_edgar/agent.py`'s `EdgarAgent` returns
   `{"success": True, "data": ...}` or `{"success": False, "error":
   "<message>"}` — a premium-gated Finnhub endpoint, a bad ticker, or an
   EDGAR lookup failure comes back as data the caller can branch on, not a
   raised exception or a swallowed `None`. A new method in either module
   follows this exact shape; don't introduce a third convention (raising,
   returning `None`, returning a bare list) alongside it.
4. **Acquired content is raw third-party data, not a verified or curated
   claim.** Article text, OHLCV bars, company news, and SEC filings are
   exactly what the provider returned — this repo does no sentiment/
   entity/category judgment of its own (that is `portfolio-nlp`'s job) and
   must not editorialize, filter for "quality," or silently drop rows that
   look wrong without recording that it did.
5. **A DB-engine change is a `portfolio-common` re-pin, not a rewrite
   here.** Every engine-specific SQL fragment goes through
   `conn.dialect`/`portfolio_common.db` helpers (Technological stock #4) —
   a new backend is implemented once, in `portfolio-common`, and adopted
   here by bumping the pinned tag.

*Claude Code / coding-agent conduct on this repo:*

6. **Match existing structure before introducing new structure** — check
   where a file's siblings live and follow that package's placement,
   naming, and import style (each package's own convention per Project
   structure #1) rather than a generic layout.
7. **This constitution and `.specify/memory/SPEC.md` are the binding
   reference for planning and review** — read both before drafting a
   spec/plan, and resolve any conflict between a request and a stated
   principle or requirement by surfacing it or proposing an amendment, not
   by quietly overriding either. `CLAUDE.md` (tracked, checked into this
   repo, unlike some sibling repos' untracked copy) may carry additional
   situational detail, but where it and this constitution or `SPEC.md`
   disagree, treat the disagreement as staleness to flag and fix, not a
   license to follow whichever is more convenient.
8. **Prefer the smallest change consistent with the existing pattern**; no
   opportunistic refactors, renames, or new abstractions outside what the
   spec/task calls for.
9. **`CLAUDE.md` must always exist on disk, stay tracked, and must always
   carry a reference to both this constitution
   (`.specify/memory/constitution.md`) and `.specify/memory/SPEC.md`.** If
   either reference is missing (freshly regenerated via `/init` or
   otherwise edited), add it in the same change — don't treat the reference
   as a one-time step.
10. **Ask before expanding scope this constitution doesn't cover** — a new
    external data provider, a new heavy dependency, a shared-schema change
    to `discovered_urls`/`articles`, or anything touching the two-package
    (`news_collector`/`extractor`) DB contract.
11. **Reconcile the architecture artifacts at the close of every
    development effort** — when a PR/feature/fix is done (merged, or ready
    to merge), update both:
    - the general, system-wide artifact — [Portfolio
      Thesis](https://claude.ai/code/artifact/d3865a63-2894-4e20-b38a-7e50cf0d4040)
      (the six-repo integrated architecture overview); and
    - the repository-specific artifact — [Portfolio Data
      Mining](https://claude.ai/code/artifact/85dbb7eb-d561-4be7-9639-adca66089f49)
      (this repo's component flow, stage table, gaps, and next steps).

    to close whatever gaps the effort closed and reconcile the artifact's
    prose with what the code now actually does — an artifact describing a
    gap that was just fixed, or a next-step item that was just built, is
    now wrong and must be corrected in the same pass, not left stale.
    **Never** rename either artifact when doing this — **NEVER** change its
    title (the `<title>` tag / the name shown in the artifact gallery).
    Content, diagrams, gap lists, and next-steps update freely; the name is
    stable forever, independent of content changes. (See `Artifact` tool
    guidance: title changes are an explicit, separate, user-directed
    action, never a side effect of a content update.)

## Executable cmds

Canonical commands — a spec/plan should reference these, not invent new
ad-hoc invocations:

```bash
uv sync                                     # install deps (dev group)
cp .env.example .env                        # fill in FINNHUB_API_KEY, NAME, EMAIL

uv run apps/news_collector_api.py           # FastAPI -> :8001/docs
uv run apps/news_crawler_api.py             # FastAPI -> :8002/docs (dev/test only)
uv run apps/pricing_api.py                  # FastAPI -> :8004/docs
uv run apps/sec_edgar_api.py                # FastAPI -> :8005/docs
uv run apps/gateway.py                      # all four mounted -> :8000 (demo only)

uv run cli/news_collector_cli.py discover --tickers AAPL MSFT
uv run cli/news_crawler_cli.py --limit 50
uv run cli/pricing_cli.py pricing AAPL --start 2024-01-01 --end 2024-06-01
uv run cli/pricing_cli.py universe-backfill        # one-time: reconstruct point-in-time universe
uv run cli/pricing_cli.py universe-snapshot        # periodic, by hand: refresh it
uv run cli/sec_edgar_cli.py filings AAPL --form 10-K
uv run cli/<service>_cli.py --help          # full subcommand list per service

uv run pytest                               # full suite (213 tests as of PR #26)
uv run pytest tests/<package> -q            # one module's tests

uv run ruff check .                         # lint (config: .code_quality/ruff.toml via root pointer)
uv run ruff format --check .                # format check
uv run mypy --config-file=.code_quality/mypy.ini src apps cli   # types

uv run pre-commit run --all-files           # all of the above hooks, plus hygiene checks
```

1. **There is no CI workflow file in this repo yet** (no `.github/workflows/`
   — unlike sibling repos such as `portfolio-nlp`). Until one exists, the
   four checks above (ruff check, ruff format --check, mypy, pytest) are a
   manual gate run locally before every PR, in that order — treat them as
   mandatory by convention even though nothing enforces it automatically
   yet (see `PLAN.md` for whether adding one is in scope).
2. **Don't hardcode a different Python/uv invocation** (bare `python`,
   `pip install`, `pytest` without `uv run`) in scripts, docs, or CI-to-be —
   every command goes through `uv run` so it resolves the locked
   environment.
3. **`uv run apps/*_api.py` / `uv run cli/*_cli.py` work standalone**, with
   no `$PYTHONPATH` setup, because of the `sys.path` bootstrap (Project
   structure #2) — don't add a setup step that's only needed for one
   invocation style.

## Code & Git

1. **Formatting/linting/types are enforced, not advisory**: `ruff-check
   --fix` + `ruff-format` + `mypy` (project venv, whole-graph) all run via
   `pre-commit`. A `# noqa` / `# type: ignore` needs a comment saying why
   the finding is wrong for this code, not just silence.
2. **Commit messages are Conventional Commits**, enforced by the
   `commitizen` pre-commit/pre-push hook — `type(scope): summary`, matching
   the existing history (`refactor: ...`, `feat: ...`, `docs: ...`,
   `chore: ...`, `test: ...`). Reference the PR number in the subject once
   it exists, as the existing log does (`(#26)`).
3. **Branch off `master`, never commit to it directly.** `master` is the
   integration branch; feature/fix/docs work happens on a descriptively-
   named branch (`feat/...`, `fix/...`, `docs/...`, `chore/...`,
   `refactor/...`, `test/...`) opened as a PR.
4. **Pre-commit hooks are mandatory, not optional**: `check-yaml`,
   `check-case-conflict`, `debug-statements`, `detect-private-key`,
   `check-merge-conflict`, `check-added-large-files` run alongside
   ruff/mypy/commitizen — install them
   (`uv run pre-commit install --hook-type pre-commit --hook-type
   commit-msg --hook-type pre-push`) rather than relying on remembering to
   run checks manually.
5. **No secrets committed.** `.env` stays git-ignored; `.env.example` holds
   placeholder values only; `detect-private-key` is a backstop, not the
   first line of defense — never paste a real key into a commit, issue, or
   PR description to "show" a config.
6. **Leave the working tree checked out on the branch just pushed/PR'd.**
   After opening a PR, don't switch back to `master` (or anywhere else) —
   the local checkout stays on that branch so the user can review the
   actual working tree immediately, without asking for a checkout or doing
   it themselves. Only move off it (per item 3, always to a fresh branch
   off up-to-date `master`) when starting genuinely new work, or when
   asked to.

## Governance

This constitution supersedes ad-hoc convention when the two conflict. A
spec or plan may not silently contradict a rule above; instead:

1. Propose the amendment as its own change (state which section, what
   changes, and why).
2. Get it reviewed the same way a code PR would be (this repo's normal
   review path) before relying on it.
3. Bump the version below per semver: **MAJOR** for a removed/redefined
   principle, **MINOR** for a new principle or materially expanded
   guidance, **PATCH** for wording/typo fixes.
4. Record the change under "Last Amended" with the date.

Compliance is expected to be checked the same way lint/type/test gates
are — a reviewer (human or agent) rejecting a PR that violates a principle
above should cite the section by name.

**Version**: 1.0.0 | **Ratified**: 2026-09-12 | **Last Amended**: 2026-09-12
