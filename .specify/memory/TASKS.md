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

## Work item 10 — Stop tagging articles with the wrong company (code + data, origin: `portfolio-nlp` T-128)

Requested by `portfolio-nlp` (its Work item 15, decision D3 = fix at the
source, 2026-10-14): the discovery matcher `BaseConnector._company_pattern`
accepts a ticker or the *first word* of the company name appearing anywhere in
a URL slug or title, so tickers that are English words (`A`, `ON`, `IT`,
`ARE`, …) and generic first words (`Trade`, `Bank`, `First`, `Dollar`, …) tag
unrelated articles. → `PLAN.md` Work item 10, `SPEC.md` §13 item 10 and FR-014.

Task IDs: `T-047`–`T-052` are intentionally unused here. `portfolio-nlp` already
cites `T-053`–`T-059` for this work item (its Work item 15, PR
`gamug/portfolio-nlp#102`), so the IDs were kept stable rather than renumbered
after the fact; IDs are never reused, so they stay reserved. `T-060` (content-quality
gate) and `T-062` (Work item 12, the matcher follow-up; `T-061` belongs to Work item 11, the leaver prices) were added later and are data-mining-only, so `portfolio-nlp` does not cite them.

- [x] **T-053** Commit a rerunnable, read-only audit under `scripts/` that
      reproduces the 2026-10-14 counts from `urls.db` per `(ticker,
      connector, strategy/source)`: ticker-only vs name-token-only vs both
      matches, the eight English-word tickers, the generic-first-word
      companies, and the `ddg` rows not yet broken out. Recompute every
      figure quoted in `PLAN.md`/`SPEC.md` §13 item 10 per connector, and
      count separately the rows only the substring shortcut
      (`ticker.lower() in url.lower()`, failure mode 3) would accept. → step 1.
      Done: `scripts/audit_ticker_tagging.py` (every breakdown is derived from
      `(ticker, company, domain, source)` cells, so the word-ticker and
      generic-word tables are split per connector/source); results in
      `PLAN.md` Work item 10.
