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

## Work item 5 — Fix `sec_edgar`'s revenue-total contradiction (code, cross-repo origin)

**Status: closed 2026-10-02 (tasks moved to `CHANGELOG.md`). `T-118` (APA's revenue
instance) and `T-042` (the general synthesis-defect fix) were built and live-verified, and
`portfolio-financial-analysis` re-verified the redeployed gateway and closed its own
`T-117`/`T-118` in its PR #104 on 2026-09-30 (`T-041`).**

**Why**: `portfolio-financial-analysis`'s `T-117`/`T-118` (`docs/model_fixes.md`) —
APA's (CIK `0001841666`) `us-gaap:Revenues` ("Total revenues") is exactly ~2x its own
income statement's later, smaller "Total revenues and other" subtotal every fiscal year
since FY2023. Root cause traced live to this repo, not APA's own filing, and it is a
**general `edgartools` synthesis defect, not an APA/revenue-specific one**: `edgartools`'
(`==5.44.1`) `xbrl.statements.income_statement().to_dataframe()` — the only place
`get_financials` sources income-statement rows from — synthesizes a non-dimensional
"total" row for a concept by *summing that concept's dimensional axis members*
(e.g. `srt:ProductOrServiceAxis` breakdown rows) whenever the filer didn't tag a
non-dimensional fact for it directly. That summation double-counts whenever one of the
members is itself a parent whose value already includes its own children. APA's case:
FY2023's synthesized `us-gaap_Revenues` is `8279 (parent "Oil") + 7385 (children already
rolled into that parent) + 894 (an unrelated member) = 16558`, surfaced as if it were
APA's own consolidated total — APA has never filed a real `us-gaap:Revenues` fact at all
(`data.sec.gov`'s `companyconcept` API returns `404 NoSuchKey` for
`CIK0001841666/us-gaap/Revenues`); the correct figure is `8279`. Same shape in FY2024:
`9737 + 8196 + 1541 = 19474` vs. the correct `9737`. A full-universe scan of every stored
filing confirms this is not isolated: 163 mismatched parent-vs-summed-children values
across 105 concept/period pairs, spanning 1,101 synthesized non-dimensional rows total —
revenue is only the one concept this work item corrects. This repo's `get_financials`
passed the synthesized, double-counted row through unexamined;
`portfolio-financial-analysis`'s own downstream `Statements._rows_for` filters out every
dimensional row by design, so it never even saw the correct, dimensionally-tagged "Oil"
parent-member total ($8,279M for FY2023) sitting a few rows later in the same payload —
that later row is corroborating evidence of the correct figure, not itself the
confirmed mechanism (the parent-vs-summed-children arithmetic above is).

**Approach**:

1. New `correct_revenue_totals(income_statement)` in `src/sec_edgar/agent.py`: scans the
   same non-dimensional-row-filtered document order for a first aggregate revenue concept
   (`us-gaap_Revenues`/`RevenuesNetOfInterestExpense`/`RegulatedAndUnregulatedOperatingRevenue`)
   contradicted by a later, non-dimensional row whose label also reads as a revenue total
   and is materially smaller. When found, and the rows between the two are individually
   small enough to trust as adjustment items, corrects that period's value to the later
   row's value less those in-between rows; otherwise drops it (`None`) rather than
   guessing. Ported directly from `portfolio-financial-analysis`'s `Statements.
   _label_total_correction` (`T-117`), which validated the identical algorithm against
   that repo's full 503-asset production universe (16 filing-periods flagged, all APA,
   all safely corrected, 0 false positives) — including the "Total cost of revenues"
   false-positive shape (a near-universal COGS label matching "total"/"revenue" as bare
   substrings) that scan caught and excluded before T-117 shipped, reused verbatim here
   rather than re-discovered.
2. Wire it into `get_financials`: `income_statement` is the corrected list, not the raw
   `clean_data_frame` output; `balance_sheet`/`cash_flow` are untouched (this fix is
   scoped to revenue only, not the general synthesis defect). `correct_revenue_totals`
   now returns `tuple[income_statement_rows, corrections]`, and `get_financials` adds
   every correction/drop it makes as a `{"concept", "column", "original", "corrected",
   "rule": "T-118"}` entry to a new top-level `data["corrections"]` list, so callers see
   exactly what was touched instead of trusting the numbers silently.
3. Tests in `tests/sec_edgar/test_agent.py`: `correct_revenue_totals` unit tests
   (APA's real FY2023 shape, a genuinely larger later total left alone, the
   "Total cost of revenues" exclusion, an unsafe-to-derive contradiction dropping the
   value, a dimensional row never picked as either candidate, a no-op when no aggregate
   concept is present, no mutation of the input, and the returned `corrections` list
   content for each case) plus one `get_financials` end-to-end test asserting
   `data["corrections"]`.
4. `SPEC.md` FR-004 updated with the correction's contract, the confirmed
   parent-vs-summed-children mechanism, and a live-verified APA figure citation;
   `docs/modules/sec-edgar.md` gets a note under "Endpoints" scoped as "APA revenue
   instance only", not a general fix.
5. **(`T-042`, the general fix)** New `reconcile_with_filed_facts(rows, xbrl, statement,
   skip=frozenset())` in `src/sec_edgar/agent.py`: the general, concept-agnostic
   counterpart to step 1's label heuristic. For every non-dimensional row not in *skip*,
   queries the already-loaded filing's own XBRL facts (`xbrl.facts.query().by_concept(...)
   .by_dimension(None)`, no extra network call — `_filed_nondimensional_values`, with
   `_parse_period_column` turning a rendered column name back into `(end_date,
   duration_kind)` and a day-span bucket match disambiguating same-end-date facts of
   different lengths) for a genuinely filed, non-dimensional fact at that concept/period.
   A rendered value matching one (by magnitude — same-magnitude-opposite-sign is
   `edgartools`' own presentation-layer sign convention, not a defect, live-verified
   against APA's `TreasuryStockCommonValue`) passes through; one with none filed at all is
   dropped (`None`) — there is no general way to reconstruct it the way step 1's label
   subtraction can for revenue, which is why `get_financials` runs step 1 first and passes
   its `(concept, column)` pairs as *skip* here, or this function would find "nothing
   filed" for revenue again and erase that reconstruction. A column that doesn't parse as
   a period, or where more than one filed value matches ambiguously, is left alone —
   never guessed. Wired into `get_financials` for all three statements (not just
   `income_statement`), combining every layer's corrections into one
   `data["corrections"]` list with an added `"statement"` field and, on `T-042` entries, a
   `"reason"`. A first version added an instant-date fallback when a duration lookup found
   nothing (to catch a cash-flow statement's "beginning/end of period" balance, genuinely
   an instant fact under a duration column) — live-verification against MSFT's FY2024 10-K
   caught it silently substituting the *wrong* endpoint's value (the "beginning of period"
   row needs the period's start date, not the column's end date, and both rows share one
   concept with no reliable way to tell them apart); removed rather than fixed with a
   label heuristic — that row now drops instead, a known conservative gap, documented on
   `_filed_nondimensional_values`.

**No constitution change** — no new dependency, provider, or stack-level rule; a
within-service correctness fix to an existing route's output, so constitution AI
behavior #10 doesn't apply here.

**Acceptance criteria**:

- `uv run pytest tests/sec_edgar -q` passes, including `T-118`'s original 8 updated tests
  plus `T-042`'s new tests (`_parse_period_column`, `_filed_nondimensional_values`,
  `reconcile_with_filed_facts` unit tests, and an end-to-end test exercising both layers
  together) — 66 tests in `tests/sec_edgar`, 262 in the full suite.
- Live-verified against real SEC EDGAR data (`NAME`/`EMAIL` set, no mocking):
  - Every available APA 10-K (FY2021 through FY2025, filed 2022-2026) and every 2024
    10-Q's `get_financials` call resolves `us-gaap_Revenues` to the statement's own
    derived total for every period step 1's label heuristic can reconstruct (FY2023
    $8,279M, FY2024 $9,737M, FY2025 $8,920M, FY2021 $7,985M, plus every 2024 10-Q's
    Q1/Q2/Q3/YTD columns — exact match to `portfolio-financial-analysis`'s `T-117`
    acceptance figures); `data["corrections"]` records each correction/drop made.
  - **Updated from `T-118`'s original acceptance note**: FY2021-filed-2022's 10-K, where
    step 1 finds no later corroborating row to reconstruct against, is *no longer* left at
    its original $1,082M (a separate, too-small, pre-existing defect,
    `portfolio-financial-analysis`'s own `T-095`) — `T-042` now also catches this period
    (APA never filed `us-gaap:Revenues` non-dimensionally in any period) and drops it to
    `None` instead. This is `T-042` correctly extending coverage to a case step 1 was
    explicitly out of scope for, not a regression of step 1's own reconstruction, which is
    unaffected wherever it applies (confirmed above).
  - MSFT's FY2024 10-K (a clean, well-filed company — sanity check for false positives):
    only the known cash-flow "beginning/end of period" gap above flags; nothing else.
  - SNA's FY2026 10-Qs (segment-breakdown-heavy, cited in the PR #44 review as a live
    non-revenue example): non-dimensional rows that are genuinely filed (e.g.
    `us-gaap:OperatingIncomeLoss` "Operating earnings") pass through untouched; ones that
    aren't (e.g. `us-gaap:OperatingExpenses`) drop.
- Scope: `T-042` is the general fix (any concept, all three statements, validated against
  actually-filed facts rather than label-pattern detection). It did not by itself close
  `portfolio-financial-analysis`'s `T-117`/`T-118` — that repo re-verified against the
  redeployed `sec_edgar` itself and closed them in its PR #104 on 2026-09-30 (`T-041`,
  done 2026-10-02).
- `SPEC.md` FR-004 reconciled; `docs/modules/sec-edgar.md` updated (both layers, the
  known conservative gap, and the live-verification evidence above).
- `uv run ruff check .` / `ruff format --check .` / `mypy` / `pytest` (full suite, 262
  passed) all clean; `pre-commit run --all-files` clean.

## Work item 6 — Expose the cover-page share count (code, cross-repo origin)

**Status: closed 2026-10-02 (task moved to `CHANGELOG.md`). `T-043` built and live-verified, PR #48.**

**Why**: `portfolio-financial-analysis`'s `T-132(a)` needs a point-in-time share count to
compute market cap, and several names (PG, XOM, PM, NEE, HUM) have no share concept
stored downstream. The filing's own cover page states it
(`dei:EntityCommonStockSharesOutstanding`, e.g. PG ~2.32B outstanding — not the ~4.0B
`CommonStockSharesIssued`, which includes treasury shares).

**Approach**:

1. `cover_shares_outstanding(xbrl)` in `src/sec_edgar/agent.py` reads
   `xbrl.facts.query().by_concept("dei:EntityCommonStockSharesOutstanding", exact=True)
   .execute()` from the already-loaded filing — T-042's mechanism, no extra network call.
   Each filed fact becomes `{"value", "as_of_date", "class_member"}`: `as_of_date` is the
   fact's own `period_instant` (the cover page's "as of" date, usually after the period
   end; returned exactly), `class_member` is `null` for a non-dimensional fact, else the
   `us-gaap:StatementClassOfStockAxis` member. Multi-class filers get one entry per class
   plus a non-dimensional total only if actually filed — never one summed here. Nothing
   filed → `[]`, never a guess. Entries are sorted deterministically (total first, then
   by member name).
2. Only a fact that is non-dimensional, or dimensioned **solely** by
   `us-gaap:StatementClassOfStockAxis`, counts. Live verification found NEE's filings
   also carry `1,000` shares under `dei:LegalEntityAxis` (Florida Power & Light, a
   co-registrant) — not a NEE share class, so any other dimension is excluded rather
   than mislabeled. A fact with no usable instant date or a non-finite/negative value is
   skipped, not guessed.
3. `_safe_cover_shares_outstanding` isolates a failed read, exactly as PR #46's
   `_safe_reconcile_with_filed_facts` does: `cover.shares_outstanding` comes back empty and
   `cover.error` carries `"<ExcType>: <message>"` (`null` on success), so "failed to read"
   is never indistinguishable from "filer filed none"; the response still succeeds. The
   failure is **not** put in `reconciliation_errors` (PR #48 review): that list means "a
   statement came back unvalidated", and `portfolio-financial-analysis` (PR #104) rejects
   the whole filing when it is non-empty. `get_financials` adds `data["cover"] =
   {"shares_outstanding": [...], "error": ...}` — purely additive.
4. Tests (hermetic, facts captured live into `tests/sec_edgar/fixtures/`), `SPEC.md`
   FR-004, `docs/modules/sec-edgar.md`.

**Acceptance criteria** (all met 2026-10-02, see `TASKS.md` `T-043` for the figures):

- Live, no mocks, latest 10-K and 10-Q: PG, XOM, PM, NEE, HUM, MCD, MSFT match SEC's
  `companyconcept` API (or, where it lags/has no rows, the filing's own cover page);
  multi-class GOOGL and BRK-B return one entry per class, checked against the filings'
  cover pages (`companyconcept` omits per-class dimensional facts).
- Hermetic tests: single class, multi-class, missing, forced read failure.
- `SPEC.md` FR-004 reconciled; `uv run ruff check .` / `ruff format --check .` / `mypy` /
  `pytest` (280 passed) clean.

## Work item 7 — Fix point-in-time universe history reconstruction anomalies & data patches (code + data, T-044)

**Status: closed 2026-10-09 (task moved to `CHANGELOG.md`). `T-044` built and verified.**

**Why**: Rebuilding the point-in-time S&P 500 universe from Wikipedia's "Historical components"
changes table revealed ticker anomalies (renames SATS/FLT/RE/FB, combination WRK/SW,
temporary spin-offs FTRE/PHIN, GOOG/GOOGL share-class swap), skipped rows for corporate restructurings
sharing the same ticker (21CF FOXA/FOX), and open intervals lacking valid_from fallback dates.
Additionally, the backfill guard falsely treated the pilot's 20-row `live_snapshot` universe.db
as "already backfilled".

**Approach**:
1. Add `Date added` fallback in `_reconstruct_intervals` clamped to table's earliest date.
2. In `_reconstruct_intervals`, process addition before removal so tickers reused across corporate
   entities (e.g. Fox Corporation replacing 21st Century Fox FOXA/FOX on 2019-03-19) resolve their
   live intervals before opening predecessor intervals.
3. Introduce versioned data patches in `src/data_mining/universe_patches.py` citing S&P DJI press
   releases with publication dates for missing renames, combinations, spin-offs, and replacements.
4. Update backfill guard to check `count_backfill_rows` (`source = 'wikipedia_changes_backfill'`).
5. Write backfilled history to `/workspaces/thesis/data/universe_history.db` without modifying
   `/workspaces/thesis/data/universe.db`.

**Acceptance criteria**:
- CPAY, ECHO, SW fall back to their roster `Date added` when unpatched.
- 21CF FOXA and FOX intervals are correctly reconstructed backward.
- Backfill guard distinguishes snapshot rows from backfill rows.
- All patch rows cite official source releases (SEC Form 8-K, S&P DJI, or company press releases) documenting each specific fact and date.
- All 96 historical constituents and predecessors overlapping 2022-01-01+ have verified 10-digit SEC EDGAR CIKs.
- Reconstructed member counts:
  - 2018-01-02: 503 exclusive (`valid_to > 2018-01-02`) / 504 inclusive (`valid_to >= 2018-01-02`, including BCR on its final day before HII replacement on 2018-01-03) against 505 expected (500 companies + 5 dual classes; TROW pre-2019 shortfall).
  - 2022-01-03: 505 against 505 expected (500 companies + 5 dual classes: GOOG/GOOGL, FOX/FOXA, NWS/NWSA, DISCA/DISCK, UA/UAA).
  - 2024-01-02: 503 against 503 expected (500 companies + 3 dual classes: GOOG/GOOGL, FOX/FOXA, NWS/NWSA).
  - 2026-10-01: 504 against 504 expected (503 baseline + transitional addition ahead of 2026-10-06 drop).
- Known residual documented: TROW's roster "Date added" is 2019-07-29, so TROW is missing prior to 2019-07-29 (outside consumer's 2022+ window).
- All quality gates (`ruff check`, `ruff format --check`, `mypy`, `pytest`) clean.

## Work item 8 — Add filer CIK and period of report to EDGAR filing-list rows (code, T-045)

**Status: in progress. `T-045` active.**

**Why**: Requested by `portfolio-financial-analysis` for its `T-149` (filing identity):
"Task: add the filer CIK and the period of report to the EDGAR filing-list rows. Requested by portfolio-financial-analysis for its T-149 (filing identity)."
Downstream analysis needs the filer CIK and period of report directly on filing-list items returned by `get_filings`, `get_filing_by_year`, `get_latest_filing`, and `search_filings` to unambiguously identify filings and companies (especially former constituents whose tickers may be delisted or reused).

**Approach**:
1. In `src/sec_edgar/agent.py` `_filing_to_dict`, add two fields:
   - `cik`: 10-digit zero-padded string (`str(cik).zfill(10)` or `f"{int(cik):010d}"` from `edgartools`' `EntityFiling.cik`), or `null` if missing.
   - `period_of_report`: "YYYY-MM-DD" string (from `edgartools`' `EntityFiling.period_of_report`), or `null` if absent/empty, handled safely without raising.
   `form`, `filing_date`, and `accession_number` remain unchanged (additive change).
2. Update docstrings of `get_filings`, `get_filing_by_year`, `get_latest_filing`, and `search_filings` to document the new response shape.
3. Update OpenAPI route descriptions in `apps/sec_edgar_api.py`, `SPEC.md` FR-004, and `docs/modules/sec-edgar.md` documenting that the `{ticker}` path parameter accepts either a ticker symbol or a CIK number with or without leading zeros, and documenting the new fields.
4. Add hermetic tests in `tests/sec_edgar/test_agent.py` covering CIK zero-padding (int and string), `period_of_report` handling (present, date, string, absent/empty/None), `filing_by_year` returning all 5 fields, and CIK path parameter resolution.

**Acceptance criteria**:
- `_filing_to_dict` outputs `cik` (10-digit zero-padded string) and `period_of_report` ("YYYY-MM-DD" or null) beside existing fields.
- CIK zero-padding handles int and unpadded string (e.g. 1326160 / "1326160" -> "0001326160").
- `period_of_report` handles date object and string, and returns null without raising when absent/empty/None.
- All routes using `_filing_to_dict` expose the new fields additively.
- `{ticker}` path parameter accepting both ticker and CIK documented across OpenAPI schemas and `SPEC.md`.
- All quality gates clean (`ruff check`, `ruff format --check`, `mypy`, `pytest`).

## Work item 9 — GICS sector/sub-industry for the S&P 500 leaver stints (code + data, T-046)

**Status: closed 2026-10-14 (PR #51, merged `9006cf6`); task moved to `CHANGELOG.md`.**

**Why**: `portfolio-financial-analysis` (its `T-153` part 3, blocking its `T-100`
full-universe run) needs the leaver stints classified. The delivered
`universe_history.db` (PR #49) has `gics_sector`/`gics_sub_industry` filled for the
503 `live_snapshot` rows and empty for all 391 `wikipedia_changes_backfill` rows, so
the 90 stints inside the backtest window (`valid_to >= 2022-01-01`, CIK with no open
stint) get no company type, no regulated-utility exemption, no REIT rules, and no
SECTOR score. Full request: `portfolio-financial-analysis`'s
`gamug/portfolio-financial-analysis/docs/upstream_requests/data_mining_leaver_gics.md`.

**Approach**:
1. New `src/data_mining/universe_gics.py`: read the "List of S&P 500 companies"
   revision list from the MediaWiki API (window from 2021-01-01, paged), fetch each
   candidate revision's content by revid, parse the constituents table **by header
   name** (the page's column set changed — 2022 revisions carry an extra "SEC filings"
   column, 2023+ do not, so the live positional parser can't be reused), and for each
   stint walk back from the newest revision at or before `valid_to` to the last one
   listing the CIK **and** symbol, bounded by `valid_from`. Labels are copied verbatim;
   a pre-2023-03-17 label is never translated.
2. Two additive `universe_membership` columns (`gics_revision_id`,
   `gics_revision_timestamp`), migrated with `portfolio_common.db.ensure_columns`
   **only on the builder's output copy** (`queries.migrate_gics_columns`), never on
   open — so a read path (the gateway's `/universe` route, which opens
   `$UNIVERSE_DB_PATH`) never ALTERs a sha1-tracked file. `queries.update_gics` writes
   them with a stint-keyed UPDATE (`symbol`+`cik`+`valid_from`+`valid_to`) —
   deliberately **not** through `write_intervals`/`_DB_COLUMNS`, so the fill never
   rewrites a row it does not fill and every other row keeps NULL citation columns;
   `_row_to_public_dict` reports the columns as `None` for an old-schema file.
3. `universe-gics` CLI subcommand: copies `--source` to `--out`, fills, prints the
   report as JSON. Refuses `--out == --source` and the names `universe_history.db`/
   `universe.db`; idempotent (an existing `--out` is the starting point, a filled stint
   is skipped, so a re-run on a filled file makes no network call and writes nothing);
   the output is built on a temporary sibling and swapped in only on success.
4. A **report-only** stability check compares each chosen label with the same
   CIK+symbol's row in the newest revision ≥30 days older. Every difference is surfaced
   for a human to accept or reject (a vandalised revision must not slip through), never
   silently trusted, and the chosen label is never changed by it.
5. Tests: `tests/data_mining/test_universe_gics.py` (hermetic — MockTransport for the
   MediaWiki API, monkeypatched fetches for the end-to-end fill, tmp-path DB).

**No constitution change** — no new dependency or provider (Wikipedia via `httpx`,
already used; the page is already scraped by `data_mining.portfolio`), so AI behavior
#10 does not apply. The builder self-identifies to Wikimedia with the real `NAME`/`EMAIL` env vars (placeholders are rejected)
and paces serial requests (Technological stock #6).

**Acceptance criteria**:
- All 90 stints carry `gics_sector`/`gics_sub_industry` plus a revision id/timestamp, or
  appear on the "could not be filled" list with a reason (and, where the CIK was found
  under another symbol notation, which).
- Re-running `universe-gics` on the filled file changes no row.
- The 503 `live_snapshot` rows and every non-target column are unchanged; the full-row
  diff vs the source touches only the four GICS columns, only on the 90 target rows.
- Opening an old-schema file through the read path leaves it byte-identical (sha1
  before == after); a parser drop raises instead of silently emptying a stint.
- Labels match the chosen revision verbatim.
- The PR gives the counts, the unfilled list, and the new file's sha1; the source's
  sha1 stays `fddbdccb71ab4243a0fb76b3027774474d352b99`.
- `uv run ruff check .` / `ruff format --check .` / `mypy` / `pytest` clean.

## Work item 10 — News collector: stop tagging articles with the wrong company (code + data, T-053–T-060)

**Status: closed 2026-10-16 (`T-053`–`T-060`; tasks moved to `CHANGELOG.md`). `T-053`–`T-055` done (rule and targets approved 2026-10-15); `T-056` done (`a`, `b`, `c`; rows discovered before T-056c are stored `accept` without a reason, the T-057 re-tag reclassifies them); `T-057` done (real re-tag run 2026-10-15, hand check passed); `T-058` done (hand-off posted on portfolio-nlp PR #102); `T-060` done (wall detector in the extractor and a per-domain audit); `T-059` done (close-out 2026-10-16); the matcher gap the real run exposed (at least 433 false rejects) is the successor Work item 12 / `T-062`, open and maintainer-scheduled, not part of this item's close-out; the maintainer confirmed the `T-056b` schema changes and the `T-057` quarantine shape (`articles.prev_fetch_status`, `fetch_status = 'quarantined'`) (constitution #10) on 2026-10-15; T-057's tooling is PR #61.**

**Why**: Requested by `portfolio-nlp` (its Work item 15, `T-128`; decided
2026-10-14; see `gamug/portfolio-nlp#102` — the cross-repo task IDs cited
below, `T-117`/`T-128`/`T-130`/`T-131`, are as written in that repo's plan on
that date). That repo's category stage labels 42.7% of 459,112 articles
`other`, and investigating why showed that a large share of the corpus is
attached to a company it is not about. Examples from `urls.db`
(`discovered_urls`): ticker `A` (Agilent) on
`cnbc.com/.../china-has-best-week-in-a-month.html`; `ON` (ON Semiconductor)
on `.../trusts-trump-on-trade-needs-quick-us-china-deal.html`; `A` on
`.../mortgages-are-now-a-banks-best-friend.html`. Nothing in those articles
concerns the company they are filed under.

