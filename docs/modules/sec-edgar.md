# sec_edgar — SEC EDGAR filings & financials

**Source:** `finhub/src/fundamental/edgar_tool.py`, `finhub/edgar_examples.py` →
`src/sec_edgar/` + `apps/sec_edgar_api.py`

The other of the two modules `finhub` was split into (the other is [pricing](pricing.md)).

Agent-facing wrapper (`src/sec_edgar/agent.py`'s `EdgarAgent`) around the `edgar`
(`edgartools`) SEC EDGAR library. Every public method returns a plain JSON-serializable
dict — never a custom SDK object — as either `{"success": True, "data": ...}` or
`{"success": False, "error": "<message>"}`; no method raises during normal use.

> **Naming note:** this module is `sec_edgar`, not `edgar` — `edgartools` itself is
> imported as `edgar` (`from edgar import set_identity, Company`), and a local package
> literally named `edgar` would shadow that import once `src/` is on `sys.path`.

`src/sec_edgar/examples.py` (adapted from `edgar_examples.py`) has runnable example calls
against `EdgarAgent`; `docs/modules/edgar_examples.txt` is the captured output from the
original run, useful as a response-shape reference without needing a live SEC EDGAR call.

## Running

```bash
# API (FastAPI/uvicorn)
.venv\Scripts\python.exe apps\sec_edgar_api.py
# -> http://127.0.0.1:8005/docs

# CLI (direct — no server), one subcommand per endpoint, prints JSON
.venv\Scripts\python.exe cli\sec_edgar_cli.py company-info AAPL
.venv\Scripts\python.exe cli\sec_edgar_cli.py --help   # full subcommand list
```

Requires `NAME` and `EMAIL` in `.env` (SEC EDGAR requires a real identity string for
programmatic access — see `.env.example`) for both.

`cli/sec_edgar_cli.py` is new — `finhub` never had a CLI, only the FastAPI app and the
fixed-ticker demo in `src/sec_edgar/examples.py`; this wraps the same `EdgarAgent` calls
the API routes make, with real ticker/form/year arguments instead of hardcoded "AAPL".

## Endpoints

`GET /edgar/company_info/{ticker}`, `/edgar/filings/{ticker}`,
`/edgar/years_available/{ticker}`, `/edgar/filing_by_year/{ticker}`,
`/edgar/latest_filing/{ticker}`, `/edgar/financials/{ticker}`,
`/edgar/search_filings/{ticker}`.

> **Note:** `/edgar/filing_by_year` returns a *list* of matching filings — a
> form/year can have more than one (e.g. a company typically files three
> "10-Q"s per year, one per fiscal quarter), and an empty list means no
> match, not an error. `/edgar/financials` takes an optional
> `accession_number` query param, required to disambiguate when form+year
> matches more than one filing; get it from `/edgar/filing_by_year` first.

> **Note (T-118, APA revenue instance only):** `/edgar/financials`' `income_statement`
> runs through `correct_revenue_totals` (`src/sec_edgar/agent.py`) before it's returned.
> The actual mechanism, confirmed against live SEC data (CIK `0001841666`, APA's FY2023
> and FY2024 10-Ks): `edgar.xbrl.statements.income_statement().to_dataframe()`
> *synthesizes* a non-dimensional "total" row for a concept by summing that concept's
> dimensional members (e.g. `srt:ProductOrServiceAxis` breakdown rows) whenever the filer
> didn't tag a non-dimensional fact for it directly. That summation double-counts when one
> of the members is itself a parent whose value already includes its own children — APA's
> case: FY2023 `8279 (parent "Oil") + 7385 (children rolled into it) + 894 (unrelated
> member) = 16558`, a row edgartools then surfaces as if it were APA's own consolidated
> `us-gaap:Revenues`, when APA has never filed that concept at all (`data.sec.gov`
> `companyconcept` → 404) and the correct figure is `8279`. Same shape in FY2024:
> `9737 + 8196 + 1541 = 19474` vs. the correct `9737`. `correct_revenue_totals` detects
> this structurally for revenue only (a later, non-dimensional row whose label also reads
> as a revenue total and is materially smaller than the synthesized one — never a filer's
> own concept name), corrects it by subtracting the rows between the two when they're
> individually small enough to trust, or drops it (`None`, not guessed) otherwise, and
> now records every correction/drop it makes as a `{"concept", "column", "original",
> "corrected", "rule": "T-118"}` entry in a new top-level `data["corrections"]` list
> returned by `get_financials` — so callers can see exactly what was touched instead of
> trusting the numbers silently.
>
> **This was confirmed to be a general `edgartools` synthesis defect, not an APA/revenue
> quirk** — a full-universe scan of every stored filing found 163 mismatched
> parent-vs-summed-children values across 105 concept/period pairs, spanning 1,101
> synthesized non-dimensional rows total. `correct_revenue_totals` fixes **APA's revenue
> instance only**; it does not by itself close `portfolio-financial-analysis`'s
> `T-117`/`T-118` (that repo's own local guard in `docs/model_fixes.md` stays in place as
> the load-bearing backstop, not a redundant one) — see `T-042` immediately below for the
> general fix that's meant to.

> **Note (T-042, the general fix):** `/edgar/financials` also runs
> `reconcile_with_filed_facts` (`src/sec_edgar/agent.py`) on all three statements
> (`income_statement` — after `correct_revenue_totals`, `balance_sheet`, `cash_flow`)
> before returning. Where `correct_revenue_totals` detects the contradiction shape by label
> pattern and reconstructs a value, `reconcile_with_filed_facts` instead validates every
> remaining non-dimensional row, any concept, directly against the filing's own XBRL facts
> (`xbrl.facts.query().by_concept(...).by_dimension(None)` — the already-loaded filing, no
> extra network call): a rendered value matching a genuinely filed non-dimensional fact for
> that concept/period (by magnitude — a same-magnitude, opposite-sign match is `edgartools`'
> own presentation-layer sign convention for contra accounts/cash-flow decreases, not a
> defect, and is left alone) passes through untouched; one with no filed non-dimensional
> fact at all for that concept/period is dropped (`None`) — there's no general way to
> reconstruct it the way `T-118`'s label-based subtraction can for revenue, which is why
> that layer still runs first and `T-042` skips whatever it already corrected. A column
> whose period can't be parsed, or where more than one filed value matches ambiguously, is
> left alone rather than guessed. Corrections/drops from both layers share one
> `data["corrections"]` list, now with a `"statement"` field and, on `T-042` entries, a
> `"reason"` (`"no_filed_nondimensional_fact"` or `"filed_value_mismatch"`).
>
> **Known conservative gap:** a cash-flow statement's "beginning of period"/"end of period"
> cash balance is rendered under a duration column but genuinely filed as an *instant*
> fact at the period's start/end date respectively (live-verified against MSFT's FY2024
> 10-K). An earlier version of this fix tried falling back to an instant lookup at the
> column's end date to catch that shape — but the "beginning of period" row needs the
> period's *start* date, and both rows share one concept with no reliable way to
> distinguish them here, so the fallback silently substituted the wrong endpoint's value
> (caught live, not shipped). `reconcile_with_filed_facts` drops that one row instead:
> loses the value, never corrupts it. Live-verified elsewhere as low-noise: MSFT's FY2024
> 10-K flags only that one known shape; SNA's FY2026 10-Qs (segment-breakdown-heavy
> filings) correctly pass through non-dimensional rows that are genuinely filed (e.g.
> `us-gaap:OperatingIncomeLoss` "Operating earnings") while dropping ones that aren't (e.g.
> `us-gaap:OperatingExpenses`, never filed non-dimensionally).
>
> This closed `T-042` (Work item 5, now in `CHANGELOG.md`). It did not by itself close
> `portfolio-financial-analysis`'s `T-117`/`T-118` — that repo re-verified against the
> redeployed `sec_edgar` itself and closed them in its PR #104 on 2026-09-30 (`T-041`).
>
> **Failure isolation (PR #45 review):** `reconcile_with_filed_facts`' `xbrl.facts.query()`
> call is live and can fail for a given statement (an unusual filing shape, a transient
> issue). `get_financials` calls it through `_safe_reconcile_with_filed_facts`, which
> catches that failure per statement and returns that statement's rows rendered but
> unvalidated (no `T-042` corrections for it) rather than letting the exception reach
> `get_financials`' one outer `try/except` — which would otherwise convert the *entire*
> response to `{"success": False}` and discard the other two statements'
> already-successfully-rendered data along with it. `reconcile_with_filed_facts` itself is
> unchanged and still raises on failure, so it stays directly unit-testable.
>
> **Surfacing that failure (PR #46 review):** the catch above is deliberately broad
> (`except Exception`, not a narrower "expected filing-query failure" list) — `reconcile_
> with_filed_facts` runs against a third-party library's live, evolving internals, so there
> is no fixed, enumerable set of exception types to narrow to, and missing one would
> silently reopen the exact failure this wrapper exists to prevent; it isn't needed to catch
> a bug in this module's own logic either, since `reconcile_with_filed_facts` is separately
> unit-tested and unaffected by this wrapper. What the broad catch must not do is make a real
> failure look identical to "verified, nothing to correct": a failure now logs at `error`
> level with a full traceback, and `get_financials` records it in a top-level
> `data["reconciliation_errors"]` list (`[{"statement", "error"}]`, empty when nothing
> failed) alongside `data["corrections"]`, so a caller can always tell the two cases apart.

> **Cover-page share count (T-043):** `/edgar/financials` also returns
> `data["cover"] = {"shares_outstanding": [{"value", "as_of_date", "class_member"}], "error"}` —
> the filing's own cover-page share count, for a point-in-time market cap
> (`portfolio-financial-analysis`'s `T-132(a)`). Purely additive: the three statements,
> `corrections` and `reconciliation_errors` are unchanged. It is read from the
> already-loaded filing's `dei:EntityCommonStockSharesOutstanding` XBRL facts
> (`xbrl.facts.query()`, no extra network call) — never `CommonStockSharesIssued` (PG: ~4.0B
> issued vs. ~2.32B outstanding) or a weighted average. `as_of_date` is the fact's own
> instant date, the cover page's "as of" date, usually after the period end; it is returned
> exactly, never replaced with the period end. `class_member` is `null` for a
> non-dimensional fact, else the `us-gaap:StatementClassOfStockAxis` member (GOOGL: Class A
> `us-gaap:CommonClassAMember`, B, and C `goog:CapitalClassCMember`; BRK-B: A and B). A
> multi-class filer gets one entry per class, plus a `null`-member total only if the filer
> actually filed one — never one summed here (T-042's rule). Entries are sorted (total first,
> then by member name). Missing → `[]`, never a guess; applies to 10-K and 10-Q alike.
>
> A fact carrying any dimension other than the share-class axis is not returned. Live
> verification found NEE's filings also carry `1,000` shares under `dei:LegalEntityAxis`
> (Florida Power & Light, a co-registrant, listed separately on NEE's cover) — not a NEE share
> class, so reporting it would mislabel another registrant's count.
>
> **Failure isolation:** a failed read goes through `_safe_cover_shares_outstanding`, the same
> pattern as T-042's: `shares_outstanding` comes back `[]` and `data["cover"]["error"]` carries
> `"<ExcType>: <message>"` (`null` on success) — the response still succeeds, and "failed to
> read" stays distinguishable from "filer filed none" (empty list, `error` null). The failure is
> deliberately **not** added to `reconciliation_errors` (PR #48 review): that list means "a
> statement came back unvalidated", and `portfolio-financial-analysis` (its PR #104) rejects the
> whole filing when it is non-empty — a cover failure leaves every statement valid, so it would
> discard an otherwise good filing. `reconciliation_errors` stays about the three statements only.
>
> **Verifying against SEC:** SEC's `companyconcept/CIK##########/dei/
> EntityCommonStockSharesOutstanding.json` carries only non-dimensional facts, so it cannot
> confirm per-class counts — check multi-class filers against the filing's cover page instead.
> It also lags (XOM's and NEE's newest 10-Qs were absent) and had no rows at all for HUM, whose
> cover page matched the returned value. Live-verified figures: `SPEC.md` FR-004,
> `TASKS.md`/`CHANGELOG.md` `T-043`.
