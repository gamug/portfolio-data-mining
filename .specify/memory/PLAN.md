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
`docs/upstream_requests/data_mining_leaver_gics.md`.

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

**Status: open. `T-053`–`T-055` done (rule and targets approved 2026-10-15); `T-056` done (`a`, `b`, `c`; rows discovered before T-056c are stored `accept` without a reason, the T-057 re-tag reclassifies them); `T-057` done (real re-tag run 2026-10-15, hand check passed); `T-058` done (hand-off posted on portfolio-nlp PR #102); `T-059`–`T-060` pending; the matcher gap the real run exposed (at least 433 false rejects) is the successor Work item 11 / `T-061`, open and maintainer-scheduled, not part of this item's close-out; the maintainer confirmed the `T-056b` schema changes and the `T-057` quarantine shape (`articles.prev_fetch_status`, `fetch_status = 'quarantined'`) (constitution #10) on 2026-10-15; T-057's tooling is PR #61.**

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
because only the length is checked, tracked as `T-060`.

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
  `&` normalization) is a follow-up task (`T-061`): it re-classes those rows with a targeted pass (the re-tag
  only touches rows with `match_reason IS NULL`) and `release_articles`. It is Work item 11 below, so this
  item closes at T-059 with a named successor.
- `portfolio-nlp` confirms wrong-company rows no longer reach its pending
  queries.
- `ruff check`, `ruff format --check`, `mypy`, `pytest` clean; `SPEC.md` §13
  item 10 and FR-014, `docs/modules/news-collector.md` and the architecture artifacts
  reconciled (constitution AI behavior #11, never renamed).

## Work item 11 — Matcher: stop rejecting pairs whose URL spells the company out (code + data, T-061)

**Status: open, not scheduled (maintainer decides; found by the Work item 10 real run, 2026-10-15).**
**Why:** the T-057 re-tag quarantined at least 433 `reject` pairs on 234 URLs (News Corp 400 as `NWS` +
`NWSA`, `KLAC` 15, `SO` 11, `BAC` 4, `DG`/`IFF`/`TTD` 1) whose URL carries the company's full name, and the
hand check estimates 2.5% of the whole `reject` class (95% interval 0.4%-12.9%). Causes, scope and the
current figures are in Work item 10 ("Accepted recall loss"). **What:** a full-name hit is at least `review`
(never `reject`), with the possessive `s` and `&` normalized; the quarantined rows are re-classed with a
targeted pass and released when they become `accept`. **Tooling the hand check needs:** the T-057 sheet
(`data/ticker_retag_check_sample.csv`) joins `EARLIER_SAMPLES` and the `--final` exclusions, and the new draw uses `--final --out <new path>` (the default output is that sheet); the new sheet is drawn from the post-fix classes.
**Acceptance:** the 433 rows are no longer `reject`, `score_ticker_rules.py --check` is re-measured and
the PLAN figures re-frozen, a fresh 60/40 sheet passes the T-057 criterion, and `portfolio-nlp` gets the new
removed and `review` counts. No schema change.

## Sequencing

Work items 3, 4, 5, 6, 7 and 9 are closed (merged or verified; tasks in `CHANGELOG.md`). Work
items 1–2 stay reverted/on hold at the maintainer's prior request. Work item 8 (`T-045`) is
open and active. Work item 10 (wrong-company tagging, requested by `portfolio-nlp`) is open;
`portfolio-nlp`'s Work item 15 development waits on it, so it takes priority over any new work,
alongside the already-active Work item 8.
Otherwise there is no ordering constraint from the rest of the backlog, since every other `SPEC.md` §13
item is accepted (Non-goals above) and not touched by this plan.

See `TASKS.md` for the discrete, checkable task breakdown.
