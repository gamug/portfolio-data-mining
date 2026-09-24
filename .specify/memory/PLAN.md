# PLAN.md — `portfolio-data-mining`

The implementation plan for the live backlog identified in
`.specify/memory/SPEC.md`. Where the constitution is principles and
`SPEC.md` is the requirements/architecture contract, this document is the
"how, and in what order" for the work that contract still leaves open.

**Scope of this plan is deliberately narrow.** `SPEC.md` §13 (Open
Questions & Risks) lists nine items; §14 (Scope Boundaries) explicitly
marks eight of them **accepted** — permanent characteristics of this
project at its current, non-production scope (item 9, `yfinance` being an
unofficial source, was added alongside Work item 3). Only **one** item — §13
item 5, "no CI workflow file exists" — is flagged "should fix regardless of
scope." Work items 1–2 cover that item. Work item 3 is the one deliberate
addition beyond it: a yfinance-backed corporate-actions endpoint moved here
from `portfolio-financial-analysis`'s backlog (see Work item 3's "Why" —
it is data acquisition, which is this repo's job, and a downstream repo is
blocked on it). This is not a product roadmap, and it does not resurrect
anything §14 already closed — see Non-goals below.

## Goal

Close the one backlog item that is genuinely actionable without expanding
this project's scope: turn the manual, convention-based lint/format/type/
test sequence (constitution: Executable cmds #1) into an enforced CI
workflow, and (a maintainer follow-up) make it a required check. Separately
(Work item 3), serve the yfinance corporate-actions data a downstream repo
already probes for.

## Non-goals

Everything else in `SPEC.md` §13 stays exactly as §14 disposed of it —
**not** part of this plan:

- Item 1 — no scheduler for the collector → extractor cadence: accepted,
  matches this repo's "not production software" stance.
- Item 2 — SQLite's single-writer/single-file model: accepted at current
  corpus scale; the `Dialect` seam exists if this ever needs revisiting, but
  implementing a second backend is a separate, much larger effort, not
  triggered by this plan.
- Item 3 — manual universe backfill/snapshot: accepted, same reasoning as
  item 1.
- Item 4 — no authentication on any of the four services: accepted for the
  private/trusted-network, single-operator assumption this project
  currently operates under; adding auth is a scope change requiring its own
  spec, not a line item here.
- Item 6 — raw SQL in `apps/news_crawler_api.py` bypassing `extractor.db`'s
  named-query pattern: already tracked as a follow-up in
  `docs/portfolio-common-v1.2-engine-agnostic.md`; not duplicated here.
- Item 7 — the Windows-only `hypothesis` flake in `tests/news_collector`:
  accepted as a known, non-blocking, platform-specific flake; not something
  a CI workflow on `ubuntu-latest` (see Work item 1) would even surface.
- Item 8 — the Turso/libSQL migration prototype (PR #12), closed unmerged:
  a deliberate past decision, not reopened by this plan.

## Work item 1 — Add a CI workflow (`.github/workflows/ci.yml`)

**Why**: `SPEC.md` NR-007 / §13 item 5 — this repo has no
`.github/workflows/` at all today. The four-step gate (`ruff check` →
`ruff format --check` → `mypy` → `pytest`) that the constitution's
Executable cmds section already documents as mandatory is enforced only by
a contributor remembering to run it; nothing stops a red PR from merging.

**What makes this cheap here** (unlike, e.g., a sibling repo's GPU/data-
dependent eval gate): per `SPEC.md` NR-004, this repo's entire test suite is
already hermetic — every external boundary (Finnhub, SEC EDGAR, Wikipedia,
the seven news domains, DuckDuckGo) is mocked, and there's no GPU, no
committed data fixtures, and no external service credentials required to
run `uv run pytest`. A standard `ubuntu-latest` GitHub-hosted runner is
sufficient; no self-hosted runner, no repo secrets, no infrastructure
decision is needed (contrast the ops-heavy shape of a data/GPU-dependent
gate elsewhere in the Portfolio Thesis).

**Approach**:

1. Add `.github/workflows/ci.yml`, triggered on `push` to `master` and on
   `pull_request`, `runs-on: ubuntu-latest`.
2. Steps: checkout → set up Python 3.12 + `uv` (`astral-sh/setup-uv`) →
   `uv sync --group dev` → `uv run ruff check .` → `uv run ruff format
   --check .` → `uv run mypy --config-file=.code_quality/mypy.ini src apps
   cli` → `uv run pytest -q`. Same order the constitution's Executable cmds
   section already prescribes for local runs — the workflow should not
   invent a different gate order.
3. No secrets, no self-hosted runner label, no `workflow_dispatch`-only
   trigger — this should run automatically on every push/PR from day one.
4. Add a status badge to `README.md` once the workflow exists and has run
   at least once successfully (cosmetic, but makes the gate visible).

**Acceptance criteria**:

- `.github/workflows/ci.yml` exists and triggers on both `push` to `master`
  and `pull_request`.
- A normal PR run completes all four steps successfully on
  `ubuntu-latest`, matching a local `uv run pytest` result (213 tests, per
  `SPEC.md` §10, unless a concurrent change altered that count).
- A deliberately-introduced failure (a `ruff`-flagged lint issue, or a
  failing test on a throwaway branch) causes the workflow to fail — proving
  the gate actually gates, not just runs. Revert the deliberate failure
  before merging.
- `SPEC.md` NR-007 and §13 item 5 updated to reflect the resolved state
  (annotate in place, keep the item number — this repo's own
  no-renumbering convention, `SPEC.md`'s header note). Also update the two
  architecture artifacts per constitution AI behavior #11 (Portfolio Thesis
  + Portfolio Data Mining) — reconcile, never rename.

**Out of scope for this work item**: making the new check a *required*
status check for merging — that's a GitHub branch-protection setting, a
repo-configuration change outside this codebase, not a code change. See
Work item 2.

## Work item 2 — Make the new CI check required (ops, maintainer)

**Why**: a CI workflow that runs but isn't required does not actually
prevent a red PR from merging — it only makes the failure visible after the
fact. Making it required is a one-time GitHub repository setting, not a
code change, and constitution AI behavior #10 ("ask before expanding
scope") applies the same way it would to any other repo-configuration
decision.

**Steps** (for the repo owner/maintainer, via GitHub's branch protection
settings for `master`):

1. After Work item 1's workflow has run successfully at least once (so its
   check name is selectable), open Settings → Branches → branch protection
   rule for `master`.
2. Add the new workflow's job as a required status check.
3. Optionally also require the branch to be up to date before merging,
   consistent with this repo's existing "branch off master, PR" convention
   (constitution: Code & Git #3) — a judgment call for the maintainer, not
   mandated here.

**Acceptance criteria**:

- A PR with a failing CI run cannot be merged through the GitHub UI (the
  merge button is disabled/blocked).
- `SPEC.md` §13 item 5 annotated as fully resolved (workflow exists *and*
  is enforced), distinct from Work item 1's "workflow exists" milestone.

## Work item 3 — yfinance corporate-actions endpoint (code, cross-repo origin)

**Status: built and merged (PR #36, 2026-09-19).** Verified live against yfinance
(XOM dividends, NVDA split, the `1900-01-01` probe range over HTTP), and against
the downstream client (`QuantPricingClient.probe('XOM')` → `True`). The redeploy
hand-off (`T-026`) is done too: PFA's `T-052` verified the redeployed gateway live
on 2026-09-21. The architecture artifacts (`T-027`) and the constitution wording
(`T-024`, constitution 1.5.0) are done. **Closed** — tasks in `CHANGELOG.md`.

**Why**: `portfolio-financial-analysis` (PFA) tracked this as its own Work
item 6 / `T-050`–`T-052`, but it is data mining, not analysis, so it moved
here (PFA's copy of `T-050`/`T-051` is annotated "moved" in place). PFA's
`src/quant/pricing_client.py::QuantPricingClient.actions` already probes
`GET /pricing/{ticker}/actions` and `GET /pricing/{ticker}?actions=true`;
neither exists on this repo's pricing service (`apps/pricing_api.py`'s
`/pricing/{ticker}` returns OHLCV only), so `probe()` always returns `False`
and PFA's `quant backfill-actions` falls back to `corpact-v0-approx`
(fiscal-year dividends spread over four synthetic quarterly dates), leaving
202 of 503 assets with zero recorded dividends. `yfinance` is already a
dependency and already used by `src/pricing/fetcher.py`'s fallback path;
`Ticker(t).dividends` / `.splits` return exact ex-dates and values with no
API key.

**Approach**:

1. `StockPriceFetcher.get_corporate_actions(ticker, start_date, end_date)`
   in `src/pricing/fetcher.py`, backed by `yf.Ticker(t).dividends` /
   `.splits`. Filter to the inclusive date range **by the series' own index
   date** (normalize the tz-aware index to `YYYY-MM-DD`), not through a
   yfinance range argument — so PFA's probe range (`1900-01-01`–
   `1900-01-02`) returns empty lists cleanly instead of erroring. Return the
   same shape family as `get_daily_candles`: `{"ticker", "start_date",
   "end_date", "source": "yfinance", "dividends": [{"date", "value"}],
   "splits": [{"date", "value"}], "warning": str | None}`. `dividends[].value`
   is cash per share; `splits[].value` is a ratio (`4.0` for a 4:1 split).
2. Never raise (constitution AI behavior #3): a yfinance failure returns
   empty lists plus a `warning`, mirroring `get_daily_candles`. An empty
   range is **HTTP 200 with empty lists, never a 404** — a 404 reads to
   PFA's probe as "endpoint doesn't exist" and it keeps falling back.
3. `GET /pricing/{ticker}/actions?start_date=&end_date=` in
   `apps/pricing_api.py` (tag `Pricing`), with `daily_pricing`'s existing
   validation (`start_date > end_date` → 400). Dedicated route only; PFA's
   client tries it before the `actions=true` form, so the query flag on the
   existing route is not needed.
4. `actions` subcommand in `cli/pricing_cli.py` (constitution Project
   structure #3 — the CLI mirrors the routes 1:1).
5. Docs: `docs/modules/pricing.md`, plus endpoint lists in the `apps/`/`cli/`
   docstrings and `README.md` where they enumerate pricing routes.

**Decision — yfinance-only, not Finnhub-first**: PFA's original plan said to
reuse the candle endpoint's Finnhub-first/yfinance-fallback pattern. Finnhub's
dividend/split endpoints have not been verified as free-tier here, and the
purpose is exact ex-dates, so v1 is yfinance-only and `source` is always
`"yfinance"`. Revisit only if a free Finnhub source is confirmed.

**Constitution notes**: no new dependency and no new provider (`yfinance` is
already in `pyproject.toml` and already used by `pricing`), so Technological
stock #7 / AI behavior #10 are not triggered. Two constitution passages are
stale against the code already, though — Technological stock #2 lists
`yfinance` only under `news_collector`, and AI behavior #1 says `pricing`
pulls from "exactly Finnhub and SEC EDGAR". Amending them is a separate,
reviewed change per Governance (`T-024`), not folded silently into this work.
Yahoo access through `yfinance` is unofficial with no SLA — state that
rate-limit/ToS posture in the implementing PR (Technological stock #6).

**Acceptance criteria**:

- `curl "http://127.0.0.1:8004/pricing/XOM/actions?start_date=2022-01-01&end_date=2026-08-27"`
  returns a non-empty `dividends` list with `source: "yfinance"`.
- The same route with `start_date=1900-01-01&end_date=1900-01-02` returns
  HTTP 200 with empty `dividends`/`splits` (not 404, not an error) — the
  exact request PFA's `probe()` makes.
- `start_date > end_date` returns 400; a yfinance failure returns 200 with
  empty lists and a non-null `warning`.
- `uv run cli/pricing_cli.py actions XOM --start ... --end ...` prints the
  same JSON.
- New tests in `tests/pricing/test_fetcher.py` mock `yf.Ticker` (no network,
  NR-004): in-range dividends and splits, out-of-range rows excluded, empty
  range, tz-aware index normalization, yfinance raising, and the 1900 probe
  range.
- From PFA, after this repo's pricing service is redeployed:
  `QuantPricingClient(...).probe('XOM')` returns `True` — that check is
  PFA's `T-052`, downstream of this item (`T-026`).
- `SPEC.md` gains the corresponding requirement and reconciled test count;
  the architecture artifacts are reconciled (`T-027`).

## Work item 4 — Fix `sec_edgar` `filing_by_year`/`financials` for multi-filing-per-year forms (code, priority)

**Status: built, verified live, and merged (PR #39, 2026-09-21).**

**Why**: a user-reported bug — requesting `10-Q` filings for a ticker/year
returned only one filing when a company typically files three per fiscal
year. Root cause: `EdgarAgent.get_filing_by_year` and `EdgarAgent.
get_financials` (`src/sec_edgar/agent.py`) both used `next((f for f in
filings if f.filing_date.year == year), None)` to find "the" filing for a
form+year — an implicit one-filing-per-year assumption that holds for
`10-K` (so the bug was invisible there) but not `10-Q` (or any other form
that recurs within a year, e.g. `8-K`). No existing test constructed
multiple same-year filings for one form, which is why it shipped uncaught.

**Approach**:

1. Add failing tests reproducing the bug (multiple same-year filings for
   one form) in `tests/sec_edgar/test_agent.py`.
2. Fix `EdgarAgent.get_filing_by_year` to return *all* matching filings as
   a list (most-recent-first, trusting `company.get_filings()`'s existing
   ordering), not just the first. An empty match becomes `{"success":
   True, "data": []}` (aligned with `get_filings`/`search_filings`'s
   existing "empty list is not an error" convention), not an error —
   breaking response-shape change for this one route, flagged in the PR.
3. Fix `EdgarAgent.get_financials` to accept an optional `accession_number`
   to disambiguate when form+year matches more than one filing (reusing
   the existing unique-filing identifier rather than inventing a new
   "quarter" concept): 0 matches → unchanged error; 1 match → unchanged
   behavior; >1 matches with no `accession_number` → new error listing the
   candidates; >1 matches with an `accession_number` → select it, or error
   if it doesn't match one of the candidates.
4. Update `apps/sec_edgar_api.py` (`edgar_financials` gains
   `accession_number`) and `cli/sec_edgar_cli.py` (`financials` subcommand
   gains `--accession-number`); `filing_by_year`/`filing-by-year` need no
   signature change, only their response shape changes.
5. Docs: `src/sec_edgar/examples.py` updated for the list shape and a new
   10-Q/`accession_number` example; `docs/modules/sec-edgar.md` gets a note
   under "Endpoints" describing both behavior changes;
   `docs/modules/edgar_examples.txt` (captured live output) regenerated
   from a real run when network access is available.

**No constitution change** — no new dependency, provider, or stack-level
rule; this is a within-service bug fix, so constitution AI behavior #10
("ask before expanding scope") doesn't apply here.

**Acceptance criteria**:

- `uv run pytest tests/sec_edgar -q` passes, including new tests for the
  multi-filing case (34 → 38 tests).
- `GET /edgar/filing_by_year/{ticker}?form=10-Q&year=<Y>` for a
  three-10-Q year returns all three, most-recent-first, as `data`; an
  empty match returns `{"success": true, "data": []}`, not an error.
- `GET /edgar/financials/{ticker}?form=10-Q&year=<Y>` without
  `accession_number` errors listing the available accession numbers when
  more than one filing matches, and succeeds when `accession_number`
  selects one of them; the `10-K` (single-match) path is unchanged.
- `SPEC.md` FR-004 and the §10/NR-004 test count (230 → 234) reconciled;
  the architecture artifacts (Portfolio Thesis + Portfolio Data Mining)
  reconciled per constitution AI behavior #11.

## Sequencing

Work items 3 and 4 are closed (merged and verified; tasks in `CHANGELOG.md`).
Work items 1–2 stay reverted/on hold at the maintainer's prior request. Otherwise there is no ordering
constraint from the rest of the backlog, since every other `SPEC.md` §13
item is accepted (Non-goals above) and not touched by this plan.

See `TASKS.md` for the discrete, checkable task breakdown.
