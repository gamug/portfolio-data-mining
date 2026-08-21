# news_nlp — pipeline stage 3: sentiment + NER + categorization + summarization

**Source:** `news-nlp/` → `src/news_nlp/` + `apps/news_nlp_api.py` + `tests/news_nlp/`

Runs up to five sequential batch stages over the `articles` rows
[extractor](news-crawler.md) wrote into the shared `data/urls.db` (override with
`$DATABASE_URL`, see root `.env.example`), each owning its own results table, and exposes
everything through a FastAPI service. There is no scraper here — `articles` is treated as
pre-populated input.

Sentiment, NER, and category (stages 1–3) always run. `c_summary`/`sector_summary`
(stages 4–5) are **opt-in** — pass `--summarize` on the CLI or `"summarize": true` in the
API's `/pipeline/run` body; the default (`summarize=False`) skips them entirely, so the
summarization model never loads and its VRAM/latency cost is never paid unless asked for.

1. **Sentiment** — FinBERT (`ProsusAI/finbert`) → `article_sentiment`.
2. **NER** — a fine-tuned SEC-BERT-BASE model trained on FiNER-ORD, published at
   [gamug/sec-bert-finer-ord-ner](https://huggingface.co/gamug/sec-bert-finer-ord-ner) →
   `article_entities`.
3. **Category** — zero-shot NLI classification (`MoritzLaurer/deberta-v3-base-zeroshot-v2.0`)
   against a fixed 10-category taxonomy (9 dimensions of company performance + `other`) →
   `article_category`. See [`../category-taxonomy.md`](../category-taxonomy.md) for the
   taxonomy, its sourcing (RavenPack, SASB, IPTC Media Topics, Refinitiv/TRNA), and the
   confidence-threshold mechanism. Classifies on the article's title + lead body chunk
   (not the full article — a per-label NLI pass makes full-article chunking too expensive
   for a mandatory, every-article stage — and not `article_summary`, since that's opt-in
   and this stage isn't).
4. **`c_summary`** — one abstractive summary per article (`sshleifer/distilbart-cnn-12-6`),
   generated from the article's body plus its already-computed sentiment/entities →
   `article_summary`.
5. **`sector_summary`** — one abstractive summary per `gics_sub_industry` per closed
   calendar week (`sshleifer/distilbart-cnn-12-6`), reduced from that week's `article_summary`
   rows across every company in the sub-industry → `sector_summary`. Sub-industries with
   no qualifying articles in a given week get no row; "closed" means the week (Mon–Sun)
   has fully ended, so a summary is never generated from a partial week and later need
   regenerating.

- **Sentence-aware chunking** (`src/news_nlp/chunking.py`) — articles run up to ~13K
  words, far past BERT's 512-token limit; chunks are packed on sentence boundaries. The
  category stage reuses it (with a tighter token budget, to leave headroom for the NLI
  hypothesis text) to take just the lead chunk; the summarization stages reuse it for
  BART's 1024-token cap, plus a **hierarchical reduce**
  (`pipeline.hierarchical_summarize()`): summarize each chunk, then if more than one chunk
  resulted, recursively summarize the concatenated chunk-summaries until they collapse
  into a single pass.
- **Idempotent, resumable batch processing** — each stage only processes rows missing
  from its results table (articles for stages 1–4, `(gics_sector, gics_sub_industry,
  week_start)` groups for stage 5, enforced by a `UNIQUE` constraint on `sector_summary`).
- **6GB-VRAM-friendly** — only one model resident on the GPU at a time; each stage loads
  its model, runs to completion, and frees the GPU before the next stage loads (skipped
  entirely when a stage has nothing pending).
- `c_summary` only covers articles that have both a computed sentiment **and** at least
  one entity scoring above 0.8 confidence (`article_entities.score > 0.8`, excluding
  single-character digit noise) — an article with sentiment but no qualifying entities
  never gets summarized. This mirrors the original `query.sql` design this feature is
  based on.

## Setup (module-specific — torch isn't in the shared `requirements.txt`)

torch isn't listed as a direct dependency (its install command depends on your CUDA
version), but a CPU-only build may already get pulled in transitively by another
package's dependency chain when you `pip install -r requirements.txt` — check
`pip show torch` before assuming you need this step. If you want GPU acceleration, install
the CUDA-matched wheel explicitly (this replaces whatever transitive build is present):

```bash
.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu124

# Pre-fetch all four models into the local Hugging Face cache (one-time, safe to re-run)
.venv\Scripts\python.exe -m src.news_nlp.setup
```

## Running

```bash
# CLI (direct — no server)
.venv\Scripts\python.exe cli\news_nlp_cli.py --limit 50
.venv\Scripts\python.exe cli\news_nlp_cli.py   # process every pending article, sentiment + NER + category only
.venv\Scripts\python.exe cli\news_nlp_cli.py --summarize   # also run c_summary/sector_summary

# API
.venv\Scripts\python.exe apps\news_nlp_api.py
# -> http://127.0.0.1:8003/docs
# POST /pipeline/run  {"limit": 50, "summarize": true}
```

`cli/news_nlp_cli.py` wraps `news_nlp.pipeline.run_pipeline()` directly with real
`--limit`/`--summarize` flags, driving sentiment + NER + category (and, with `--summarize`,
`c_summary` + `sector_summary`) in sequence. `src/news_nlp/pipeline.py` also still has its
own bare `if __name__ == "__main__":` (usable via `python -m news_nlp.pipeline [limit]`, a
positional arg instead of a flag, sentiment + NER + category only — no way to opt into
summarization from that entrypoint) — kept for backward compatibility, but
`cli/news_nlp_cli.py` is the documented entrypoint going forward.

Query results via the API: `GET /articles/{id}` now includes `"category"` and `"summary"`
keys (same shape as `"sentiment"`/`"entities"` — `"category"` is `None` until stage 3 has
processed that article, `"summary"` until stage 4 has), `GET /stats/categories` (optional
`company`/`date_from`/`date_to` filters) returns per-label article counts, and
`GET /sectors/summary` (optional `sector`/`sub_industry`/`week_start` filters) lists
`sector_summary` rows, newest week first.

## Testing

```bash
.venv\Scripts\python.exe -m pytest tests/news_nlp -q
```

Requires torch installed (see Setup above). 98/100 pass once it's present (one previously
stale test, `test_main.py`, was removed — it exercised the old `news-nlp/main.py`
uvicorn-launcher module, whose one line folded into `apps/news_nlp_api.py`'s
`if __name__ == "__main__":` block during migration, so there's no separate `main` module
left to import). Two pre-existing failures, neither a regression:
- `test_db_module.py::test_db_path_points_to_project_root_data_dir` — a pre-existing
  `.env`/`DATABASE_URL` interaction; it also needed its own `parents[N]` index bumped by
  one, for the same reason `db.DB_PATH` did, see the file-mapping note in the root README
  about the `src/news_nlp/` nesting being one level deeper than the original
  `news-nlp/src/`.
- `test_summary_db.py::test_fetch_pending_sector_weeks_excludes_open_week` — a time-bomb
  test: it hardcodes a date meant to fall inside "the still-open current week" at the time
  it was written, which eventually becomes a closed week as real time passes (as it now
  has). Needs a relative-to-`date('now')` fixture date rather than a hardcoded one to be
  durably correct; out of scope for the category-stage work that surfaced it.
