# PLAN.md — `portfolio-data-mining`

The implementation plan for the live backlog identified in
`.specify/memory/SPEC.md`. Where the constitution is principles and
`SPEC.md` is the requirements/architecture contract, this document is the
"how, and in what order" for the work that contract still leaves open.

**Scope of this plan is deliberately narrow.** `SPEC.md` §13 (Open
Questions & Risks) lists eight items; §14 (Scope Boundaries) explicitly
marks seven of them **accepted** — permanent characteristics of this
project at its current, non-production scope. Only **one** item — §13
item 5, "no CI workflow file exists" — is flagged "should fix regardless of
scope." This plan covers that item only. It is not a product roadmap, and
it does not resurrect anything §14 already closed — see Non-goals below.

## Goal

Close the one backlog item that is genuinely actionable without expanding
this project's scope: turn the manual, convention-based lint/format/type/
test sequence (constitution: Executable cmds #1) into an enforced CI
workflow, and (a maintainer follow-up) make it a required check.

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

## Sequencing

Work item 2 is blocked on Work item 1 (the check must exist and have run
before it can be marked required) — otherwise there is no ordering
constraint from the rest of the backlog, since every other `SPEC.md` §13
item is accepted (Non-goals above) and not touched by this plan.

See `TASKS.md` for the discrete, checkable task breakdown.
