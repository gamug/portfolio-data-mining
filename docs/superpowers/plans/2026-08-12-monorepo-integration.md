# Monorepo Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Executed inline in the authoring session instead — this is a file-reorganization/migration task (copy + mechanical import fixes), not new feature work, and the authoring session already holds the full source-tree survey needed to do it correctly.

**Goal:** Consolidate four sibling projects (news-collector, news-crawler, news-nlp, finhub) into `portfolio-data-mining` as a single git-tracked monorepo: one `.env`, one venv, one root `README.md`, one `src/` with clearly named module folders, and finhub split into `pricing` and `edgar`.

**Architecture:** Each original project becomes one subpackage under `src/`, keeping each project's *internal* import style unchanged wherever possible (news_collector, extractor) to minimize risk, and doing small, bounded import rewrites only where a project's package was itself named `src` (news-nlp, finhub) and would otherwise collide with the shared root `src/`. Every module keeps its own FastAPI entrypoint under `apps/`, so the five services stay independently runnable, sharing one `.venv`, one `requirements.txt`, one `.env`, and (for the three news-pipeline stages) one SQLite file at `data/urls.db`.

**Tech Stack:** Python 3.11, FastAPI/uvicorn, pytest, httpx, finnhub-python, edgartools, transformers/torch (news-nlp only, installed separately per existing convention).

**Spec:** User request (this conversation) — see "Global Constraints" below for the exact deliverables asked for.

## Global Constraints

- Single `.env` (+ `.env.example`) at repo root — merges vars from news-collector/.env.example and finhub/.env.example.
- Single Python venv folder (`.venv`) at repo root.
- Single root `README.md` describing the integration (per-module detail moves to `docs/modules/*.md`).
- Single `src/` folder, one clearly-named subfolder per source project.
- finhub split into two modules: `src/pricing` (Finnhub pricing/news/market-data) and `src/edgar` (SEC EDGAR).
- Git-track the change properly: work on a branch, commit with a clear message, in the existing `portfolio-data-mining` repo (do not touch the 4 source folders — user chose "leave them untouched").
- Bring test suites along (user chose "bring tests along"), fixing import paths to match the new layout.
- Exclude runtime/generated artifacts from git: `.venv`, `data/urls.db`, `__pycache__`, `.pytest_cache`, `.hypothesis`, `models/` (news-nlp — fetched from HF Hub at setup time, not vendored), logs, `output/`, `input/`.

---

## File mapping