*Root cause, confirmed in code (not inferred)*: `BaseConnector._company_pattern`
(`src/news_collector/connectors/base.py`) builds
`\b(<ticker>|<first word of company name>)\b`, case-insensitive. The base
`url_matches_company` is abstract; **seven** connectors override it and call
the pattern (`cnbc`, `ft`, `investing`, `nasdaq`, `seeking_alpha`,
`stocktwits`, `yahoo_finance`), and both discovery strategies
(`strategies/sitemap.py`, `strategies/ddg.py`) filter on the override. The
base class documents the rule as intended ("Matching on ticker OR
significant company name token is sufficient"). Three failure modes follow:

1. **Ticker that is an ordinary word.** A hyphen is a regex word boundary, so
   the slug `in-a-month` matches ticker `A`, `trusts-trump-on-trade` matches
   `ON`. Reproducing the rule on the 462,826 sitemap-discovered rows in
   `urls.db`: 49.8% match through the ticker alone, 48.7% through the company
   first word alone, 1.5% through both. The tickers `ON` (66,489 rows),
   `A` (56,121), `ARE` (21,117), `IT` (11,798), `TECH` (7,812), `HAS`
   (7,631), `NOW` (6,696) and `ALL` (4,804) are matched through the ticker
   alone in 97–100% of their rows — 182,468 rows between them.
2. **Company name reduced to a generic first word.** `Trade Desk` → `Trade`
   (TTD, 11,627 rows, 100% matched through that word alone), `Bank of America`
   → `Bank` (BAC, 10,201), `First Solar` → `First` (FSLR, 10,193),
   `Dollar General`/`Dollar Tree` → `Dollar` (~8,550 rows each; audited: DG
   8,554 and DLTR 8,564 name-only), `Best Buy` → `Best`, `Global Payments` → `Global`, `News Corp` → `News`. Name-token
   matches are not all wrong (`Apple`, `Amazon` are legitimate), so this
   mode's error rate was measured by T-054: **52% wrong** (see the T-054
   results below).
3. **Raw substring shortcut in four connectors.** `nasdaq`, `seeking_alpha`,
   `stocktwits` and `yahoo_finance` first run
   `if ticker.lower() in url.lower(): return True` — no word boundary at all.
   Ticker `A` matches any URL containing the letter "a"; `ON` matches
   "season" or "reason". This is looser than mode 1 and a change to
   `_company_pattern` alone does not touch it. Its share of the corpus is
   small in what is stored (T-053: 823 rows, all `ddg`) and it is wrong 89% of
   the time (T-054, 19 of 20 sampled); its reach on a sitemap run of the other
   connectors is unmeasured.

**T-053 audit results** (`scripts/audit_ticker_tagging.py`, read-only, reproduces
every figure below from `urls.db`; 481,332 `discovered_urls` rows, 0 rejected by
the current rule). Word-boundary matches per `(domain, source)`:

| domain / source | rows | both | ticker only | name only | substring shortcut only |
|---|---:|---:|---:|---:|---:|
| cnbc.com / sitemap | 462,826 | 6,888 | 230,448 (49.8%) | 225,490 (48.7%) | n/a |
| cnbc.com / ddg | 2,218 | 599 | 856 | 763 | n/a |
| seekingalpha.com / ddg | 10,530 | 4,881 | 806 | 4,094 | 749 (7.1%) |
| finance.yahoo.com / ddg | 4,100 | 2,169 | 1,523 | 338 | 70 (1.7%) |
| ft.com / ddg | 776 | 108 | 195 | 473 | n/a |
| investing.com / ddg | 723 | 60 | 79 | 584 | n/a |
| nasdaq.com / ddg | 158 | 79 | 55 | 21 | 3 |
| stocktwits.com / ddg | 1 | 0 | 0 | 0 | 1 |

The earlier figures reproduce exactly (`ON` 66,489 ticker-only rows, `A`
56,121, `ARE` 21,117, `IT` 11,798, `TECH` 7,812, `HAS` 7,631, `NOW` 6,696,
`ALL` 4,804). Findings that change the picture: (a) the problem is almost
entirely `cnbc.com` sitemap rows (96.2% of the corpus); (b) the substring
shortcut (failure mode 3) accounts for 823 stored rows (0.17%), all `ddg`,
because only the shortcut connectors' `ddg` rows exist, but its *potential*
reach is larger for any future sitemap run on those connectors, so T-056a still
removes it; (c) the audit counts only rows that were stored, so it cannot see
candidates the rule rejected. Generic-first-word companies matched by the name
token alone in over 90% of rows are listed by the script (`Dollar`, `American`,
`Trade`, `Bank`, `First`, `News`, `Best`, `Global`, …); legitimate brand tokens
(`Apple`, `Amazon`, `Microsoft`, `Netflix`) also appear there, so that list is
a candidate set for T-054, not a verdict.

**T-054 measured error rates** (`data/ticker_tagging_sample.csv`, 263 pairs,
labeled by hand by the maintainer; `scripts/ticker_tagging_sample.py report`).
Seeded stratified sample over `(domain, source, match mode)`, with `ticker_only`
split into English-word and other tickers, plus 20 known-correct candidates
(company's full normalized name in the URL or title). A pair is `yes` when the
tagged company is a subject of the article (named and discussed; a roundup
section counts; an acronym counts), `no` when it is another company, a homonym
or a passing mention. Labeling is done in this repo and does not depend on
`portfolio-nlp`. An AI assistant's first-pass labels (kept in
`assistant_label`) agreed with the hand labels on 240 of 263 pairs (91%); the
hand labels are authoritative. For `ft.com` rows the body is a bot-challenge
page, so those were labeled from the title.

| match mode | n | wrong | wrong rate (95%) |
|---|---:|---:|---:|
| both | 56 | 1 | 0.1% (0–10%) |
| name token only | 63 | 8 | 52% (28–76%) |
| ticker only | 104 | 60 | 94% (85–100%) |
| substring shortcut only | 20 | 19 | 89% (66–100%) |
| whole corpus | 243 | 88 | **70% (58–82%)** |

By connector: `cnbc.com` 72% (60–85%), `nasdaq.com` 14%, `seekingalpha.com` 8%,
`ft.com` 7%, `investing.com` 3%, `finance.yahoo.com` 2% (the small ones have
intervals reaching 0%). **By discovery path** (the maintainer's observation that
`ddg` searches by the company, so the query is already evidence): `ddg` rows
(4% of the corpus) are wrong **7%** of the time (1–13%), `sitemap` rows (96%)
**73%** (60–85%). The `sitemap` strategy downloads every article URL of the
site regardless of company and then tests each of the ~500 companies against
each URL, so it has no query to lean on and the ticker-or-first-word filter is
the only evidence; `cnbc` is 98.9% of the 459,112 `fetch_status = 'ok'` rows
`portfolio-nlp` reads, so that path is almost all of what it sees. Inside `ddg`
the company-name queries are reliable (name-only 0 wrong of 48, both 1 of 48)
but queries by a bare English-word ticker are not (`site:cnbc.com A 2024`: 30
of 33 wrong), and the same loose filter is applied on top of the search
results. `urls.db` records `source` but not which query found a row. Other
findings that shape `T-055`:

- A ticker-only match is wrong 94% of the time, and 45 of the 48 English-word
  ticker pairs were wrong: a bare ticker should never be sufficient.
- A name-token-only match is right about half the time, all of it in the
  `cnbc` sitemap path (8 wrong of 15); every `ddg` name-only group was clean.