- [x] **T-054** Measure each failure mode's real error rate. Build **one**
      shared labeled sample of `(url, ticker)` pairs from `urls.db`,
      stratified by connector and match mode (ticker word-boundary / ticker
      substring shortcut / name-token-only / both) and including
      known-correct pairs, and label each pair (is the article about that
      company: `yes`/`no`/`unclear`). **Labeling is done in this repo, by hand;
      it does not depend on `portfolio-nlp`** (data flows from this repo to
      that one, never back). Commit the sample (pairs + labels) so it can be
      reused: `portfolio-nlp` T-117 reads it, and the retention
      measurement of the implemented rule (the T-057 fresh check) is added to the same
      sample's evidence. The committed file carries
      `url`, `ticker`, `company`, `match_mode`, `label`, `labeled_by`
      (`hand`/`assistant`) and the assistant's `assistant_label`. Report; the
      rule is chosen from this, not assumed. → step 1.
      Built: `scripts/ticker_tagging_sample.py` + `data/ticker_tagging_sample.csv`
      (263 pairs, all hand-labeled by the maintainer; an assistant's first pass
      agreed on 91%), `data/ticker_tagging_manual.csv` (the **filled** labeling
      sheet with the maintainer's notes, excerpts removed) and
      `scripts/score_ticker_rules.py` (reproduces the prototype-rule table);
      results and the duplicate/quality analysis are in `PLAN.md` Work item 10.
      The maintainer reviewed the two labels that looked inconsistent with
      their notes, `MGM` id 317362 and `DE` id 180987, and approved them as
      `no` (2026-10-15); known-correct retention of record is 17/20.
- [x] **T-055** Record the matching-rule design (starting direction:
      explicit ticker forms only, full normalized name + curated aliases,
      common-word stoplist for a lone first word; no new dependency) with the
      T-054 numbers behind it, and get it approved before coding. Division of work
      decided (`PLAN.md` Work item 10 step 2): strict deterministic rules here,
      semantic relevance in `portfolio-nlp`, questionable pairs quarantined. Decide, with the
      per-path rates in `PLAN.md` (`ddg` 7% wrong, `sitemap` 73%): use the
      company-name search query as evidence for `ddg` rows (and record the query
      per row), and attribute each `sitemap` article once instead of testing
      every company against every URL. The same
      decision sets the **measurable targets** — a maximum wrong-company rate
      and a minimum retention of known-correct pairs on the shared labeled
      sample — and writes both numbers, plus the final rule, into `SPEC.md`
      FR-014 and the `PLAN.md` acceptance criteria before any code is written.
      **Design approved 2026-10-15 (maintainer: "take care not to exclude").**
      Validation sample (`data/ticker_validation_sample.csv`, 100 fresh pairs,
      labeled by the maintainer) showed the first draft missing its 15% target
      (`accept` 20.7% wrong); fixed (ordinary-word names, `&`/`and`, abbreviation
      list, aliases; `ddg` promotion withdrawn) and re-scored in-sample. **Closed
      2026-10-15:** the targets are written into `SPEC.md` FR-014 and the `PLAN.md`
      acceptance criteria (wrong rate among `accept` at most 15%, correct pairs kept
      at least 95%, known-correct `accept` at least 90%); they are verified by the
      fresh hand-labeled check in T-057's dry run (60 `accept`, 40 `reject`), not
      here. Outcomes of the open questions: the `ddg` query is stored as provenance
      only (it does not change a class), and each sitemap article is **not** decided
      once across companies; the class is per `(url, ticker)` pair and
      `multi_company` is derived (T-056c). All figures reproduce with
      `scripts/score_ticker_rules.py` and `ticker_tagging_sample.py report`;
      details in `PLAN.md` Work item 10. → step 2.
- [x] **T-056** *(done 2026-10-15: `a`, `b`, `c`; split into `T-056a`, `T-056b`, `T-056c` so each piece is
      reviewable and shippable; the IDs stay sub-numbered because `portfolio-nlp`
      cites only `T-053`–`T-059`. Order: `a` (no schema) → `b` (constitution #10
      confirmed 2026-10-15, starts after PR #58 is reviewed) → `c`; T-057 starts after all three.)* Implement the approved
      three-class rule. `T-056` is done when all three are.
- [x] **T-056a** *(no schema change; done 2026-10-15, PR #58)* The matcher and the connectors. Put the pure
      module in the **shared library** (`data_mining/matching.py`; both `news_collector`
      and `extractor` already import `data_mining.db`/`schema`, so the direction stays
      stages → `data_mining` and the two stages import nothing from each other): the logic
      of `scripts/score_ticker_rules.py` `design_class` (alias map, ordinary-word-name
      rule, `&`→`and`, abbreviation list, path-only URL matching, no new dependency),
      returning the class and the deciding signal (`name`, `alias`, `explicit_ticker`,
      `upper_ticker`, `weak_ticker`, `weak_first_word`, `abbreviation`, `none`). Expose it to
      the connectors through one new **concrete** `BaseConnector.classify_match(url, title,
      company, ticker) -> "accept" | "review" | "reject"`; the abstract
      `url_matches_company` keeps its signature and, in each of the **seven** connectors
      that override it (`cnbc`, `ft`, `investing`, `nasdaq`, `seeking_alpha`, `stocktwits`,
      `yahoo_finance`), returns `classify_match(...) != "reject"`; the raw
      `ticker.lower() in url.lower()` shortcut the last four ran first is removed; the
      base-class docstring postcondition ("ticker OR significant company name token is
      sufficient") is rewritten. The rule is monotone (more text can only add evidence), and
      a test locks that. Hermetic parametrized tests **per connector** for all three
      classes (ambiguous tickers `A`/`ON`/`IT`/`ARE`, generic first words `Bank`/`Trade`/
      `First`/`News`, aliases, `&`/`and`, abbreviations → `review`, the substring cases, host
      not matched); only `test_cnbc.py` covers `url_matches_company` today (and asserts the
      permissive rule), so update it deliberately and add the six missing files.
      **Done when:** `ruff check`, `ruff format --check`, `mypy`, `pytest` clean;
      FR-014's tests (per connector) pass; the base-class docstring and
      `docs/modules/news-collector.md` describe the matcher; mergeable without `b`/`c`.
      **`api` feeds are out of the rule's scope:** the `stocktwits` and `yahoo_finance`
      connectors build `DiscoveredURL(source="api")` directly from ticker-keyed feeds
      (message URLs, provider news; no company in the URL), the rule was never measured
      on them and the corpus holds none, so they keep `accept` by construction
      (`match_reason = 'feed'`), stated in FR-014; `rss` entries go through the sitemap
      strategy's filter and **are** classified, with their feed title. → step 3.
      **Results (T-056a, PR #58):** signals as above plus `cashtag`. Exchange forms (`NYSE: X`,
      `Nasdaq: X`, `NYSE American`, `NYSE MKT`, `NYSEARCA`, parenthesised or not) accept; a bare `(X)`
      does not; `$X` accepts, but for an ordinary-word ticker or an abbreviation it is `review`.
      Corpus class sizes: re-tag `accept` 104,614 / `review` 51,295 / `reject` 325,423; discovery
      86,985 / 63,162 / 331,185. `score_ticker_rules.py --check` reproduces the PLAN table (both
      samples, both modes) and exits 1 on drift. Known limitation, measured with `--names`: 59,956
      `accept` rows rest on a one-word name, about 12,400 of them dictionary words (Apple 9,890).
      T-057's fresh check is the acceptance evidence.
- [x] **T-056b** *(done 2026-10-15; shared-schema change; constitution AI behavior #10 **confirmed by the
      maintainer on 2026-10-15** for the exact columns, the `skipped`+`reject` convention, the
      queue upsert and the extractor's promote-only update listed here: they touch the
      two-package DB contract. Starts after PR #58 is reviewed; cite the confirmation and its
      date in this task's PR description.)* Storage, queue
      and API. **Migration via the real mechanism in `data_mining/schema.py`:** add the
      columns to `_DISCOVERY_DDL`, bump `SCHEMA_VERSION` 1 → 2, add a `run_migrations`
      step calling `db.ensure_columns("discovered_urls", {...})` (the pattern of
      `_migrate_legacy_sector_column`; forward-only, idempotent) and an index
      `idx_match_class` on `discovered_urls(match_class)`; a test upgrades an old-schema
      database and leaves a current one untouched. Columns: `match_class TEXT NOT NULL
      DEFAULT 'accept'`, `match_reason TEXT`, `query TEXT` (the `ddg` search query that
      returned the row; provenance only, it never changes the class). **Status
      convention, distinct from the existing `skipped`:** `URLStatus` already has
      `skipped` (paywall / irrelevant after inspection; `mark_skipped`/`mark_skipped_by_id`
      set it and write their reason into `title`). A matcher-rejected pair is
      **`status = 'skipped' AND match_class = 'reject'`**, written at insert time
      (`DiscoveredURL` gains `match_class`/`match_reason`/`query`; the queue's insert path
      writes `status = 'skipped'` for `reject` and never touches `title`); `mark_skipped`
      keeps its meaning and is never used for rejects. It is not the default `'pending'`
      because the extractor (`extractor/db.py` `get_urls_by_status`/`get_pending_urls`) and
      the queue (`storage/queue.py` `get_pending` and the per-ticker counts filtering
      `status='pending'`) select by `status` only. **Upsert, not `INSERT OR IGNORE`:**
      `enqueue_batch` ignores a duplicate `(url, ticker)`, which would discard a promotion
      when a pair first stored as `reject` (sitemap, no title) is rediscovered with a title
      (`ddg`). On conflict keep the higher class (`reject` < `review` < `accept`), fill
      `query`/`match_reason` when null, and reset `status` from `skipped` to `pending` when
      the class rises above `reject`. **API:** `/discovered` responses include
      `match_class` and `match_reason`, the list gains a `match_class` filter, and the
      `status` filter description says `skipped` includes matcher-rejected rows. Tests: the
      migration; a `reject` row is never returned by `get_pending_urls`/`get_pending` and an
      `accept` row is; the insert keeps a `reject` row's `title`; `mark_skipped` leaves
      `match_class` alone; sitemap-then-ddg re-discovery promotes the row and resets
      `status`; the API listing and filter. **Done when:** the same four gates clean, the migration test and the `schema.py` docstring (`SCHEMA_VERSION` 2) updated, `SPEC.md` FR-014 and `docs/modules/news-collector.md` describe the columns, and the maintainer's #10 confirmation is recorded here. → step 3.
      **Results (T-056b):** the promotion runs in `URLQueue._promote_duplicates` (portable SQL, the dialect's
      `upsert` can only overwrite with `excluded.c`), after the insert; a row skipped for another reason (paywall)
      is not reset by a promotion. `match_class` has a `CHECK` constraint and `enqueue_batch` validates it before any write; `stats()` gains `by_match_class`; the promotion UPDATE is conditional in SQL (no stale status write-back, no demotion by a stale read) and counted (`last_promotion`, `promoted=` in the logs); the extractor's `get_status_counts`/`/status`/CLI line leave rejects out; `get_urls_by_status` excludes `reject` rows and the crawler's reset answers 409 for one. The API surface is the collector's `/discovery/query` and `/discovery/pending`
      and the crawler's `/discovered` (the task text's `/discovered` is the crawler's route). Existing rows default to
      `accept` until the T-057 re-tag. The strategies still build `DiscoveredURL` with the default class, so nothing
      is quarantined until T-056c.
- [x] **T-056c** *(done 2026-10-15)* Strategies, fetch policy and re-evaluation (after `a` and `b`).
      **Strategies stop dropping:** `ddg` and `sitemap` (`_dedupe_and_filter`) call
      `classify_match` and keep `review` and `reject` rows with their class and (for `ddg`)
      `query` instead of `continue`, so the quarantine exists for new crawls; `accept`
      and `review` rows stay `pending` (a `review` pair needs its body for
      `portfolio-nlp`'s decision), `reject` rows are `skipped`. **Discovery vs fetch:** the
      class is computed at discovery from the URL (and the title for `ddg`), then
      **re-evaluated after the fetch** with the stored title; monotone, so it can only
      promote, never demote. **Who re-evaluates:** the extractor, through its existing
      post-fetch update of `discovered_urls` (`extractor/db.py`), calling
      `data_mining.matching` (no import of `news_collector`) and writing only
      `match_class`/`match_reason`, guarded in SQL so it can only promote. It applies to
      **fetched** rows, i.e. `accept`/`review` ones (`review`→`accept` when the title
      supplies the evidence). A `reject` row is never fetched, so a `sitemap` `reject` row
      (no title at discovery) stays `reject` unless a feed title was supplied with it
      (`entry.title` of an RSS entry). Accepted cost: about 5,800 pairs that are `reject`
      at discovery but would be `review` or `accept` with a title are never fetched;
      `hidden as reject` was 0.0% in discovery mode on both labeled samples, and the
      T-057 check measures it again. **`multi_company` is derived, not stored:** a URL
      with two or more `accept` pairs is multi-company (108,313 of the 320,763 stored
      URLs carry several tickers); the pair stays one row per `(url, ticker)`. Tests: the
      strategies keep `review`/`reject` rows with their class and `query`; the extractor
      promotes a fetched `review` row and never demotes. **Done when:** the four gates clean, `scripts/score_ticker_rules.py --check` exits 0 (or PLAN table and `EXPECTED_FIGURES` are updated together), the strategy and extractor tests pass, `docs/modules/news-collector.md` and the architecture artifacts describe the fetch policy (AI behavior #11, never renamed). → step 3.
      **Results (T-056c, PR #60):** the strategies keep every class with `match_reason` and, for `ddg`,
      the `query`; `api` feed rows (`stocktwits`, `yahoo_finance`) carry `match_reason = 'feed'`; the
      orchestrator's merge keeps the higher class of a pair found twice (and any `query`); discovery
      counters report `rejected=` (part of `inserted=`, never fetched). `extractor.db.record_fetch`
      (save, status, re-evaluation of a page that was read) is the one post-fetch step used by the
      crawler CLI and both API endpoints; `POST /extract/{id}` answers 409 for a `reject` row.
      `scripts/score_ticker_rules.py --check` exits 0 ("figures match PLAN"; no matcher change since T-056a). Test counts live in the PR description.
- [x] **T-057** *(done 2026-10-15: PR #61 tooling, real run on `urls.db` after the hand check passed)* Re-tag `urls.db`: dry run on a copy (before/after counts per
      ticker and source; count URLs also tagged to a correct ticker), then the
      real run with a restorable backup and a reversible representation
      (quarantine over delete) that `portfolio-nlp`'s pending queries
      (`fetch_status = 'ok'`, non-empty `body_text`) will actually exclude.
      Proposed shape, to agree with that repo first: set the `articles` row of
      every `review`/`reject` pair to `fetch_status = 'quarantined'` (their query
      already filters on `'ok'`, so no change there is needed to stop reading it),
      keeping the previous status in a new `articles.prev_fetch_status` column (the class
      and reason stay on `discovered_urls` as `match_class`/`match_reason`, joined on
      `id`) so clearing the flag restores the row exactly; the `accept` rows are
      untouched, `discovered_urls.status` of already-fetched rows is **left as it is**
      (`fetched`/`failed`; the class is read from `match_class`, and `/discovered`
      now exposes it), and `reject` rows that were never fetched have no `articles` row (their
      `discovered_urls.status` is already `skipped`). The run also writes the list of
      removed `(url, ticker)` / article ids and the `review` quarantine list
      (class and triggering evidence), which T-058 hands over. Before the real
      run, the fresh hand-labeled check must pass: **60 newly `accept` and 40
      `reject` pairs**, drawn after the rule is frozen; pass = at most 5 of the 60
      `accept` wrong and at most 1 of the 40 `reject` correct, otherwise back to T-055
      (FR-014, `PLAN.md`). → step 4.
      **Progress (T-057):** `articles.prev_fetch_status` added (schema version 3, confirmed 2026-10-15,
      recorded in `portfolio-nlp` PR #102 T-132/T-128); `data_mining/quarantine.py`; `record_fetch` quarantines/
      releases after every fetch (a fetched `review` pair would otherwise read `ok`) and a rediscovery promotion
      releases the article too; `scripts/retag_urls.py`
      (dry run by default; `--apply` needs `--backup` and `--hand-check SHEET`, which it scores with the verdict, validates as the 60/40 fresh sheet drawn under the current rule and records, and aborts before writing if the corpus moved since the plan). Dry run and apply on a copy of
      the 481,332-row corpus (2026-10-15): accept 104,614 / review 51,295 / reject 325,423 (the PLAN figures),
      376,718 articles quarantined, no pending rows (every row already has an article), restoring the flag
      reproduces the backup exactly, a second run changes nothing, integrity and foreign-key checks clean;
      26,044 `reject` and 9,343 `review` URLs are also `accept` for another ticker. By previous status: `review` 49,645 `ok` / 1,153 `thin_content` / 497 `failed` (only the `ok` ones are decidable by T-132), `reject` 316,243 / 8,197 / 983. `--apply` verifies the result against the plan and exits 1 (no lists) on a mismatch. **Real run (2026-10-15):** hand check PASS (60 `accept`: 1 wrong, max 5; 40 `reject`: 1 correct, max 1; 1 `unclear`), sheets
      committed (`data/ticker_retag_check_*.csv`). `--apply` on `/workspaces/thesis/data/urls.db`, backup `urls.pre-retag.db`: `verified: true`,
      481,332 rows re-tagged (accept 104,614 / review 51,295 / reject 325,423), 376,718 articles quarantined, lists in
      `data/retag/` (git-ignored: `removed_articles.csv` 325,423, `review_quarantine.csv` 51,295, `retag_summary.json`). Label vocabulary is `yes`/`no`/`unclear`.
      The one `reject` row labeled `yes` (id 302365, KLAC `kla-corporation-…-earnings-call-presentation`) is a rule gap, not a
      one-off, and the check passed at its limit. Three causes: a name that is short or a stopword once the suffix is removed
      (`kla`, `southern`, `news`; no `review` fallback), a possessive written as a trailing `s` in the slug (`bank-of-americas`), and an `&`
      the URL drops. `scripts/known_false_rejects.py` (lower bound: the name as whole words of the URL path) finds **at least 433 pairs
      on 234 URLs** (News Corp 400 as `NWS`+`NWSA`, `KLAC` 15, `SO` 11, `BAC` 4, `DG`/`IFF`/`TTD` 1), committed in
      `data/retag_known_false_rejects.csv`, quarantined not deleted; the hand check estimates 2.5% of the `reject` class (95% interval
      0.4%-12.9%). Accepted as a recall loss in `PLAN.md`, reported to `portfolio-nlp` as "do not purge". Real `urls.db`:
      `PRAGMA integrity_check` ok, `foreign_key_check` empty.
- [x] **T-058** *(done 2026-10-15: hand-off comment on `portfolio-nlp` PR #102 with counts, lists and the selection; follow-up comment 2026-10-16 correcting the known false rejects from 28 to at least 433 pairs, `T-062`)* Hand off: tell `portfolio-nlp` the corrected corpus is ready, with the
      list of removed article ids (it needs them to clean its existing results
      and `sector_summary` — its T-131) and the quarantined `review` pairs
      (its `T-132` decides them; nothing is excluded on doubt there). **One agreed
      selection, cited by both repos:** `portfolio-nlp` T-132 reads
      `SELECT a.id, d.ticker, d.company, d.match_reason FROM articles a JOIN
      discovered_urls d ON d.id = a.id WHERE a.fetch_status = 'quarantined' AND
      d.match_class = 'review'`, while its default pipeline query keeps
      `fetch_status = 'ok'` (so `accept` rows only) and never sees `reject` ones
      (unblocks its Work item 15 development and its re-measurement on the
      cleaned data); close `portfolio-nlp` `T-128`. → step 5.
- [x] **T-060** Content-quality gate on `articles`, so a login wall, bot
      challenge or empty body can never reach `portfolio-nlp`. Today the only
      protection is the extractor's length check (`thin_content`) plus
      `fetch_status`; checked 2026-10-15 every wall seen in the labeling sheet
      is already `failed`/`thin_content` and `nlp.db` holds only `ok` rows, but a
      wall long enough to pass the length check would be marked `ok`. Add a
      detector for known wall/challenge/paywall phrases at extraction (e.g.
      "Security Verification", "Just a moment", "unusual traffic", sign-in and
      subscribe prompts) that leaves such rows non-`ok`, a rerunnable read-only
      audit of `ok` rows per domain (phrases, word-count floor, duplicate
      bodies), and tests on real excerpts including the false positives found
      (CNBC articles that contain "just a moment" or "CAPTCHA" in normal prose).
      Separately report the duplicate-processing finding (302,512 distinct
      bodies among 459,112 `ok` rows) so `T-055`/`T-056c` can decide to attribute
      each URL once. → quality of what step 4 hands over.
      **Done:** `extractor.parse.wall_kind` looks for whole-word wall phrases in the first 300 characters of the
      body only. A bot challenge (`Security Verification`, `Just a moment`, ...) is stored `failed` (transient:
      `--retry-failed` retries it), a login or subscribe prompt `paywalled` (gated: not retried); neither is `ok`.
      `scripts/audit_article_quality.py --by-status` ran the final detector over all 481,332 real bodies
      (2026-10-16): `failed` 578 hits of 9,634, `quarantined` 198 of 376,718, `ok` 0 of 93,224, `thin_content` 0 of
      1,756. The 776 hits are exactly the ft.com `Security Verification` pages (578 + 198, the counts of the earlier
      raw scan); nothing else. The scan also found three head false positives that shaped the rules: "just a
      *momentary* blip", "investigating unusual traffic that might be from hackers" and a quarantined CNBC article
      (id 78699) opening "For just a moment, mentally fast-forward...". So phrases are whole-word, `unusual traffic`
      needs `from your`, and `just a moment` / `access denied` count only at the very start of the body. The real
      excerpts are fixtures in `test_parse_body.py`.
      The audit (read-only, per domain: walls, under the word floor, hashed duplicate bodies; `--list` prints wall ids;
      exit 1 on a wall or an under-floor row) also covers rows quarantined from `ok` (`held`, `held walls`), since a
      release restores `ok`. Real `urls.db`: 93,224 `ok` + 366,000 held rows, 0 walls, 0 under the floor. The
      duplicate-body finding stays reported, not acted on (it is `T-055`/`T-056c` territory). Tests: 771.
- [ ] **T-059** Close-out: `SPEC.md` §13 item 10 annotated and FR-014 moved from planned to done,
      `docs/modules/news-collector.md` updated, both architecture artifacts
      reconciled (never renamed), then move this work item to `CHANGELOG.md`
      (constitution AI behavior #12). Also: decide how long `urls.pre-retag.db` (next to `urls.db` in
      `/workspaces/thesis/data/`) is kept, and who holds the git-ignored `data/retag/` lists
      (regenerate them with `retag_urls.py --out` on a copy of the backup if lost; the known false rejects are committed).
      FR-014 is "implemented with the known recall gap in `T-062`", not "done". `T-062` is its own Work item 12 (PLAN), so item 10 closes
      here and the `CHANGELOG.md` entry names `T-062` as the open successor. → acceptance criteria.
- [ ] **T-062** *(Work item 12; follow-up found by the T-057 real run; changes the matcher, so the maintainer schedules it)*
      Fix the full-name false rejects: at least 433 `reject` pairs on 234 URLs whose URL spells the company
      out (`data/retag_known_false_rejects.csv`, `PLAN.md` Work item 10 "Accepted recall loss"). Three causes in
      `matching._name_reason`: a name that is short or a stopword once the suffix is removed (`kla`, `southern`, `news`),
      a possessive written as a trailing `s` (`bank-of-americas`), and an `&` the URL drops. Make a full-name hit at
      least `review` (never `reject`), normalize the possessive and `&`, then re-class the quarantined rows with a
      targeted pass (the re-tag only touches `match_reason IS NULL` rows) and `release_articles` for any that become
      `accept`. Acceptance: the 433 rows are no longer `reject` (update `known_false_rejects.py --check` and
      `tests/scripts/test_known_false_rejects.py`), `score_ticker_rules.py --check` is re-measured and `PLAN.md`'s figures
      re-frozen, a fresh hand check (new 60/40 sheet, drawn from the post-fix classes) passes the T-057 criterion, and `portfolio-nlp` is told the new
      counts of removed and `review` rows (its T-131/T-132 inputs change). Constitution AI behavior #10 does not apply
      (no schema change). Tooling for that sheet: add `data/ticker_retag_check_sample.csv` to `EARLIER_SAMPLES` and to the
      `--final` exclusions in `ticker_validation_sample.py`, and draw with `--final --out <new path>` (the default output is the T-057 sheet and must not be overwritten).

## Status

Closed Work items 3, 4, 5, 6, 7, 9 and 11 are in `CHANGELOG.md` (Work item 5 closed 2026-10-02 once
`portfolio-financial-analysis` re-verified the redeployed gateway and closed its own
`T-117`/`T-118` in its PR #104; Work item 6, `T-043`, closed 2026-10-02 in PR #48;
Work item 7, `T-044`, closed 2026-10-09; Work item 9, `T-046`, closed 2026-10-14 in PR #51;
Work item 11, `T-061`, closed 2026-10-16 in PR #62, approved, awaiting merge).

Work item 8 (`T-045`, filer CIK and period of report on filing-list rows) is open and active.

Work item 10 (`T-053`–`T-060`, wrong-company tagging, requested by `portfolio-nlp`) is open: `T-057` and
`T-058` are done (the corpus is re-tagged and quarantined, the hand-off is posted, so `portfolio-nlp` is
unblocked and no longer waits on this repo); `T-060` (content-quality gate) is done; `T-059` (close-out) remains.

Work item 12 (`T-062`, matcher follow-up for full-name false rejects) is open but not scheduled: the
maintainer decides when. `portfolio-nlp` does not wait on it.

Work item 1 (CI workflow) was built and then reverted at the maintainer's
request (#35), so T-001–T-007 stay unchecked and T-010–T-014 are moot until
CI is wanted again.