| Source | Destination | Import changes |
|---|---|---|
| `news-collector/news_collector/**` | `src/news_collector/**` | none (package already named `news_collector`) |
| `news-collector/app.py` | `apps/news_collector_api.py` | add `src/` to `sys.path` bootstrap; imports unchanged |
| `news-collector/tests/**` | `tests/news_collector/**` | none |
| `news-collector/data/sp500_sample.csv` | `data/sp500_sample.csv` | — |
| `news-crawler/src/extractor/**` | `src/extractor/**` | none (package already named `extractor`) |
| `news-crawler/app.py`, `run_extraction.py` | `apps/news_crawler_api.py`, `apps/news_crawler_extract.py` | add `src/` bootstrap; imports unchanged |
| `news-crawler/tests/**` | `tests/extractor/**` | none |
| `news-nlp/src/**` (pkg literally named `src`) | `src/news_nlp/**` | none — internal imports are relative (`from . import db`) |
| `news-nlp/app.py` | `apps/news_nlp_api.py` | `from src import db, pipeline, corrections` → `from news_nlp import db, pipeline, corrections`; add bootstrap |
| `news-nlp/main.py` | folded into `apps/news_nlp_api.py`'s `__main__` block | — |
| `news-nlp/tests/**` | `tests/news_nlp/**` | `from src.` / `import src` → `from news_nlp.` / `import news_nlp` |
| `finhub/src/config/__init__.py`, `src/commons/{utils,errors,portfolio}.py` | `src/common/{config,utils,errors,portfolio}.py` | `from src.config import` → `from common.config import`; `import src.config as config` → `from common import config`; `from src.commons... ` → `from common...` |
| `finhub/src/trading/pricing.py` | `src/pricing/fetcher.py` | none needed (no `src.` imports) |
| `finhub/src/market/profile.py` | `src/pricing/market_data.py` | none needed |
| `finhub/src/news/finnhub_collector.py` | `src/pricing/news.py` | none needed |
| `finhub/src/fundamental/edgar_tool.py` | `src/edgar/agent.py` | none needed |
| `finhub/edgar_examples.py`, `examples/edgar_examples.txt` | `src/edgar/examples.py`, `docs/modules/edgar_examples.txt` | `from src.fundamental.edgar_tool import` → `from edgar.agent import` |
| `finhub/app.py` (split by tag) | `apps/pricing_api.py` (Universe, Pricing, News, Market Data) + `apps/edgar_api.py` (Edgar) | rewritten imports to `common.*` / `pricing.*` / `edgar.*`; add bootstrap |
| `finhub/s&p500/s&p500.csv` | `s&p500/s&p500.csv` (unchanged path — `common/config.py`'s `BASE_DIR` math depends on this) | — |
| `finhub/requirements.txt`, `.env.example`, `README.md` | merged into root `requirements.txt`, `.env.example`, `README.md` + `docs/modules/pricing.md`, `docs/modules/edgar.md` | — |

`apps/*.py` all get a 3-line `sys.path` bootstrap so `python apps/x.py` and `uvicorn apps.x:app` both resolve `src/`-rooted imports without requiring the user to set `PYTHONPATH` by hand.

## Tasks

- [ ] **Task 1: Branch + skeleton** — create `feature/monorepo-integration`, create `src/`, `apps/`, `tests/`, `docs/modules/` directories.
- [ ] **Task 2: Migrate news_collector** — copy package + tests + sample csv, write `apps/news_collector_api.py`.
- [ ] **Task 3: Migrate extractor (news-crawler)** — copy package + tests, write `apps/news_crawler_api.py` + `apps/news_crawler_extract.py`.
- [ ] **Task 4: Migrate news_nlp** — copy package + tests, fix `src.` → `news_nlp.` imports, write `apps/news_nlp_api.py`.
- [ ] **Task 5: Split finhub into common/pricing/edgar** — copy+rewrite the 4 common modules, 3 pricing modules, 2 edgar modules, write `apps/pricing_api.py` + `apps/edgar_api.py`, carry `s&p500/s&p500.csv`.
- [ ] **Task 6: Root config** — single `.env.example`, `requirements.txt` (merged/deduped), `pytest.ini` (`pythonpath = src`), `.gitignore`.
- [ ] **Task 7: Root README.md + docs/modules/*.md** — integration overview, mermaid pipeline diagram, per-module quick reference and links.
- [ ] **Task 8: Verify** — `py_compile` every new/changed file, run the lightweight test suites (news_collector, extractor) with the new venv, smoke-import news_nlp/pricing/edgar modules.
- [ ] **Task 9: Commit** — `git add -A`, commit on the feature branch with a message describing the consolidation (including the old monolith paths being superseded, per finhub's own README noting GDELT/gnews/scraping were intentionally dropped).

## Self-Review

- Spec coverage: single `.env` ✓ (Task 6), single venv ✓ (Task 1/instructions in README), single README ✓ (Task 7), single `src/` with clear per-source references ✓ (Tasks 2–5), finhub split into pricing/edgar ✓ (Task 5), git tracking ✓ (Task 9).
- No placeholders: every import rewrite above is a literal find/replace, not "fix imports as needed".
- Type/signature consistency: `common.config.general`, `common.utils.init_repository`, `common.errors.UpstreamDataError`, `common.portfolio.{list_universe,resolve_symbol}`, `pricing.fetcher.StockPriceFetcher`, `pricing.market_data.MarketDataClient`, `pricing.news.FinnhubNewsFetcher`, `edgar.agent.EdgarAgent` — used consistently across Task 5 and the two new `apps/*_api.py` entrypoints.