- The substring shortcut is wrong 89% of the time but covers only 823 stored
  rows.
- Known-correct candidates retained 17 of 20 (85%). The misses were `News`
  (a lone generic word), a `Deere` mention that is a person's name in the
  hand-label note, and an `MGM` pair whose note and label disagree. The maintainer
  reviewed and approved both labels as they stand (`MGM` id 317362 and `DE`
  id 180987, 2026-10-15), so 17 of 20 is the figure of record; had both flipped
  to `yes` it would be 19 of 20.
- The sample is small (8 per small stratum) and the intervals are wide; use the
  rates as the evidence for choosing a rule, and verify the implemented rule on a fresh
  sample (the T-057 check), not on these pairs, which are in-sample by now.

**Hand-label policy decisions** (from the maintainer's notes): an acronym
(`UPS`, `IBM`) counts as the company; a "stocks making the biggest moves"
roundup counts as `yes` for each company with its own section, so one URL may
legitimately carry several tickers (to be marked `multi_company` for
`portfolio-nlp`, which weights sentiment by entity); an interview or transcript
counts if the company is its subject even if it is not financial news.

**Prototype evidence for `T-055`** (explorative, in-sample, rules written after
reading the errors, so optimistic; not a measurement of `T-056a`; reproduce with
`scripts/score_ticker_rules.py`, committed with a test). Scoring a
rule on the 263 hand labels (kept-correct / kept-wrong / precision / recall of
the `yes` pairs):

| rule | kept-correct | kept-wrong | precision | recall |
|---|---:|---:|---:|---:|
| `current (accepts all)` | 172 | 91 | 65% | 100% |
| `name in URL/title, or explicit ticker`: full normalized name in URL/title, or explicit ticker form (`(NYSE: X)`, `$X`) | 122 | 5 | 96% | 71% |
| `above, or name twice in body` | 127 | 5 | 96% | 74% |
| `above, or ticker/first word in title`: an upper-case ticker (3+ letters, not a common word) in the title, or a distinctive capitalized first word in the title | 164 | 6 | 96% | 95% |

The two disputed-looking labels (`MGM` 317362, `DE` 180987) were approved as
labeled `no`; for sensitivity, leaving them out (`--exclude-ids 317362 180987`)
the last rule keeps 4 (not 6) wrong pairs, precision 98%, and flipping both to
`yes` would give the same 4. On the
`ok`-only rows `portfolio-nlp` sees, the last rule keeps 79 of 80 correct
and 5 of 61 wrong pairs. Missed or kept wrongly: the remaining wrong ones are a
phrase ("the next Amazon"), a surname (`Deere`), a word (`news`, `Royal`) and the
`MGM` and `DE` pairs approved as `no`. This shows the current rule's problem is the lack of
any evidence beyond a word match, not an inherently hard task: a handful of
deterministic checks on the title and body recover most of it.

**Duplicate analysis and quality state** (checked 2026-10-15, read-only): the
481,332 `discovered_urls` rows are only **320,763 distinct URLs** (212,450 URLs
carry one ticker, 108,313 carry two or more, up to 8); the 459,112 `ok` articles
hold only **302,512 distinct bodies**, so about a third of what `portfolio-nlp`
processed is the same article analysed once per tagged ticker. The login-wall,
bot-challenge and empty bodies seen in the labeling sheet are all rows with
`fetch_status` `failed` (11,114) or `thin_content` (11,106): none of the 11
`ok` rows that match wall phrases in their first 500 characters is a real wall
(manual check), and `nlp.db` holds exactly the 459,112 `ok` articles (zero
`failed`/`thin_content`), so no wall has propagated. The `ft.com`,
`investing.com`, `nasdaq.com` and `stocktwits.com` rows are 100% non-`ok` (no
article body at all). The remaining risk is a wall page that passes as `ok`
because only the length is checked; `T-060` closed it (the extractor now stores a wall as `failed` (challenge) or `paywalled` (login prompt), and `scripts/audit_article_quality.py` audits the `ok` rows).

Counts are from the local `urls.db` copy as of 2026-10-14. The 30 articles
hand-read in `portfolio-nlp` suggested roughly two thirds were not about the
tagged company; that is a small-sample estimate, not a measurement.

**Approach** (measure first; nothing is deleted before a dry run):

1. **Measure.** Commit a rerunnable audit (`scripts/`, read-only) that
   reproduces the counts above from `urls.db` per `(ticker, connector,
   strategy/source)`, splitting ticker word-boundary / ticker substring
   shortcut / name-token-only / both matches, and also covers the `ddg`
   strategy (18,506 rows) not broken out above. Estimate the error rate of
   each mode on **one shared labeled sample** (stratified by connector and
   those match modes, labeled here by hand, with no dependency on
   `portfolio-nlp`; reused by that repo's own measurement and by this item's
   retention check), so the fix is judged on evidence and nothing is labeled
   twice.
2. **Design the stricter rule** (decision recorded in T-055 before coding).
   **Decided 2026-10-14 (maintainer): split the work by what each side can
   see.** Discovery sees only the URL (the 462k `cnbc` sitemap rows have no
   title), so this repo applies only the cheap, deterministic strict rules below
   (it also saves fetching bodies for obviously wrong pairs); the semantic
   question "is the company a subject of the article" is answered by
   `portfolio-nlp` from the body and its NER organization spans, and this repo
   keeps the questionable pairs marked (quarantine, step 4) so that stage only
   judges those. Rejected: fixing everything from the slug alone (precision
   capped by a hand-kept stoplist) and moving everything to `portfolio-nlp`
   (wrong rows stay in `urls.db`, ~450k bodies fetched for mostly wrong pairs).
   The numeric targets are still set in T-055 from the hand labels. The
   options below are the discovery-side parts of that split:
   - **Provenance for `ddg`**: treat a hit returned for a *company-name* query
     as evidence for that company (it is where the search engine did the
     matching), stop issuing bare-ticker queries when the ticker is an ordinary
     word, and record the query that found each row (new column, additive,
     migrated like `gics_*`) so later steps can weight rows by how they were
     found.
   - **Attribute once per article for `sitemap`**: the URL set is
     company-independent, so decide for each article which company (if any) it
     is about, from the full normalized name, explicit ticker forms, and the
     title/body when fetched, instead of testing every company against every
     URL. This is where ~96% of the corpus and ~all the wrong tags are.
   **Design v1, approved 2026-10-15 by the maintainer ("take care not to
exclude correct pairs") and revised the same day after the validation sample
(T-055; nothing is coded).** Discovery can only see the URL and, for `ddg`
rows, the title (the 462k `cnbc` sitemap rows have no title at discovery; a
title exists only after the article is fetched), so each `(url, ticker)` pair
is put in one of three classes from those two texts
(`scripts/score_ticker_rules.py`, function `design_class`):

- **accept**: the full normalized company name (digit-bearing names such as
  `3M` included; `&` read as `and`; a name that is itself an ordinary word, such
  as `News` for News Corp, never counts alone) or a hand-kept alias (`Google`
  for Alphabet, `P&G`, `Facebook` for Meta) is in the URL or title, or an
  explicit ticker form (`$X`, `(NYSE: X)`, `NASDAQ: X`; for an ordinary-word ticker or an
  abbreviation only the exchange forms, its `$X` is `review`), or the ticker as an
  upper-case token of 2+ letters in the title that is not an ordinary word or
  common abbreviation (`GM`, `MSFT`; never `A`, `ON`, `IT`, `PM`, `MS`). A bare
  ticker never counts against a lowercase URL slug and never as a raw substring.
- **review**: only a weaker sign (a non-common ticker or a distinctive first
  word somewhere in the URL/title; a stoplist first word does not count), or a
  2-letter abbreviation-like ticker (`PM`, `HD`, `MS`, ...) in upper case in the
  title, which can never be `accept` alone but is never excluded either.
- **reject**: nothing else.

**Out-of-sample result first (validation sample, 100 pairs not used to design
the rule, strata `(class, source)` as in `data/ticker_validation_sample.csv` /
`data/ticker_validation_manual.csv`, hand-labeled 2026-10-15; `ticker_tagging_sample.py report --sample
data/ticker_validation_sample.csv`).** Design v1 as first written did **not**
meet its draft 15% target: `accept` was **20.7%** wrong (6/40 raw; 5 were `News
Corp`, 1 was `PM`), `review` 45% (`ddg` 5/10 wrong: `ICE`, `CNP`, `GEN`, `BRO`)
and `reject` 99.9% wrong (28 of 30; the two correct ones were `S&P Global` and
`Flavors & Fragrances`, whose URLs spell "&" as "and"). Four changes follow
from it: a normalized name that is an ordinary word never counts alone; the
2-letter common-abbreviation list grows (`PM`, `MS`, `MA`, `ED`, `EL`, `IP`,
`CI`, `HD`, `PH`, `NI`, `DD`); `&` is read as `and`; the proposed `ddg`
promotion of `review` to `accept` is **withdrawn** (5 of 10 `ddg` review rows
were wrong, against 11 of 11 under the first draft in the first sample (12 of 12
after the fixes), so it is not reliable).
Aliases are part of the rule and tested; no new dependency.

**Re-scored after the changes: both samples are now in-sample** (the rule was
tuned on the first sample's errors and then on the validation sample's), so the
figures below are optimistic and are not the acceptance evidence; the
acceptance evidence is the fresh check in T-057 (below). Reproduce with
`PYTHONPATH=src python scripts/score_ticker_rules.py --sample <csv>` (the **current**
rule, post-stratified on the drawn strata). Note that
`ticker_tagging_sample.py report --sample data/ticker_validation_sample.csv`
reproduces something different: that CSV's `match_mode`/`stratum` columns hold the
class the **first draft** assigned when the sample was drawn, so `report` gives the
out-of-sample first-draft result quoted above (20.7% / 45% / 99.9%), not the revised
rule's figures (the column keeps its name because `report`, `manual-export` and the
first sample share one schema):

| sample, mode | `accept` share / wrong | `review` share / wrong | `reject` share / wrong | correct pairs hidden as `reject` |
|---|---|---|---|---|
| first (243 + 20), re-tag (fetched title) | 28.8% / 10.9% | 12.1% / 64.4% | 59.2% / 100% | 0.0% |
| first, **discovery** (sitemap URL only) | 25.3% / 12.3% | 15.5% / 50.2% | 59.2% / 100% | 0.0% |
| validation (100), re-tag | 20.2% / 0.0% | 10.9% / 52.5% | 68.9% / 100% | 0.0% |
| validation, **discovery** | 14.3% / 0.0% | 16.0% / 30.6% | 69.7% / 100% | 0.0% |

Raw cells (correct / wrong), first sample re-tag: `ddg` accept 125/1, review 12/0,
reject 1/58; `sitemap` accept 13/1, review 4/5, reject 0/23. Validation re-tag:
`ddg` accept 18/0, review 4/5, reject 0/13; `sitemap` accept 21/0, review 9/10,
reject 0/20. Today 70% of tagged pairs are wrong. **At discovery the `accept`
share of `sitemap` rows drops (their titles are unknown) and the difference moves
to `review`, not to `reject`**, which is the cost the maintainer asked us to take
instead of excluding. **Exact class sizes of the current rule over the whole corpus**
(`score_ticker_rules.py --sizes`, 481,332 rows, read-only). Re-tag mode (fetched
title): `accept` 104,614 (21.7%; `ddg` 16,264, `sitemap` 88,350), `review` 51,295
(10.7%), `reject` 325,423 (67.6%). Discovery mode (no title for `sitemap` rows):
`accept` 86,985 (18.1%), `review` 63,162 (13.1%), `reject` 331,185 (68.8%).
(Requiring an exchange in the parenthesised ticker form moved 13 rows, from `accept` to `review`/`reject`.)
**Known limitation, measured** (`--names`): 59,956 `accept` rows (57% of re-tag `accept`) rest on a
one-word company name alone, across 186 companies, led by Apple 9,890, Amazon 6,390, Microsoft 3,179,
Nvidia 2,800 and Boeing 2,408. Full list (`--names --top 186`, re-tag mode): the dictionary-word names (Apple 9,890,
Oracle 741, Visa 509, Carnival 236, Booking 207, Waters 164, Ball 145, Mosaic 105, Progressive 87,
Flex 81, Dover 67, Hartford 59, Coherent 46, Everest 38, Tapestry 19, Block 18) add up to about
12,400 rows, 21% of those 59,956 (Apple alone is 80% of them); the rest are distinctive brands. Apple, Oracle and
Visa are mostly the company in finance news, but a name like `visa-bulletin` for Visa is accepted wrongly. The rule is not
changed without data; the T-057 fresh check decides.
`review` is the volume handed to `portfolio-nlp` T-132: about 51,000 pairs on the
existing corpus (63,000 if the rule ran on bare URLs). The first draft had
117,900 / 52,088 / 311,344. The `reject` class was 28 of 30 wrong, so "do not exclude"
cost about 2 in 30 correct pairs with the first draft and 0 of 33 after the `&`
and ordinary-word fixes (in-sample).

**Corpus-wide effect of the last two changes** (`score_ticker_rules.py --diff`,
whole `urls.db`, 481,332 rows, read-only): matching the URL host used to accept
`Nasdaq, Inc.` on any `nasdaq.com` URL; **0 stored rows are affected**
(the corpus does hold 158 `ddg` rows for `nasdaq.com`, but none is tagged NDAQ;
NDAQ's 34 rows come from `cnbc`, `yahoo` and `seekingalpha`), so no figure above
changes, but an NDAQ crawl of `nasdaq.com` would have hit it. The 2-letter abbreviation list moves **1,239 rows from `accept` to
`review` and none to `reject`** (`PM` 879, `HD` 109, `MS` 67, `IP` 60, `DD` 42,
`CI` 25, `EL` 21, `MA` 17, `ED` 10, `NI` 8, `PH` 1): an upper-case abbreviation
in the title is `review`, judged downstream, never excluded. Entries without a
labeled error (all but `PM`) are kept for that reason: the cost of a wrong entry
is a `review` pair, not a hidden one.
The alias map (`--diff`, "no alias map") moves **2,782 rows to `accept`**: `JPM`
2,122 (`jpmorgan`), `BRK.B` 353 (`berkshire`), `PG` 196 (`P&G`, the only ones
that were `reject`), `META` 89 (`facebook`), `GOOG` 12, `GOOGL` 9, `WMT` 1. The
`KO` alias `coke` was removed (also a fuel and a steel input; a test locks it).
`berkshire` is the least specific alias left; it is kept because 353 rows is
small and `portfolio-nlp` sees the article body, and is revisited by the T-057
fresh check.

**Discovery versus fetch.** The stored class is computed at discovery (URL, plus
the title for `ddg`) and **re-evaluated after the fetch** with the stored title.
The rule is monotone, since more text can only add evidence, so re-evaluation can
only promote and two code paths cannot disagree by demoting. It applies to fetched
(`accept`/`review`) rows: this is why 63,162 discovery-time `review` pairs fall to
51,295 in the existing corpus, where every row was fetched. Going forward a
`reject` row is written `status = 'skipped'` (with `match_class = 'reject'`) and never fetched, so a `sitemap`
`reject` row stays `reject` unless a feed title came with it; about 5,800 pairs
that would improve with a title are therefore not fetched (accepted: 0.0% of
correct pairs hidden in discovery mode on both samples, re-measured in T-057).

**Quarantine** means the pair stays in `urls.db`, is flagged with its class and
reason, and is left out of the default `articles` selection that
`portfolio-nlp` reads (`fetch_status = 'ok'`, non-empty `body_text`); undoing it
is clearing the flag. The flag is `articles.fetch_status = 'quarantined'` with the previous status kept in the new
nullable `articles.prev_fetch_status` (schema version 3; a shared-schema change confirmed 2026-10-15 and recorded in
`portfolio-nlp` PR #102, T-132/T-128), so clearing it restores the row exactly and `discovered_urls` is never touched.
The state stays in sync by itself: `extractor.db.record_fetch` quarantines a non-`accept` article after every fetch and
releases it on a promotion, and a rediscovery that promotes a fetched pair releases its article too. The re-tag
(`scripts/retag_urls.py`, T-057) is gated: a labeled sheet that passes the 60/40 verdict and is validated as the fresh
sheet drawn under the current rule, a backup, the collector and crawler stopped (it aborts before writing if the corpus
moved), and a post-apply verification against the dry-run plan. Nothing is deleted; `reject` is quarantined with a reason,
`review` is quarantined and decided by `portfolio-nlp` (its `T-132`, which keeps
any pair it cannot decide).

What happens to each class (the cross-repo shape is agreed in `T-058` before the
re-tag runs): `accept` rows stay as they are; `review` rows are quarantined and
sent to the body/NER judge; `reject` rows are quarantined with a reason, so the
re-tag is reversible. For `ddg`, record the query that found each row (a new
additive column, `T-056b`) as provenance for later analysis only; it does not
change a row's class. For `sitemap`, the same article is stored once per ticker
that discovered it (212,450 URLs carry one ticker and 108,313 carry two or more,
up to 8), and the class is evaluated per pair. Deciding each article once across
all companies was considered and **not adopted**: the class stays per `(url, ticker)`
pair, and `multi_company` is derived (a URL with two or more `accept` pairs;
roundups are legitimate), decided in `T-056c`. Neither was measured separately here;
the class figures are per pair.

**Targets (written into FR-014 on 2026-10-15 as the requirement; the first draft
missed them out-of-sample and the revised rule is verified only by the fresh check
below, in T-057):** wrong-company rate among `accept` rows at most
**15%**; correct pairs kept as `accept` or `review` at least **95%**; known-correct
pairs labeled `yes` classed `accept` at least **90%** (discovery mode counted
separately, where 16 of 17 pass); no `accept` row whose only evidence is an
ordinary-word ticker, a stoplist first word or a raw substring (hermetic tests per
connector). **Final check (T-057 dry run, before the real re-tag), with a pass criterion:**
the maintainer hand-labels **60 pairs newly classed `accept`** by the implemented
rule (drawn after the rule is frozen, stratified by source like the corpus) and
**40 `reject`** ones. Pass: at most **5 of the 60 `accept` pairs wrong** (observed
8.3%; the 95% Wilson upper bound is then about 18%, so it supports the 15% target
only with the earlier samples behind it, and 0-2 wrong is a clear pass) and at most
**1 of the 40 `reject` pairs correct** (hidden-correct 2.5%). Fail: stop and return
to T-055 with the new errors, as was done after the validation sample; the real
re-tag does not run on a failed check.

Direction for the per-company matcher, to be confirmed against step 1's data:
   - A bare ticker never counts when it is an English word or a very short
     symbol, and never counts against a lowercase URL slug at all; it counts
     only in an explicit form (`$X`, `(NYSE: X)`, `NASDAQ:X`) or as an
     uppercase token in the title.
   - The company name counts as the **full normalized name** (corporate
     suffixes stripped, hyphens treated as spaces), plus a small curated alias
     list for brand names ("Meta" for Meta Platforms, "Berkshire" for
     Berkshire Hathaway, "Alphabet"/"Google"); a first word alone counts only
     when it is distinctive (not on a common-word stoplist). Adding a
     word-frequency library would be a constitution-level dependency change
     (Technological stock #7) — prefer a hand-kept stoplist.
   - Recall matters too: the rule must keep what was correctly tagged. Report
     the retained share on a hand-labeled set of known-correct pairs.
3. **Implement** (tasks `T-056a` matcher and connectors, `T-056b` storage/queue/API, `T-056c`
   strategies/fetch/re-evaluation, in that order) the approved three-class rule once, in a shared pure module
   in `data_mining/matching.py` (shared by both stages, which never import each
   other), exposed by one new concrete `BaseConnector.classify_match` returning
   `accept`/`review`/`reject`; the seven overriding connectors' abstract
   `url_matches_company` keeps its signature and returns `class != "reject"`, the
   substring shortcut in `nasdaq`, `seeking_alpha`, `stocktwits` and
   `yahoo_finance` is removed, and the docstring postcondition that blesses
   "ticker OR first word" is rewritten. `discovered_urls` gains additive
   `match_class`, `match_reason` and `query` columns (`SCHEMA_VERSION` 1 → 2, a
   `run_migrations` step with `ensure_columns`, an index on `match_class`), the
   `ddg` and `sitemap` strategies keep `review`/`reject` rows with their class
   instead of dropping them (so new crawls have the quarantine), `reject` rows
   are written `status = 'skipped' AND match_class = 'reject'` (the existing
   `skipped` meaning, paywall/irrelevant after inspection, is kept distinct by
   `match_class`; `mark_skipped` is not used and `title` is preserved) so the
   fetcher and queue, which select by `status`, never fetch them, and
   `accept`/`review` rows stay `pending`; `/discovered` exposes `match_class`.
   The queue's insert becomes an upsert (an `INSERT OR IGNORE` would drop a
   promotion when a `reject` pair is rediscovered with a title): on conflict it keeps
   the higher class and resets `skipped` to `pending` when the class rises. `api`
   feeds (`stocktwits`, `yahoo_finance`) are ticker-keyed, were never measured and keep
   `accept` by construction; `rss` entries are classified. The extractor re-evaluates
   a fetched row's class through its existing `discovered_urls` update (promote only). This is a shared-schema change under
   constitution AI behavior #10: the maintainer confirms the exact columns and the
   `skipped`+`reject` convention before T-056b starts. The class is computed at
   discovery from the URL (and the `ddg` title) and re-evaluated after the fetch
   with the stored title; the rule is monotone, so re-evaluation only promotes.
   `multi_company` is derived (a URL with 2+ `accept` pairs), not stored.
   Hermetic parametrized tests **per connector** for all three classes, the
   monotone-promotion test and the migration test (details in `TASKS.md` T-056a–c);
   only `test_cnbc.py` covers `url_matches_company` today and asserts the
   permissive rule, so it changes deliberately and the other six connectors get
   test files.
4. **Re-tag the existing data (`urls.db`).** Apply the new rule to
   `discovered_urls` / `articles` (481,332 rows each) as a dry run on a copy
   first, reporting how many `(url, ticker)` pairs would be removed, per
   ticker. Pairs are unique on `(url, ticker)`, so a URL tagged to the wrong
   company may also be tagged to the right one by another ticker's discovery;
   count that case. Prefer a reversible quarantine over a delete. Whatever
   the mechanism, **`portfolio-nlp` reads `articles` and only filters on
   `fetch_status = 'ok'` plus a non-empty `body_text`**, so the chosen
   representation must make wrong-company rows disappear from what it
   selects, not merely carry a flag it does not read; agree the shape with
   that repo before running the patch (the ticker linkage is the cross-repo
   contract here).
5. **Hand off.** Tell `portfolio-nlp` when the corrected corpus is available,
   together with the list of article ids the re-tag removed (that repo has to
   clean its existing results for them, `sector_summary` included):
   its Work item 15 development (the relevance gate, calibration, and the
   full re-classification) starts after this item, and its measurements are
   re-taken on the cleaned corpus.

**Acceptance criteria**:

- The audit reproduces the counts above from `urls.db`, and the error rate of
  both failure modes is measured (not assumed) before the rule is chosen.
- The new matcher classes the documented examples (`A` on `in-a-month`, `ON`
  on `trusts-trump-on-trade`, `Bank`/`Trade`/`First`/`News` as sole evidence, and
  a ticker as a bare substring such as `A` in any URL) as `reject` or `review`,
  never `accept`, **in each of the seven connectors**; `accept` meets the FR-014
  targets (wrong rate at most 15%, correct pairs kept as `accept`/`review` at
  least 95%, known-correct `accept` at least 90%) on the labeled samples; **the T-057
  fresh check passed its pass/fail criterion** (2026-10-15: 1 of 60 `accept` wrong, 1 of 40
  `reject` correct, the limit; at most 5 and 1 were allowed), but 100 rows cannot confirm the
  percentage targets: the `reject` side's 95% interval is 0.4%-12.9%, so "correct pairs kept at
  least 95%" is neither confirmed nor refuted by it (see the accepted recall loss below; at least
  433 known false rejects); no abstract `BaseConnector` signature change.
- The re-tag dry run reports before/after counts per ticker and per source,
  and the real run leaves a restorable backup (done: `urls.pre-retag.db`).
- **Accepted recall loss (T-057 real run):** the frozen rule rejects some pairs whose full
  company name is in the URL, so they are quarantined, not deleted. **Three causes**
  (`_name_reason`, `matching.py`): (1) the name is short or an ordinary word once its suffix is
  removed (`KLA Corporation` becomes `kla`, under `MIN_NAME`; `Southern Company` becomes `southern`;
  `News Corp` becomes `news`, both in `STOPWORDS`), and the `weak_first_word` fallback has the same
  floor, so the pair is `reject`, not `review`; (2) a possessive, which URLs write as a trailing
  `s` (`bank-of-americas`, `dollar-generals`, `trade-desks`), so the whole-word match fails; (3) an
  `&` in the name that URLs drop (`International Flavors & Fragrances` is searched as `… and
  fragrances`). **Known (lower bound):** `scripts/known_false_rejects.py` scans the removed list for
  the name as whole words of the URL path (with and without suffix, `&`, trailing `s`) and finds
  **at least 433 pairs on 234 URLs** (`NWSA` 201 and `NWS` 199, the same News Corp articles under
  both tickers; `KLAC` 15; `SO` 11; `BAC` 4; `DG`, `IFF`, `TTD` 1 each), committed in
  `data/retag_known_false_rejects.csv` and pinned by `tests/scripts/test_known_false_rejects.py`.
  The 19 hits for `BBY`, `ON`, `T`, `TGT` were read and are not the company (`best-buys`,
  `on-semiconductors`, `at-t-mobiles`, `target-corporations`), so those article ids, and only
  those, are left out (`NOISE`). The hand-check row 302365 is one of the KLAC pairs, which is why the `reject` check
  passed at its limit. **Estimated:** the scan finds one shape only. The hand check had 1 of 40
  `reject` pairs correct (2.5%, Wilson 95% interval 0.4%-12.9%), i.e. roughly 1,400 to 42,000 of the
  325,423 (point estimate about 8,100); a 40-row sample cannot say more, so 433 is about a twentieth
  of the point estimate. The rule is not changed here (it would move every PLAN figure and the
  frozen samples); `portfolio-nlp` is told not to purge the known ones (T-058 comment). A matcher
  fix (for example `review` when the full normalized name is in the text, with a possessive and
  `&` normalization) is a follow-up task (`T-062`): it re-classes those rows with a targeted pass (the re-tag
  only touches rows with `match_reason IS NULL`) and `release_articles`. It is Work item 12 below, so this
  item closed at T-059 (2026-10-16) with a named successor.
- `portfolio-nlp` confirms wrong-company rows no longer reach its pending
  queries. *Status at close (2026-10-16): hand-off posted on `portfolio-nlp` #102 (`T-058`); their
  confirmation has not arrived. It is theirs to give (its `T-132`) and does not block this item.*
- `ruff check`, `ruff format --check`, `mypy`, `pytest` clean; `SPEC.md` §13
  item 10 and FR-014, `docs/modules/news-collector.md` and the architecture artifacts
  reconciled (constitution AI behavior #11, never renamed).

## Work item 11 — Fetch the Tiingo prices for the 33 leaver lines into a private local file (code + data, cross-repo origin, T-061)

**Status: closed 2026-10-16 (PR #62, merged); task moved to `CHANGELOG.md`.**

**Why**: `portfolio-financial-analysis` (its `T-100` full-universe run, raised by
`T-153` parts 2–3; request `gamug/portfolio-financial-analysis/docs/upstream_requests/data_mining_leaver_prices.md`)
has no candles for the 35 S&P 500 leaver lines that stopped trading inside the
backtest window. The gateway serves none of them (Finnhub's free tier then
yfinance keep no history for a delisted ticker), so those members drop out for
lack of data — exactly the survivorship bias `PER-11` exists to prevent. The Task
B feasibility report (2026-10-14) showed Tiingo's free EOD endpoint serves 33 of
the 35 (`INFO` and `WRK` are unobtainable). The user and reviewers decided
(2026-10-15) that the thesis uses the Tiingo prices **now, for the backtest
only**, on the condition that the data is **never redistributed**: this repo
fetches, `portfolio-financial-analysis` imports (its part 3d). The gateway is
**not** changed — Task B's plan to serve the lines through `GET /pricing` is
dropped.

**Approach**:
1. `src/data_mining/leaver_prices.py`: fetch Tiingo's daily EOD rows for the 33
   lines (symbols `SIVBQ`/`FRCB`/`DAY`/`WBD`; `CDAY`/`DISCA` are stubs) over
   `2019-01-02 .. latest valid_to` from `universe_history_gics.db` (read-only),
   apply the report's trim rule and split formula, and write a CSV + a JSON
   manifest to `/workspaces/thesis/data/private/tiingo/` (outside every git
   repository, 0700/0600, effective modes recorded). Reuses the feasibility
   code: trims (report §3), split formula (report §4), identity checks (§5).
2. `cli/pricing_cli.py leaver-prices` subcommand, like `universe-gics`; it never
   calls Finnhub and sends only read-only GETs to the shared gateway.
3. Port `portfolio-financial-analysis`'s rule-based NYSE calendar
   (`src/kg_schema/trading_calendar.py`, commit `87dd9bf…`) into
   `src/data_mining/nyse_calendar.py` for the "expected sessions" counts.
4. Prices live only in a private `expectations.json`; committed code/tests carry
   counts, dates and trim reasons only.
5. Constitution amendment (Technological stock #8, MINOR 1.8.0) and `SPEC.md`
   FR-015/§2.1/§11/§12 record the sanctioned one-off source.

**Acceptance criteria**:
- Every line's row count, first/last date, trim set **and reason class** and
  last real close equal report §2/§3; every row's date is an NYSE session; a
  non-zero gateway candle count stops the run (one vendor per line); a
  difference, or a `valid_to` that gives a different expected count, writes
  nothing and stops.
- The ported calendar reproduces report §2's expected session count for all 35
  lines (`tests/data_mining/test_nyse_calendar.py`).
- TTD/PVH/KMX match the gateway within tolerance; the AAPL dividend formula is
  reported; WBD matches production's 1,189 stored closes within one cent.
- ≤ 37 Tiingo requests, under the 50/hour cap; key only in `$TIINGO_API_KEY`.
- `/tmp/tiingo_feasibility/` deleted once the file passes; the four production
  DB sha1s unchanged.
- All quality gates clean (`ruff check`, `ruff format --check`, `mypy`, `pytest`).

## Work item 12 — Matcher: stop rejecting pairs whose URL spells the company out (code + data, T-062)

**Why:** the T-057 re-tag quarantined at least 433 `reject` pairs on 234 URLs (News Corp 400 as `NWS` + `NWSA`,
`KLAC` 15, `SO` 11, `BAC` 4, `DG`/`IFF`/`TTD` 1) whose URL carries the company's full name, and the hand check
estimates 2.5% of the whole `reject` class (95% interval 0.4%-12.9%). Causes, scope and the figures are in Work item 10
("Accepted recall loss"). **What:** a full-name hit is at least `review` (never `reject`), with the possessive `s` and
`&` normalized; the rows already rejected are re-classed with an audited targeted pass (the re-tag only touches
`match_reason IS NULL` rows). **Acceptance:** the 433 rows are no longer `reject`, `score_ticker_rules.py --check` is
re-measured and the figures re-frozen, a fresh 60/40 sheet passes the T-057 criterion, the real run is rehearsed on a
same-mount copy and then done with a backup, and `portfolio-nlp` is told what changes for it. No schema change.

**Status: closed 2026-10-17** (scheduled by the maintainer 2026-10-16; found by the Work item 10 real run, 2026-10-15). The real run was done on 2026-10-17 on merged code (#66, #67, #69, #70) and its post-conditions held; the hand-off to `portfolio-nlp` is prepared (delivery pending with the maintainer).

- **Matcher.** `matching._loose_name` reads a full name the URL spells loosely as `review`/`loose_name`, never `reject`:
  the name with its corporate suffix kept, with the `and` of an `&` dropped, the brand of a camel-case name (`pepsi` for
  `PepsiCo`; only a capital `Co`/`Corp` tail is cut), a trailing `Corp` spelled out (`news-corporation`: 11 News Corp
  pairs on 8 URLs, 2026-10-17), and a slug's possessive `s` only for a name of two words or more outside
  `PLURAL_PHRASES` (`best buy`, `on semiconductor`, `target corporation`). A form needs two words or more, each of at
  least two characters, or one distinctive word, so `AT&T`'s `at t` never counts (22 real names with a one-letter word
  are pinned in a test). All 433 known false rejects are `review` (45 depend on the possessive form, 38 of them News
  Corp); `score_ticker_rules.py --check` is unchanged.
- **Tooling and guards.** `scripts/reclass_rejects.py`: dry run by default (status of the promoted rows, predicted sync
  and predicted change of the readable articles, top tickers, `api` rows); `--apply` needs a new `--backup` (checked:
  fingerprint, `quick_check`, SHA-256), refuses a `portfolio-common` older than 1.2.2 (before anything is written;
  the dry run warns) and a busy database, a full disk, an existing or unwritable audit list or
  summary, writes the audit list as `.partial` and publishes it only after the commit, and rolls back if the database
  fingerprint moved since the plan; after the update and the sync it reads the database back (rows per class, rows
  changed, none still `reject`, the sync equals the prediction, no `accept` pair holds a quarantined article, the `ok`
  articles changed by exactly the prediction), truncates the WAL and attaches the file read-only the way
  `portfolio-nlp` does (`Database.attach(read_only=True)`, `portfolio-common` 1.2.2); it writes a run summary JSON and
  prints the backup, list and summary paths absolute. `--revert LIST` validates its list before anything (columns,
  integer unique ids, each row a promotion, same ticker and url as the database) and touches only the listed pair. A
  failure prints the remedy that fits (data: how to undo; reader: checkpoint or `uv sync`, no revert). Each stage prints
  its duration. The whole sequence was rehearsed on a same-mount copy with `scripts/rehearse_reclass.py` (removed after the real run; see git history).
  `ticker_validation_sample.py --refresh` and `loose_name_precision.py` keep the hand-check sheets current.
- **Real-database evidence** (read-only, `urls.db`, 2026-10-17). Dry run: **448 promotions**, all `review`/`loose_name`,
  none `accept`: 448 of 448 promoted articles are already quarantined (before quarantine `ok` 417, `failed` 25,
  `thin_content` 6), none is `skipped`, `api` has no `reject` row, predicted sync 0 quarantined / 0 released, the `ok`
  articles stay 93,224 (News Corp 413 pairs: `NWSA` 207, `NWS` 206, on 210 distinct URLs; `KLAC` 16, `SO` 11, `BAC` 4,
  `DG`/`IFF`/`PEP`/`TTD` 1). Classes: `reject` 325,423 -> 324,975, `review` 51,295 -> 51,743, `accept` 104,614.
  Rehearsal with `rehearse_reclass.py` (removed 2026-10-18, see `4bfa812`) on a copy (2.3 GB) on the same mounted drive, commit `ff51985`,
  `portfolio-common` 1.2.2: 12 checks, all held. Apply **925.3 s** (fingerprint 46.9 s, plan 63.6 s, report 146.7 s,
  backup + `quick_check` + SHA-256 346.0 s, update + sync 120.8 s, verify 130.6 s), `-wal` 0 bytes afterwards; a second
  apply wrote nothing (581.3 s); revert **773.8 s**, `-wal` 0 bytes; the reverted copy equals the pristine one (`EXCEPT`
  both ways: `discovered_urls` [0, 0], `articles` [0, 0]); a second writer makes `--apply` refuse (`busy`, no backup, no
  list). Earlier rehearsals on the same mount took 697 s, 965 s and 1,030 s for the apply (the spread is the shared
  drive), so the runbook says 12 to 17 minutes and to plan for 20.
- **Hand check and precision.** `data/ticker_reclass_check_sample.csv` passes the T-057 criterion for the shipped rule
  (2 of 60 `accept` wrong, 0 of 40 `reject` correct, 1 unclear). `loose_name` precision, the maintainer's labels on
  distinct URLs: News Corp 10 of 10 (95% lower bound 72%; all 202 URLs contain the literal `news-corp`, the 201 slugs of
  the known file were read), the other 35 pairs 20 of 20 (lower bound 84%; the one earlier `BAC` `no` was confirmed
  `yes`). The 11 legal-name pairs (8 URLs) added on 2026-10-17 are in no hand sample yet: they are `review`, which is
  the doubt class, and the maintainer may label the 8 URLs.
- **What it changes for `portfolio-nlp`:** nothing it can read. A promoted pair is `review`, `review` stays quarantined
  and `portfolio-nlp` reads only `fetch_status = 'ok'`; the run checks that count (predicted change 0). What moves is
  the labels (`reject` -448, `review` +448), which the hand-off must say. **Decided by the maintainer on 2026-10-17**
  (Work item 13): the News Corp pairs stay `review` for the NER check, as do the other 35; nothing is released after the
  run.
- **Known gap** (read-only scan of 2026-10-16, on the rule of that day: 442 promotions, `reject` 324,981; the shipped
  rule promotes 448, so `reject` is **324,975** after the run, 325,423 before): 438 `reject` rows have a distinctive
  non-first word of the company name in the URL, almost all generic words (`service`, `estate`, `equities`), a handful
  real (`DIS` 3, `SCHW` 3, some `LYV`, `BAC`). **Re-measured on 2026-10-18** on the real `urls.db` after the run (read-only; the rule is a reconstruction of the 2026-10-16 description, not the original script, and it reproduces the count 438, the set was not compared: a word of the normalized name
  other than the first, 5+ characters, not in `STOPWORDS`, whole word of the URL path; top words `service` 82, `nation` 69,
  `estate` 50, `semiconductor` 40): still **438** pairs on 438 URLs (tickers `LYV` 77, `ARE` 74, `PEG` 67, `ON` 40, `A` 31,
  `UAL` 28), so the count did not change after the fix and the 11 legal-name pairs. Not
  fixed here, below the T-057 hand check's resolution.
- **Owner of the hand-read lists:** `matching.PLURAL_PHRASES` and the camel-tail names are re-scanned by whoever changes
  the company universe (a new two-word name that is also a plural noun is read as a spelling and lands in `review`,
  which is harmless for the quarantine but raises the false rate of `loose_name`). The re-scan is part of the
  universe-change task, and `loose_name_precision.py sample` re-measures the rate. The legal-name gap found in Work item
  13 (11 News Corp pairs on 8 URLs, `news-corporation` in the slug) is closed: the rule reads a trailing `Corp` spelled
  out, the pairs are `review`, the figures of this item are on 448.
- **Real run (2026-10-17, `urls.db`, collector and crawler stopped, `portfolio-common` 1.2.2, master `0c14a5a`).** Dry run **448 promotions** (all `review`/`loose_name`; `ok` 417, `failed` 25, `thin_content` 6 before quarantine; 448 of 448 articles already quarantined; predicted sync 0/0; 5 min). Apply 22 min 7 s on the shared drive (fingerprint 54.5 s, plan 77.6 s, report 168.0 s, backup + `quick_check` + SHA-256 402.6 s, update + sync 137.2 s, verify 150.9 s), backup `/workspaces/thesis/data/url_cat_backup.db` (SHA-256 `5cfb1b21…c8887`), audit list `data/retag/reclass_promoted.csv` (448 rows), summary `data/retag/reclass_apply_summary_20261017T232931362685Z.json`; post-conditions ok, rows changed 448, sync 0 quarantined / 0 released, WAL truncated, reader check `attach(read_only=True)` ok (481,332 pairs, 93,224 readable articles). Classes: `reject` 325,423 → **324,975**, `review` 51,295 → **51,743**, `accept` 104,614 unchanged, `loose_name` 448, `articles.fetch_status` unchanged (`ok` 93,224). Read back afterwards: no `accept` pair holds a quarantined article; `news-corporation` URLs are 13 `review` pairs; no `Coherent Corp.` or `Vistra Corp.` URL spells `corporation` yet. The first apply attempt of the same day wrote nothing: it refused because a leftover 2-row test file sat at the `--list` path (the script's refusal worked as designed; the file was moved to `data/retag/reclass_promoted.fixture-2026-10-16.csv`).
- **Closed:** the follow-ups are the `portfolio-nlp` NER check on the `review` class (Work item 13; not built) and the known gap in Work item 12 above (438 pairs, re-measured after the run; no owner yet, listed in `TASKS.md`). `SPEC.md` FR-014 matches (the doubt rule, and a loosely spelled full name is `review`, never `reject`). The architecture artifacts were reconciled in the close-out.
- **Scripts after the run:** `T-063` needs no script; `reclass_rejects.py` is kept for its `--revert` as the rollback of the run. `rehearse_reclass.py` and its test were removed after the real run (maintainer, 2026-10-18); `reclass_rejects.py` stays
  as the rollback and as the pattern for the NER apply-verdicts pass. The audit list, the run summary and the backup are the record.

## Work item 13 — The News Corp pairs wait for the NER check (decision, T-063 closed without code)

**Status: closed 2026-10-17 by the maintainer's decision (option B).** The maintainer's design rule: where the tag
confidence is in doubt, the pair stays `review` so that `portfolio-nlp` can take a deep look with NLP models and NER
(the feature is not built). `T-063` proposed releasing the News Corp pairs (`NWS`, `NWSA`) to `accept` after Work item
12's run; on the evidence below the maintainer chose to **hold them all for the NER check**. No alias, no script change,
no release: the News Corp pairs stay what `T-062` makes them.
**Decision record** (the rule itself is in `SPEC.md` FR-014).
- **Why not release:** the pairs are 413 of `T-062`'s 448 promotions (`NWS` 206, `NWSA` 207; 210 distinct URLs; 402
  `review` before the legal-name form, 11 added by it). Every URL has the literal `news-corp`, but the maintainer's hand
  sample is 10 of 10 with a 95% lower bound of 72%, and 42 URLs (84 pairs) also name another tagged company (roundups,
  the FOX merger stories, `dow-jones` stories). A developer read of 20 of the 168 uncontested URLs found 18 about News
  Corp and 2 multi-company lists (95% lower bound for 18 of 20: 70%).
- **What release would have changed** (measured read-only on 2026-10-17, the audit's own tests): the 413 News Corp pairs
  with the slug (402 `review` + 11 `reject` before the legal-name form, all `review` after it; 210 URLs) have no wall
  and no body under the floor of 80 words; 400 articles are `ok` before quarantine (200 distinct bodies, each stored for
  both share classes) and 13 would go back to `failed` (9) or `thin_content` (4). Releasing all would have made 400
  articles readable; the uncontested subset 314 (325 pairs). The readable set therefore stays as `T-062` leaves it:
  nothing moves for `portfolio-nlp`.
- **What the NER check inherits:** the 413 News Corp pairs here and the 35 others from `T-062`; quarantined bodies are
  kept. `GET /articles?fetch_status=quarantined` does not return the match class, so the check needs either a
  `match_class` filter on that endpoint or a read of `discovered_urls.match_class` through
  `Database.attach(read_only=True)`; that belongs to the NER work item and is not decided here. Until it exists,
  quarantine means "waiting for the check", not "excluded".
- **Closed 2026-10-17 (the maintainer's doubt rule):** the 11 News Corp pairs on 8 URLs that spell the legal name
  (`news-corporation` in the slug) were `reject`, out of reach of the NER check. `T-062` now reads a trailing `Corp`
  spelled out as a loose name (`matching._loose_name`), so they are `review` like the other 402: the run promotes 448,
  not 437. No other company changes (measured on the real `reject` rows).
- **Nothing to build here:** `known_false_rejects.py --check` stays at its numbers; `reclass_rejects.py` keeps its
  `reject` → `review` scope.

## Work item 14 — Collector and gateway: a discovery batch above SQLite's variable limit, the database path, the `.env` rule (code, `T-064`–`T-067`, `T-077`)

**Status: Closed 2026-10-19. `T-064` closed 2026-10-18 (PR #73; its step 5 was redefined as a read-only check, see `CHANGELOG.md`); `T-075` done in PR #75; `T-065` done in PR #79; `T-076` done in PR #78 (option: drop the dead `ft`/`nasdaq` roots, failures loud); `T-066a` done in PR #80; `T-066b` design in PR #82, code done 2026-10-18 in PR #83 (choice 11 on `config.py` answered 2026-10-18: remove the import-time read, done in `T-077`); `T-067` done in PR #81; `T-077` (the discovery end date is an input defaulting to today, UTC) designed with the maintainer 2026-10-18 in PR #84 (docs and constitution 2.0.1, merged), code done 2026-10-18 in PR #85 (added 2026-10-18; the maintainer schedules them).** Found by the `T-062` step 6 check: a
`KLAC` discovery through `POST /collector/discover/cnbc/` on the real `urls.db` returned `too many SQL variables` and
inserted nothing. `enqueue_batch` and `_count_new_rejected` (`src/news_collector/storage/queue.py`, from `T-056c`) bind
two variables per `(url, ticker)` pair in one `IN (...)`; SQLite 3.40.1 allows 32,766, so a batch above about 16,000 pairs
is lost. The cause is the likely one, not yet confirmed by the batch size of that call (first step of `T-064`).
**Fix:** chunk both lookups, same results, a loud failure instead of an error only in the response. **Acceptance and
test:** `CHANGELOG.md` `T-064`. **Impact:** the CNBC connector (465,044 of the 481,332 pairs before the two stray rows of `T-067`) cannot add rows from a large
batch; nothing is corrupted when the rejects exceed the limit (it fails before the insert, as in the KLAC call); with few rejects the rows are stored and the ids and the promotion are lost, so a retry is needed (`T-064` tests both). Not caused by `T-062`; it blocks only the data half of the
`T-062` step 6 check.

`T-065`, `T-066a`/`T-066b`, `T-067` (same Work item, found the same day while validating the database after the gateway restart): the
collector API's routers and request schemas pass the literal `data/urls.db` (an empty file) as `db_path`, and seven sites open the queue without a shared resolver, so the `$DATABASE_URL` fallback is not applied (`T-065`); the container sets
`DATABASE_URL="../data/urls.db"` in `/etc/environment`, which beat `.env`: `.env` is read today only as an import side effect of the sub-apps and of `news_collector/config.py`, each with `override=False`, and nothing logs which value won; the maintainer
made reading `.env` a mandatory rule for every app and CLI (`T-066a`/`T-066b`, the latter with a test that enforces it; the maintainer first decided 2026-10-18 that the inherited
environment stays above `.env`, and then, in the `T-066b` design session the same day, reversed it: `.env` wins, the system value is the fallback, every variable, through a shared `load_env()`; constitution rule 7 amended, version 2.0.0); and a discovery request without
`company_names` stored the ticker as the company and got rejected (`T-067`, fixed in PR #81: names are positional and looked up when omitted). The workaround before `T-066b` (start with
`DATABASE_URL=/workspaces/thesis/data/urls.db`, pass `db_path`) is no longer needed: `.env` wins.

`T-075` and `T-076` (same Work item, found 2026-10-18 while asking why only `cnbc.com` has sitemap rows): the sitemap date parser never parsed a date, so the run's date range was never applied to the sitemap and no row got a `pub_date` (`T-075`, fixed; the rows already stored are not touched); and a failed sitemap fetch (`ft.com` and `nasdaq.com` answer 403) is only a warning and the pair is recorded as completed (`T-076`, fixed: the strategy lists the failed roots and children, the orchestrator reports them and does not checkpoint the pair, and the dead `ft`/`nasdaq` roots are dropped; RSS and Wayback failures stay warnings, on purpose). `T-075` also changes the size of Work item 15's sitemap cache: a cnbc match per ticker is about 121,000 entries in range, not 619,000.

**The 939 older rows with `company = ticker`** (`IBM` 892, `MSCI` 44, `AAPL` 3; `CHANGELOG.md` `T-067`): looked at read-only 2026-10-18. `IBM` is correct; the 47 `MSCI`/`AAPL` rows get their label normalized (maintainer choice, option B). The `discovered_urls` write was done 2026-10-18 (47 labels, plus 484810 and 484811 to `review`; backup `urls.pre-wi14-labels.db` verified first). The same labels and the quarantine of the two `review` articles in `articles` were written 2026-10-19 with the maintainer's approval (`CHANGELOG.md` `T-067`). **Work item 14 is closed 2026-10-19.** The leaver lookup of `T-067` was confirmed by the maintainer 2026-10-18.

## Work item 15 — Collector: stop the sitemap from multiplying the database by the number of tickers (code + schema + data, `T-068`–`T-074`)

**Status: closed 2026-10-21 (`T-074`; added 2026-10-18, the maintainer chose option B that day). The tasks are in `CHANGELOG.md`.**
- `T-068`: measured and designed 2026-10-19; **G1 decided 2026-10-19 (freshness 7 days for every run)**; closed (PR #88 merged).
- `T-069`: closed (PR #90 merged; **G2 approved 2026-10-19**; the real file is at schema version 4 since 2026-10-19, checked after a verified backup, PR #91).
- `T-070a`: closed (PR #92 merged 2026-10-20; the gateway runs it on `master` and the real file is at version 4, checked): `news_collector/sitemap_cache.py`, one crawl per domain per process. `T-070b` is closed (PR #93, 2026-10-20): the guard across processes; `T-072` is closed too (PR #94, 2026-10-20: `reclass-sitemap`, no network, current entries only, promote-only through `enqueue_batch`, dry run by default). `T-071` closed afterwards (below). Every gate (G1, G2, G3) is a maintainer approval; nothing in this item touches the real `urls.db` before its gate.
- `T-071`: closed 2026-10-21 (PR #99): per-ticker matching from the cache with the token index (`data_mining/token_index.py`), no sitemap reject stored (`rejected_not_stored` counted and reported), equal to the full scan (tests; on a copy of the real file the 10 tickers give the same 17,235 `accept`/`review` pairs in 4.0 s against 266.2 s, 198 rows against 1,060,682); **the first run on the real file ran 2026-10-21** (503 tickers, +8,757 rows, +7.7 MB, 55,065,566 sitemap rejects counted and not stored, backup `urls_t_071.db`; the cache was fresh, so the `T-070b` guard was not exercised on the real file; the same discovery twice on a copy inserted 198 rows then 0).
- `T-073`: closed 2026-10-20 (PRs #95 and #96 merged; **G3 approved with N = 578,113**; the real `reclaim` deleted 578,113 never-fetched `cnbc.com` sitemap rejects: `discovered_urls` 1,059,452 → 481,339, `articles` unchanged, `accept`/`review` unchanged, 280 MB reclaimed, backup `urls_t_073.db`); the version-5 migration (the index drop) was applied and checked on the real file, recorded by `T-074`.
- `T-074`: closed 2026-10-21 (this change): the real file reads `user_version` 5 without `ix_sitemap_crawls_latest`; the runbook, `SPEC.md` FR-014, the architecture artifacts and the known gap say that the `reject` count dropped by design (903,092 → 328,075 rows today, not data loss); the interim caution is removed; Work item 15 moved to `CHANGELOG.md`.

**Problem (measured 2026-10-18 on the real database).** The `KLAC` `cnbc.com` discovery of `T-064` step 5 added 578,117 `reject`/`skipped` rows
(+243 MB, about 420 bytes a row with five indexes; `discovered_urls` 481,334 → 1,059,452 rows, the file 2.27 → 2.51 GB). The cause is the
design of `T-056c`, not of `T-064`: the sitemap strategy turns every URL of the domain's sitemap into a `(url, ticker)` pair and the queue
stores every matcher-rejected pair. The cnbc sitemap (`sitemapAll.xml`) holds about 578,000 article URLs and is the same for every ticker,
so a full run of the 503 tickers would have added about 503 × 578,000 ≈ 290 million rows (about 120 GB at the same row size). That figure is the one **before the sitemap date-range fix (PR #75)**, which made the date range apply to the sitemap: with it, a match per ticker handles about 121,000 entries (measured on the live sitemap, 618,898 entries in all), so the **projection** for 503 tickers is about 61 million rows (roughly 25 GB at 420 bytes a row, not measured). That is still not acceptable, so option B stands; the figures of the tasks below assume that fix is merged and are re-measured by `T-068`. Facts that bound the fix:
- Only `cnbc.com` has sitemap rows today (1,040,939 rows, 597,559 distinct urls); the sitemap rows carry **no title** (0 of 1,040,939), so the class comes from the URL alone.
- 901,977 of the 903,092 `reject` rows come from the sitemap; 1,115 from `ddg` (a ticker-specific search hit, a real near miss). `portfolio-nlp` reads `accept`/`review` only. Of the 901,977 sitemap rejects, **578,113 were never fetched** (`skipped`, no article) and **323,864 keep a quarantined article** (fetched before they were classed, `T-057`); a `reject` row stored from `T-056c` onwards is never fetched.
- The sitemap crawl and the matching of 578,124 URLs took about one minute; the same file is downloaded and parsed once per ticker (the per-domain `Semaphore(1)` of the orchestrator is handed to the `ddg` strategy only; the sitemap strategies of different tickers of one domain run concurrently, up to the run's global concurrency, 5 by default, each with its own copy of the entries).
- The stored rejects served one purpose: re-classing after a matcher fix without a new crawl (`T-062` promoted 448 of about 325,000, 0.14%).

**Decision (maintainer, 2026-10-18): option B.** Keep the sitemap once per domain, not once per ticker: a ticker-agnostic cache of the sitemap entries; per ticker, match against the cache and write to `discovered_urls` only the pairs that are `accept` or `review`. Option A (drop the sitemap rejects and re-crawl on every matcher fix) was not chosen; B also removes the repeated download and gives a re-class from the cache with no network.

**Guardrails for the whole item**
1. **Additive schema only** (new tables; `discovered_urls`, `articles` and what `portfolio-nlp` reads do not change). It is still a shared-schema and DB-contract change, so constitution AI behavior #10 applies: **G2** is the maintainer's explicit approval of the DDL, the migration and the rollback before any code that creates the tables is merged.
2. **Equivalence before anything else:** for every ticker, the `accept`/`review` pairs the new path writes must equal the ones today's full scan produces (same class, reason and `pub_date`: after the date-range fix (PR #75) the row carries the sitemap `lastmod`, so the cache must keep it), proven on a corpus (`T-068`, `T-071`). A faster path that changes a class is a defect.
3. **Development on a copy of `urls.db`**, never on the real file; the real database only at **G3** with a backup that passes `quick_check`, the gateway and crawler stopped for the data step, and a row-count fingerprint taken before and after.
4. **Unchanged:** the `ddg` and `api` paths (including their `reject` rows), the `--resume` checkpoint (`discovery_progress`), the fetch selection (`status='pending'`), and what `portfolio-nlp` sees.
5. **Interim caution (docs, this PR):** until `T-071` is merged, do not run a `cnbc.com` discovery for more tickers on the real database; `KLAC` is the one that was run. **Lifted 2026-10-21:** `T-071` merged (PR #99) and the first discovery ran on the real file (503 tickers, +8,757 rows; `T-074`).

**Gate criteria decided by the maintainer, 2026-10-18** (each gate still needs its own approval when its evidence exists; these are what it is judged against):
- **G1 (numbers of `T-068`):** exact equality with the full scan on the whole corpus, no loss accepted (a pre-selection that loses a pair is rejected, the fallback is the full scan); no time limit for matching the 503 tickers, **but the CLI shows the advance with a `tqdm` bar** (`tqdm` is already a dependency; the bar is part of `T-071`/`T-072`).
- **G2 (schema of `T-069`):** the cache covers **any domain with `sitemap_roots`** (a `domain` column, no per-domain schema); it lives **in `urls.db`**, as new tables (rollback: drop them); it is refreshed when the run's date range is not covered **or the last crawl is older than 7 days**, and a flag forces it.
- **G3 (data of `T-073`):** it may delete **only the stored rejects of source `sitemap`** (**narrowed from `sitemap`/`rss` in the PR #95 review, 2026-10-20: the proof re-derives stored pairs from the sitemap cache and no `rss` pair can be; 0 `rss` rows qualify today**; **578,113 rows today** of the 901,977 stored sitemap rejects; *narrowings **decided by the maintainer 2026-10-18** (the G3 approval of the evidence is still separate), see `T-073`: only rows that were never fetched, `status='skipped'` with no `articles` row (578,113 today; the other 323,864 keep a quarantined article and stay); only of a domain whose cache is filled (its latest complete crawl holds entries), today `cnbc.com`, so a Wayback or RSS reject of an uncovered domain is never deleted; and never a row whose id is in a committed hand-labeled sample*; the `ddg` rejects and every `accept`/`review` row stay), and only after `T-072` re-derives the **in-range** stored `accept`/`review` pairs of a sample (0 in-range pairs missing; the out-of-range ones and the ones whose URL **left the sitemap** (4.6% of the stored `accept`/`review` rows, measured by `T-068`) are counted and listed, stay stored and are not part of the delete); the file is shrunk with **`VACUUM INTO` a new file and a swap** (gateway and crawler stopped, the old file kept as the backup until the maintainer deletes it).

**Tasks.** `TASKS.md` `T-068`–`T-074`, each with its acceptance: `T-068` measure and design (read-only, **G1**), `T-069` schema and migration (**G2**), `T-070a` the per-domain cache and its refresh in one process, `T-070b` the guard across processes, `T-071` per-ticker matching from the cache, `T-072` re-class from the cache, `T-073` reclaim the stored sitemap rejects (data, **G3**), `T-074` close-out. Order: `T-068` → G1 → `T-069` → G2 → `T-070a` → `T-071` → `T-072` → `T-073` (G3) → `T-074`.

**Risks the tasks must answer.** (a) Matching the sitemap's entries (about 578,000 before the date-range fix (PR #75); **measured by `T-068`: 109,493 article URLs in range, about 4.6 s a ticker, 39 CPU-minutes for 503 tickers**) is too slow and too big unless candidates are preselected, and a preselection (for example a token index on the URL slug) can miss a pair the full scan finds: `T-068` measured the recall on the real data before the design was fixed (exact on the 503 tickers, see its result above), and ordinary-word and one-letter tickers (`ON`, `A`, `IT`, `ARE`, `TTD`) are in the test set on purpose. (b) A sitemap changes over time and its entries are filtered by `lastmod` against the run's date range: the cache needs a coverage range and a refresh rule, and a failed crawl must leave the previous cache valid. (c) The orchestrator runs up to `concurrency` (5) tickers at once, and the sitemap strategies of the tickers of the **same** domain are not serialized (its per-domain semaphore guards `ddg` only): the refresh of a domain's cache must run once while the other tickers of that domain wait for it. (d) The SQL file size does not shrink by deleting rows: reclaiming the space (about 243 MB, the 578,113 rows of the `KLAC` run; the rest of the 2.5 GB is article bodies, which stay) needs a `VACUUM` with free disk and an exclusive window (`T-073`). (e) `reclass_rejects.py` and its `--revert` list work on stored rejects, and the 438-pair known gap is defined on them: change meaning only for the 578,113 never-fetched sitemap rejects that `T-073` removes; the 323,864 that keep an article stay, and `T-072` reports where the 438 pairs sit (`T-072`, `T-074`).

**`T-068` result: measurement and design (2026-10-19; read-only; G1 decided 2026-10-19; revised after the manager review of PR #88).** Run on a fresh crawl of the live sitemaps (crawl at 12:55 UTC, date range 2022-01-01 to 2026-10-19) and on the live S&P 500 list from Wikipedia (503 tickers, kept in `data/sitemap_preselect_per_ticker.csv` with the result for each ticker). The real `urls.db` was opened read-only (`mode=ro`) by the `stored` subcommand only; nothing was written anywhere. **Every figure below is printed by a subcommand of `scripts/sitemap_preselect_check.py`** (`crawl`, `check`, `synthetic`, `cache-size`, `duplicates`, `stored`, `probe`, `timing`; tests in `tests/scripts/test_sitemap_preselect_check.py`), so every figure can be reproduced. The crawl file itself is not committed (74 MB); `crawl` rewrites it.

**G1 summary (what the maintainer accepts or rejects; the evidence is below, each figure printed by a subcommand of the script).**
1. **Preselection by a token index, exact:** 46,341 candidates (0.084% of 55,074,979 pairs) give the same 38,055 pairs, class, reason and `pub_date` as the full scan for 503 tickers, also with an end date (28,260 pairs) and on a synthetic titled corpus; the full scan stays only as the proof.
2. **The cache stores in-range entries only:** 32.7 MB (299 B a row) against 165.6 MB for all dates.
3. **Upsert and expire by "last seen", never replace:** current entries only; a crawl is complete only when nothing failed (a domain whose child always fails is unsupported); a refresh never narrows the start; 90-day purge.
4. **`ft` and `nasdaq` roots stay empty** (`ft`: `/content/<uuid>` URLs, nothing to match; `nasdaq`: no article sitemap).
5. **Freshness: 7 days for every run** (the maintainer's G1 decision, 2026-10-19; the 1-day rule for a run ending today, proposed in earlier versions of this note, was rejected).
6. **Scope of the evidence:** the real-data figures are `cnbc` only; the titled-entry path is proved on a synthetic corpus and by the argument.

**G1 decided (the maintainer, 2026-10-19): freshness is 7 days for every run.** The G2 criterion (Work item 15, "Gates"), `T-069`'s "Criteria decided" and the refresh-rule heading already say 7 days and are unchanged; the 1-day proposal is removed from the note, `T-070a` and the PR body.

**Not approved by G1** (recorded here so `T-069`/`T-070a`/`T-070b` start from it, and reviewed in those PRs): the DDL (G2), the owner of the cache in the code, the cross-process guard and its timeouts, the signature and `CACHE_VERSION`, the force flag, the thread offload, the retention of `sitemap_crawls`, and the split of `T-070`.

*What the sitemaps hold.*
- **`cnbc.com`** (crawl 10.2 s, no failures): 618,934 entries over all dates; **0 with a title**; 617,752 (99.8%) with a `lastmod`; 127,225 in range (or without a `lastmod`); 578,156 distinct article URLs over all dates; **109,493 distinct article URLs in range**. The figure the matcher sees is 109,493, after the article filter, the normalization and the de-duplication; it is below the "about 121,000" measured on 2026-10-18, and I did not reconstruct how that one was counted. Entries by `lastmod` year: the peaks are 2010 (84,944) and 2012 (60,827); 2013–2025 have 25,531–44,692 a year; 2026 has 19,311 so far. **23 normalized URLs are listed more than once, 10 of them with different `lastmod` values** (none with a missing one); today the first entry of a URL wins (`_dedupe_and_filter`), and the cache keeps the same rule inside one crawl (below).
- **`ft.com`** (`probe`): the root the code tried (`sitemap-index.xml`) still answers 403, which is why no `ft` rows exist. A different root, `https://www.ft.com/sitemaps/index.xml`, **does answer**: 291 child files, 1,073,301 entries (837 s to crawl; the first run of the same day read 1,073,292 and 122 titles, the feed grows), 1,073,174 with a `lastmod`, **127 with a title**, 2 children that do not parse (`archive-2011-11.xml`, `archive-2012-8.xml`; under `T-076` that is a failed, not-checkpointed pair). The URLs are `https://www.ft.com/content/<uuid>`: no slug and, but for 127 entries, no title. The matcher reads only the path, the query and the title, so it has nothing to match on and would class almost every `ft` entry `reject`. **Finding: do not re-enable an `ft` root** (the cost would be 800 s and about 1.07 million cache rows for no pair); this is a connector decision and is not part of this item.
- **`nasdaq.com`** (`probe`): `https://www.nasdaq.com/sitemap.xml` answers with 748,177 entries, **none an article**: 388,348 `market-activity/mutual-fund`, 184,424 `market-activity/stocks`, 87,022 `market-activity/etf`, and so on; no `lastmod`, no title. Its `articles/sitemap.xml` child answers 403 and `education/sitemap.xml` 404, as `T-076` found. The article sitemap of `nasdaq` is still not reachable; its `sitemap_roots` stay empty.
- So `cnbc.com` remains the only domain with a usable sitemap; the cache has no per-domain schema (G2 criterion), so a later domain needs only a `sitemap_roots` entry.

*Cost of today's path (full scan).* The matcher costs about 42 µs a pair (`timing`: 10 random tickers × 3,000 entries, one warm-up call per ticker so the compile is not timed; the first version of this note said 45 µs from an uncommitted sample), 4.6 s a ticker for 109,493 entries, about 39 CPU-minutes for 503 tickers. Measured with the matcher alone: 109,493 × 503 = **55,074,979 pairs in 308–355 s on 14 processes**; the committed script, whose baseline is the production filter (normalization and article filter on every entry for every ticker), runs both paths in 536 s on 14 loaded processes. They contain **38,055 `accept`/`review` pairs** (23,192 `accept`, 14,863 `review`; 51 tickers have none); the other **55,036,924 are `reject`**, which is what the queue stores today (the projection of `T-075`, 61 million, was an estimate from 121,000 entries; the measured figure is 55 million). At 420 bytes a row, the 38,055 pairs are about 16 MB and the 55 million rejects are about 23 GB.

*Candidate preselection (the design names it): a token index.* Per entry, the set of `flat()` tokens of `path + query + title` (the exact text `classify` reads); per ticker, the candidates are the union, over the signals of the matcher, of the postings of **one anchor token** of each signal's form: the name (`name`), each alias, the ticker (`weak_ticker`, only for a ticker of 3+ letters that is not an ordinary word or an abbreviation), each loose-name spelling (`loose_name`) and the first word (`weak_first_word`); plus the postings of the ticker in the *title* index (the title-only signals `explicit_ticker`, `upper_ticker`, `cashtag`, `abbreviation`). The candidates are then classed by the same `classify`. **`TokenIndex` and the cache reader move from `scripts/` into `src/` in `T-071` (next to `matching`), and the script imports them from there**, so the equality and mutation tests guard the code that ships. The index is built in memory per run (1.6 s for 109,493 entries, 33,843 distinct tokens); it is not stored.

*Why it cannot skip a pair the matcher would class `accept` or `review` (the argument, by construction).* Every non-`reject` class comes from one of the signals above, and each needs its form to appear in `flat(text)` as consecutive whole tokens (`flat` lower-cases, reads `&` as `and` and splits on anything that is not `a-z0-9`; `word()` and the `" form "` tests are the same boundaries, because a non-ASCII character separates in both). So every token of the form is a token of the entry, and **any one token of the form is a valid anchor**; the index takes the rarest, which can only shrink the set. Two cases change a token and are handled: (1) the possessive `form + "s"` of a loose name changes the **last** token (`americas` for `america`), so a form of two words or more is anchored on a token other than the last, **or on the entries that have the last token or the last token plus `s`** (both are valid; the smaller wins: for `ON Semiconductor` that is 205 candidates instead of the 11,300 of the token `on`); (2) the title-only signals need the ticker as a token of the title, hence the second index. The argument stops holding if a signal is added to `classify` that does not need a token of its form (a fuzzy or a prefix match): `T-071` must keep the equality tests below, so such a change fails them.

*Evidence, all exact (no tolerance), on the matcher as merged.* The baseline is the production code, `SitemapRSSStrategy._dedupe_and_filter` over the entries in range; the preselected path reads its entries back from an in-memory table with the DDL below, so the normalized URL, the `lastmod` and the title are the cached ones; a pair is equal when its **class, reason and `pub_date`** are.
- **Real corpus**, 503 tickers × 109,493 entries: the preselection classes **46,341 candidates (0.084% of the 55,074,979 pairs; median 19 a ticker, 95th percentile 393, largest 2,106 for `NVDA`)** and finds **the same 38,055 pairs, same class, same reason, same `pub_date`, for every ticker: no pair missing, none extra, none changed** (`check`; the 503-row table is `data/sitemap_preselect_per_ticker.csv`, and a re-run of `check` after the later script changes wrote a byte-identical file).
- **The hard tickers, one by one** (candidates, `accept`, `review`; both paths agree on each): `ON` 205, 8, 0; `A` 0, 0, 0; `IT` 0, 0, 0; `ARE` 2, 0, 2; `TTD` (not in today's live list, run on its own) 35, 8, 12; `KLAC` 6, 0, 5; `NWS` and `NWSA` 92, 0, 18 each (the same URLs under two tickers); also `AAPL` 1,833, 1,685, 129; `MS` 1,039, 856, 147; `MA` 36, 36, 0; `GE` 64, 6, 0.
- **A corpus with titles**, because `cnbc` has none (the case `news:title` produces on another domain): `synthetic` builds 12,000 entries (8,421 with a title, 3% without a `lastmod`) from real slugs, titles such as `(NASDAQ: X)`, `(NYSE: X)`, `$X`, `X stock news`, `X's CEO`, `Egypt PM says`, `A look at IT and ON and ARE`, a non-ASCII title, and URLs spelling the ticker, the name, `<name>s` and `<name>-corp`, over the 503 tickers plus `TTD`: 11,618 pairs in both paths, equal; **all nine reasons of the matcher appear** (`name` 4,498, `upper_ticker` 2,034, `weak_ticker` 1,587, `explicit_ticker` 1,489, `loose_name` 656, `weak_first_word` 564, `abbreviation` 608, `alias` 154, `cashtag` 28). **The check can fail:** with one anchor family left out in turn, the same run loses pairs (`name`: 49 pairs in 11 tickers; `alias`: 112 in 4; `weak_ticker`: 1,179 in 375; `loose_name`: 162 in 38; `weak_first_word`: 248 in 41; `title`: 913 in 61).
- **A run with an end date** (the cache has no end; it is applied on read): `check --start 2022-01-01 --end 2025-06-30` over the same 503 tickers — 95,378 entries in range, the cache's 109,493 rows read with `lastmod <= end`: 28,260 `accept`/`review` pairs in both paths, no ticker differs (class, reason, `pub_date`), 393 s — and a test with an entry after the end, an entry without a `lastmod` and one inside.
- **Sitemap, RSS and Wayback entries** reach `_dedupe_and_filter` in one list, first URL wins, `source` `rss` for the RSS entries. The evidence above is sitemap-only; `T-071` feeds the cached entries first, followed by the live RSS and Wayback entries as today, so the winner of a URL listed in both is unchanged; a test pins a URL present in the cache and in an RSS entry (same `source` and `pub_date` as the old path).
- **The `news:title` fixture**: the entry `…/neutral-slug.html`, `title="(NASDAQ: KLAC) shares jump"` is the one candidate of `KLAC` through the title index and classes `accept`/`explicit_ticker` (printed by `synthetic`, and a test).
- **Tests** (`tests/scripts/test_sitemap_preselect_check.py`): the fixture; equality on a small synthetic corpus; the check failing when an anchor family is removed; ten hard forms each required to be a pair of the matcher and a candidate (a possessive, `AT&T`, a ticker with a dot, a stop-word name, a camel-case brand, a name with a digit, an ordinary-word ticker, an alias, a cashtag, a plain full name); the cached `pub_date` with a duplicated URL and an entry without a `lastmod`; article-only normalized cache rows; the `stored` subcommand not changing the database file (hash before and after).
- So the preselection stands: **the full scan is not kept**, it stays only as the proof (the script) and as the fallback if the equality test ever fails.

*Cache design (for G2; nothing is created in this PR).* Two additive tables in `urls.db`:
- `sitemap_entries(domain TEXT NOT NULL, url TEXT NOT NULL, lastmod TEXT, title TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, PRIMARY KEY (domain, url)) WITHOUT ROWID` and `CREATE INDEX ix_sitemap_entries_lastmod ON sitemap_entries (domain, lastmod)`. `url` is the normalized URL of the entries that pass the connector's `is_article_url`. **Duplicates:** inside one crawl the first entry of a URL wins (`INSERT OR IGNORE`, as `_dedupe_and_filter` does today, which the equality check above proves for the 23 duplicated URLs); on a later crawl the upsert takes the new `lastmod`. **The refresh writes the whole crawl in one transaction: 0.66 s for the 109,493 in-range rows (3.9 s for all dates)** (`cache-size`, a fresh database on the local disk, no other writer), so the other process waits less than a second; `data_mining/db.py` already sets `BUSY_TIMEOUT_MS = 30_000` in `connect()`, so a write waits instead of failing; `T-070a` makes the cache open its connection through `connect()` (a test that it does) and tests a write during the upsert. The real `urls.db` is in **WAL** mode (read-only check, 2026-10-19: 2.5 GB, 4096-byte pages); `cache-size --wal` repeats the upsert in WAL with `synchronous=NORMAL`, as `connect(wal=True)` sets it: **0.57 s in range, 3.14 s all dates**, readers are not blocked, and the WAL checkpoint cost on a 2.5 GB file is for `T-070a` to measure on the copy. No staging table is needed at this size.
- `sitemap_crawls`: one row a crawl, timestamps UTC ISO strings, **`signature`** = a hash of the domain's sorted `sitemap_roots` and a constant `CACHE_VERSION` that is bumped when `is_article_url` or the normalization changes. **A crawl in progress is a row with `finished_at IS NULL` and `complete = 0`** (the cross-process guard, below). **The complete DDL, which is what G2 approves, with its rollback** (`data_mining.schema.SITEMAP_CACHE_DDL` and `SITEMAP_CACHE_ROLLBACK` are these statements, the only copy: the script imports them, and `tests/data_mining/test_schema.py` executes them and shows by `EXPLAIN QUERY PLAN` that the three queries of the design — the latest complete crawl of a domain, a crawl in progress younger than the timeout, the current entries of a domain — each `SEARCH` an index and none `SCAN`s a table):
  ```sql
  CREATE TABLE sitemap_entries (
      domain TEXT NOT NULL, url TEXT NOT NULL, lastmod TEXT, title TEXT,
      first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, PRIMARY KEY (domain, url)
  ) WITHOUT ROWID;
  CREATE INDEX ix_sitemap_entries_lastmod ON sitemap_entries (domain, lastmod);
  CREATE TABLE sitemap_crawls (
      domain TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
      complete INTEGER NOT NULL CHECK (complete IN (0, 1)), range_start TEXT NOT NULL,
      signature TEXT NOT NULL,
      entries INTEGER, failures INTEGER, PRIMARY KEY (domain, started_at)
  );
  -- the latest-complete-crawl lookup orders by started_at (T-070b) and is served by the primary key
  -- (domain, started_at). Version 4 also created ix_sitemap_crawls_latest (domain, complete, finished_at), which no
  -- query uses: dropped in schema version 5 (T-073, decided by the maintainer 2026-10-20), `DROP INDEX IF EXISTS` in run_migrations()
  CREATE INDEX ix_sitemap_crawls_open ON sitemap_crawls (domain, started_at) WHERE finished_at IS NULL;
  -- rollback (the indexes go with their tables):
  DROP TABLE sitemap_crawls; DROP TABLE sitemap_entries;
  ```
  **Formats** (compared as strings by the design; written in the DDL's comment too): `lastmod` is an ISO date `YYYY-MM-DD` (NULL when the sitemap has none); `started_at`, `finished_at`, `first_seen`, `last_seen` are UTC ISO-8601 with microseconds and no offset (`2026-10-19T10:00:00.000000`), `range_start` a date; `last_seen` equals the `started_at` of the crawl that saw the entry (not enforced by the schema); `complete` is 0 or 1 (a `CHECK`). `rollback_sitemap_cache` refuses a database that is not at the current schema version (5 since T-073).
  **Retention of `sitemap_crawls`:** the refresh deletes the rows older than 90 days except the latest complete one of each domain, in the same purge as the entries; `sitemap_entries` is purged by `last_seen` older than 90 days.
- **Size, measured on a built database** (`cache-size`, with the index, after `VACUUM`): **in range only, 109,493 rows, 32.7 MB (299 bytes a row)**; all dates, 578,156 rows, 165.6 MB (286 bytes a row). `cnbc` has no titles, so those rows carry a `NULL` title; **with a 70-character title each, the in-range rows are 374 bytes, 41.0 MB**, which is what a domain with `news:title` costs. **Proposal: store the in-range entries only** (`lastmod >= range_start` or no `lastmod`; the cache has no end column: **the run's end is applied when the candidates are read** (`lastmod <= end`, an entry without a `lastmod` kept), exactly as `_parse_sitemap_leaf` filters with `date_range.contains` today; the script proves it with `check --end` and a test). A crawl is 10 s, so extending the start date re-crawls.
- The token index is not stored (a rebuild is 1.6 s). **Memory** (`cache-size`, in range, no titles): the rows as Python tuples about 28 MB and the token index about 21 MB (33,843 tokens), so **about 49 MB a domain a process**, in the gateway as well; a domain with titles adds about 70 bytes of text a row plus its title tokens, so tens of MB, not hundreds; the all-dates cache would be about five times that, one more reason for in-range only.

**Implementation rules recorded here (reviewed in `T-069`/`T-070a`/`T-070b`; G1 does not approve them).**

*Who owns the cache in the code.* `SitemapRSSStrategy(connector, client)` has no database handle and is **built per (ticker, domain) pair** (`orchestrator.py` `_run_one`, and `api/routers/_common.py`, where a request is one ticker). The guard and the cache therefore live **above** the strategy: a `SitemapCache` object per domain (in `news_collector`, owning the lock, the DB access and the in-memory token index), **injected into the strategy as an optional constructor argument whose default `None` keeps today's live fetch** (so `ddg`, `api`, and every existing test are unchanged). Lifetime: the orchestrator creates one per domain per run; the API creates one per domain per process (an app-level registry), so two requests for two tickers share one crawl and one index, and **a process rebuilds its index when the domain's latest complete crawl is not the one the index was built from**: before using the index a run reads that crawl's `started_at` (its identity; one query, served by the primary key `(domain, started_at)` since `T-070b`) and rebuilds when it differs, so a refresh by the other process (the crawler at night, the gateway holding yesterday's index) is seen on the next run, and so is a purge. Two requests of the API and a run of the orchestrator in one process share the guard. **As built (`T-070a`, PR #92):** the registry is `shared_cache(connector, client, db_path)`, keyed by `(db_path, domain)`; `begin(range, force)` returns a **`SitemapRun`** (the crawl it decided on, the failures every pair must report, the rows parsed once per crawl), and the strategy receives the run, not the cache, so one run's failed decision never changes what another run reads; `begin` never raises (a database error is a failure of the domain's pairs). **Guard across processes (`T-070b`, PR #93):** `_claim` is one `BEGIN IMMEDIATE` transaction that looks for an unfinished crawl row younger than `SITEMAP_STALE_MINUTES` (default 30, > 0; the lookup is `OPEN_CRAWL_SQL`, served by `ix_sitemap_crawls_open`) and otherwise inserts this process's row (`started_at` = the claim, which is the crawl's `last_seen`); the winner's write closes that row in the same transaction as the upsert (`ON CONFLICT DO UPDATE`) and never moves an entry's `last_seen` back (`WHERE excluded.last_seen >= last_seen`), and **the latest complete crawl is the one with the latest `started_at`, not `finished_at`**, so with two overlapping crawls (a takeover of a slow winner) the newest data is current whichever finishes last; a waiter polls every 5 s and then reads a complete crawl, crawls nothing after a failed one (and backs off like the winner) or, when the row is stale, takes over; a crawl that raises or is cancelled closes its row as failed; a forced call is satisfied by the crawl it waited for; a waiter whose range is wider than the winner's crawls after it. **Also as built (PR #92, review):** (1) **back-off:** after a failed refresh a process does not crawl again for `SITEMAP_RETRY_MINUTES` (default 5, `0` = off) unless the call is forced, so an outage costs one attempt per window, not one per run; a gateway whose source recovers inside the window keeps failing that domain until it ends or the call is forced; (2) **`warnings`:** a run that went on with an older cache because the refresh failed (cases a and b) says so in `DiscoveryStats.warnings`, the CLI summary and `warnings` on `RunAllResponse` and `DiscoverResponse` (additive API fields), not as an error; (3) **one error per unusable domain** in the run summary, with the number of pairs left undone (each pair is still not checkpointed); every refresh also purges crawl rows older than 90 days except the latest complete one. `T-070a` tests: two API requests, one crawl; the orchestrator and an API route in one process share the guard; no cache argument means the old behavior. The cache's database calls run in `asyncio.to_thread` so the event loop is not blocked by the 0.6 s upsert, with a test that a pair on another domain progresses during a refresh.

*Who crawls (two processes) and what a run does when the refresh fails.*
- **One crawl per domain across processes.** The gateway and the crawler are separate processes on one `urls.db`, so an in-process lock is not enough. A process that must refresh inserts the in-progress row (`finished_at IS NULL`) in one `BEGIN IMMEDIATE` transaction after checking that no in-progress row of the domain is younger than the stale timeout; if one is, it waits, polling every 5 seconds, and when the winner's row is finished it reads the result. **The timeout is 30 minutes and is a setting** (`ft` took 837 s, so a slow domain fits, but a root that needs longer must raise it). A row older than the timeout is a crashed crawl: it is ignored and a new one starts. **If the winner failed** (an incomplete crawl), the waiter does not crawl again: it applies the failed-refresh cases below, so one outage causes one crawl, not one per process. **If the waiter has waited the timeout and the winner is still running,** it takes over (the winner's row is by then stale). The lookup is served by a partial index, `CREATE INDEX ix_sitemap_crawls_open ON sitemap_crawls (domain, started_at) WHERE finished_at IS NULL`. The in-process guard of `T-070a` stays for the tickers of one process. Acceptance: two processes (two connections), one crawl.
- **Failed refresh, three cases.** (a) The latest complete crawl covers the run's start and is at most 7 days old: use it. (b) It covers the start but is older than 7 days (the refresh failed): **proceed with a warning that carries the age**; the data is stale at the recent end (it misses the re-dates and the new URLs since the crawl), not wrong. (c) It does not cover the run's start, or there is no complete crawl: **the pair fails** (reported as a failure and not checkpointed, as `T-076` requires), because matching a narrower cache would record the pair as checked. A run never matches on a cache that does not cover its range.

*What a crawl must match to be reused (the signature).* "Covered" is not only `range_start` and age: the latest complete crawl's `signature` must also equal the current one (a hash of the sorted `sitemap_roots` of the domain and `CACHE_VERSION`). A changed or added root, or a bumped version (a release that changes `is_article_url` or the normalization, so the cached rows were filtered by the old rule), makes the cache not covering: it refreshes, and if the refresh fails it is case (c), the pair fails. Nothing is silently matched on a cache of another root set or filter.

*The force flag.* `--refresh-sitemaps` on the CLI and `refresh_sitemaps=true` on the domain and run-all routes force a crawl for the domains of the run; it does not bypass the in-progress row (two forced runs still make one crawl) and it never narrows the cache.

*Freshness (decided at G1: 7 days for every run).* The age of the latest complete crawl is measured from its **`started_at`** (the data is as of the start of the crawl), in UTC, **once per domain at the start of a run**; the clock never triggers a refresh in the middle of a run. A crawl at most 7 days old is fresh enough for any run, whatever its end date. **Consequence, accepted at G1:** a run whose end is today reads a cache that can be up to 7 days old, so it misses what changed in the sitemap since the crawl (measured: about 53 re-dates and 0.5 new URLs a day, so up to about 370 entries on day 7, mostly re-dates whose `pub_date` is the modification date). A run that needs the live state uses the force flag (`--refresh-sitemaps`), which costs 10 s of crawl and 0.6 s of upsert.

*Refresh rule (the G2 criterion: refresh when the run's range is not covered, when the last complete crawl is older than 7 days, or on a flag).* "Covered" means the run's start is not earlier than the `range_start` of the domain's latest **complete** crawl. Two rules close holes the first version of this note left:
- **A crawl is complete only when every root and child was fetched and parsed, with no exception list** (decided by the maintainer 2026-10-19: no `sitemap_ignore_children` config field). A failure of any child makes the crawl incomplete: it changes nothing but `sitemap_crawls`, the previous complete crawl stays the basis (the 7-day rule then retries), and the failure is reported as `T-076` requires. Only sitemap failures count; RSS and Wayback failures stay warnings. **A domain with a child that fails every time (`ft` has two) is unsupported until the site fixes its files**: its cache is never marked complete, so it must not be given a `sitemap_roots` entry; this is one more reason to keep the `ft` root empty.
- **A refresh never narrows the cache.** A refresh crawls from `min(the run's start, the latest complete crawl's range_start)`. A run with a later start is covered, so it does not refresh at all; a forced or 7-day refresh keeps the wider start. Hence a narrower run cannot make the 2022–2023 entries stop being current.

*What happens to an entry that leaves the sitemap (measured).* The sitemap is not append-only. Of the 597,559 distinct `cnbc.com` sitemap URLs stored on 2026-08-10 to 08-13, **19,437 (3.3%) are not in today's sitemap, 10 weeks later; none is a normalization or article-filter artifact** (0 are in the raw crawl in any form), and **34 article URLs in the crawl are new since then (not stored; `stored` prints it)**. The sitemap mostly **re-dates** URLs it already lists: 3,664 article URLs have a `lastmod` after 2026-08-13 (about 53 a day), and 3,630 of them were already stored. So `lastmod` is a modification date, not a publication date, and "entries by `lastmod`" is not a count of new articles: genuinely new URLs are about 0.5 a day. Sized (`cache-size`, `stored`): **1,536 of the 109,493 in-range cache rows (1.4%) have a URL year before 2022** (an old article re-dated into the range), and **170 of the 32,362 stored in-range accept/review rows (0.5%)**; so the in-range population is overwhelmingly 2022 or later, and the limitation is small. **For cnbc the pair's `pub_date` is a modification date** (the sitemap's `lastmod`), as it is today; changing that (the URL year is the publication date) is out of scope here and, if wanted, a separate item. Two consequences: the date filter admits an old article that was re-dated into the range (as it does today), and a stale cache misses re-dates and about one new URL every two days, not about 100 articles a day. **The loss is not uniform** (`stored`, by the year in the URL): none for 2006–2007 and 2010–2012; 0.1–0.3% for 2008–2009 and 2013–2015; 2.4–5.9% for 2016–2021 (9,650 URLs); **8.1–9.2% for 2022–2025 (8,662 of the 100,243 URLs stored for those years, 8.6%) and 5.3% for 2026 so far**, 9,577 URLs in all for 2022–2026. So the entries inside the date range churn about 8% in 10 weeks, which is why the cache keeps "last seen" and does not replace. **Design: upsert and expire by "last seen", never replace.** A complete crawl upserts every entry with **`last_seen = the crawl's started_at`** (known before the entries are written, and unique per domain by the key of `sitemap_crawls`; `finished_at` does not exist yet at the single write); an entry is *current* when its `last_seen` equals the latest complete crawl's `started_at` (the "latest complete crawl" query selects it); an incomplete crawl changes nothing but `sitemap_crawls`. `T-071` (matching for discovery) and `T-072` (re-class) read **current entries only**; a stored `discovered_urls` row is never derived from the cache and is never touched by an expiry; expired entries are purged by the refresh after 90 days (a delete in the cache table only). **For `T-072`/`T-073`:** of the **138,962 stored `accept`/`review` `cnbc.com` sitemap rows (111,943 URLs, all with `pub_date` NULL)**, `stored` classes every row by the `lastmod` of the crawl: **100,210 (72.1%) are dated before 2022-01-01, 32,362 (23.3%) are in today's range, and 6,390 (4.6%, 5,197 URLs) are of URLs no longer in the sitemap**. The 72.1% and the "about 65%" that `T-073` quotes agree once the populations are the same: PR #75 counted 62,379 of 93,226 `ok` articles (66.9%) of every source, and `stored` prints the same count over the `ok` sitemap rows alone, 62,140 of 86,724 (71.7%, by the year in the URL; the numerators match, 62,138 against 62,140), and 102,505 of 138,962 of all stored rows (73.8%) by the year in the URL, 100,210 (72.1%) by the crawl's `lastmod`. **The set `T-073` would delete** (`stored`, read-only): the **578,113 never-fetched sitemap rejects** are all of URLs that are **still in today's crawl**: **0 left the sitemap, because that set is one day old** (the `KLAC` run of 2026-10-18, 13:19–13:20); the URLs that left were stored 2026-08-10 to 08-13, ten weeks ago (the fetched articles and the `accept`/`review` rows), which is the same 3.3% (8–9% for 2022–2025 URLs) that will apply to this set as it ages, so **G3 recounts the three kinds on the day** and "left the sitemap" will no longer be 0. By the crawl's `lastmod` and the **default range start 2022-01-01** (`DISCOVERY_START_DATE`; another start gives another split) **468,661 (81.1%) are before 2022-01-01, outside the cache's range, and 109,452 (18.9%) are in range and re-derivable from the cache**. So the part `T-073` deletes that the cache cannot re-derive is the out-of-range 81.1%, which is the narrowing the maintainer already decided (outside the configured range); it is large, so G3 states it. The check of `T-073` must therefore count three kinds of stored pair, not two: out of range, **left the sitemap** (counted and listed, never "missing", never deleted), and in range and present (must be re-derived).

*Estimate of a 503-ticker run with the design* (cnbc, today's sitemap): crawl 10 s once (not 503 times), index 1.6 s, matching 46,341 candidates about 2 s of CPU (46,341 × 42 µs, from `timing`; one pattern compile per ticker is not in the 2 s), then the inserts of up to 38,055 pairs (about 16 MB) and the 32.7 MB of the cache: **seconds of matching and about 49 MB written, against about 41 CPU-minutes, 503 sitemap downloads and 55 million rows (23 GB) for the same result today.** The `tqdm` bar is the maintainer's G1 criterion and stays: a bar over the 503 tickers, in `T-071`/`T-072`.

The decisions the maintainer takes at G1 are the six of the **G1 summary** at the top of this section.

## Work item 16 — The gateway keeps an untagged duration column, and the financials payload carries the filing's own filed facts (code, T-159b)

**Status: in progress. `T-159b` active.**

**Why**: Requested by `portfolio-financial-analysis`'s T-143 pilot (part A, the reviewer's
empty-values finding, §3 "F0" and §2 "the filed facts in the payload"). The pilot measured
17,363 values the filing itself holds and the gateway does not serve; 499 of them are dropped
by the gateway's own **untagged-column defect**: `edgartools` renders a 16/24-week filer's
quarter or year-to-date column as a bare date (no tag -- its day-span falls outside every
bucket it labels), the gateway looked that column up as an *instant*, found nothing for a
flow, and dropped the row -- the filer's own filed value, served and then thrown away. The
pilot's downstream fix (its `T-159c`) needs the filing's own facts to fill the rest (F1-F3,
F5b), so the payload has to carry them.

**Approach**:
1. In `src/sec_edgar/agent.py`, treat an untagged column on a duration statement (income
   statement, cash flow) as a period of unknown length: look up the filed non-dimensional
   facts of the concept that *end* on the column's date -- the durations of at most 371 days
   (PER-08's 52/53-week year) and the instants filed at that date (a cash-flow statement's
   "cash at end of period" row is an instant fact rendered under a duration column). A fact
   whose value matches and whose period length is unique among the matching facts identifies
   the cell: the rendered value stands (T-042's treatment of a matching value -- the same
   magnitude, the rendered sign kept). Two matching facts of *different* lengths are
   ambiguous and the cell is left alone (never guessed); no matching fact drops the value, as
   before. A tagged column and a balance-sheet row keep their own lookups and are unchanged.
2. Add `filed_facts(xbrl, period_ends)` and its `_safe_filed_facts` wrapper: the filing's own
   non-dimensional, numeric facts whose period ends on one of the statements' dates, each with
   its concept, its own raw value, its unit, its period start/end and its `decimals`.
   `get_financials` returns them additively as `data["filed_facts"]`, with
   `data["filed_facts_error"]` carrying a read failure (never a failed response, and never an
   entry in `reconciliation_errors`).
3. Update the docstrings, the OpenAPI route description in `apps/sec_edgar_api.py`,
   `SPEC.md` FR-004 and `docs/modules/sec-edgar.md`.
4. Add hermetic tests in `tests/sec_edgar/test_agent.py`, with the facts captured live in
   `tests/sec_edgar/fixtures/`: an untagged 168-day column kept, an untagged column matching
   facts of two lengths left alone, a tagged column and a balance-sheet row unchanged, the
   filed facts present and non-dimensional only, and the `_safe_` wrapper isolating a failure.

**Acceptance criteria**:
- An untagged income-statement or cash-flow column is served again: the rows the gateway used
  to drop on the 32 filings with untagged columns are served, with the SEC's own value.
- No tagged column and no balance-sheet row changes (the same values, the same drops).
- A cell whose filed facts match at two different period lengths is left alone, never guessed.
- `data["filed_facts"]` holds the filing's own non-dimensional, numeric facts for the
  statements' dates, each with its own value (never derived) -- and no dimensional fact and
  no non-numeric fact.
- A failed filed-facts read returns an empty list plus `filed_facts_error`, never a failed
  response.
- All quality gates clean (`ruff check`, `ruff format --check`, `mypy`, `pytest`).

## Sequencing

Work items 3, 4, 5, 6, 7, 9, 10, 11, 12, 13 and 14 are closed (merged or approved; tasks in `CHANGELOG.md`). Work
items 1–2 stay reverted/on hold at the maintainer's prior request. Work item 8 (`T-045`) is
open and active. Work item 10 (wrong-company tagging, requested by `portfolio-nlp`) closed 2026-10-16; `portfolio-nlp` has the
quarantine and the hand-off. Work item 12 (`T-062`, the matcher follow-up) closed
2026-10-17 after the audited real run; `portfolio-nlp` did not wait on it. Work item 13 (`T-063`, the News Corp pairs) closed 2026-10-17 by decision: they wait for the NER check, nothing is released.
Work item 14 (`T-064`–`T-067`, `T-075`–`T-077`, found 2026-10-18) has all its code merged; the `articles` label and quarantine step of the 939 rows was written 2026-10-19 (`CHANGELOG.md` `T-067`); it is closed. History: it was maintainer-scheduled; `T-064` goes first because it blocks the data half of the `T-062` step 6 check; then `T-065`, then `T-066a` (start-up check and visibility, done in PR #80); `T-067` (done in PR #81); `T-066b` (the shared `data_mining.env.load_env()` and its test) has its design: `.env` wins over the inherited environment for every variable, the system value is the fallback, and constitution rule 7 is amended (version 2.0.0, `TASKS.md` `T-066b`); the code is done (`T-066b`, `TASKS.md`). `T-077` (found 2026-10-18: `.env` pins `DISCOVERY_END_DATE=2026-08-10` and, since `T-066b`, wins) makes the end date an input with a UTC-today default in the CLI and the API, retires the variable with one notice, and removes `config.py`'s import-time `.env` read; its docs PR (#84, merged) amended the constitution to 2.0.1; the code is done in PR #85. The 939 older rows with `company = ticker` were decided and the `discovered_urls` part written 2026-10-18 (`T-067`; see that section).
Work item 15 (`T-068`–`T-074`, the sitemap cache, option B chosen by the maintainer 2026-10-18) is **closed (2026-10-21)**; its three gates were G1 the design numbers, G2 the schema, G3 the real-data step, **passed 2026-10-19, 2026-10-19 and 2026-10-20 (N = 578,113); `T-068` to `T-074` are closed (the per-task lines above)**; it does not depend on Work item 14 except that `T-064` (PR #73) is the fix that let the large batch through.
Otherwise there is no ordering constraint from the rest of the backlog, since every other `SPEC.md` §13
item is accepted (Non-goals above) and not touched by this plan.

See `TASKS.md` for the discrete, checkable task breakdown.
