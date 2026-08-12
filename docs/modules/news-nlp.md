# news_nlp — pipeline stage 3: sentiment + NER

**Source:** `news-nlp/` → `src/news_nlp/` + `apps/news_nlp_api.py` + `tests/news_nlp/`

Runs FinBERT sentiment analysis and a fine-tuned NER model (SEC-BERT-BASE trained on
FiNER-ORD, published at
[gamug/sec-bert-finer-ord-ner](https://huggingface.co/gamug/sec-bert-finer-ord-ner)) over
the `articles` rows [extractor](news-crawler.md) wrote into the shared `data/urls.db`
(override with `$DATABASE_URL`, see root `.env.example`), writing results into two new
tables it owns (`article_sentiment`, `article_entities`) and
exposing everything through a FastAPI service. There is no scraper here — `articles` is
treated as pre-populated input.

- **Sentence-aware chunking** (`src/news_nlp/chunking.py`) — articles run up to ~13K
  words, far past BERT's 512-token limit; chunks are packed on sentence boundaries.
- **Idempotent, resumable batch processing** — each stage only processes articles missing
  from its results table.
- **6GB-VRAM-friendly** — only one model resident on the GPU at a time (sentiment runs to
  completion, frees the GPU, then NER loads).

## Setup (module-specific — torch isn't in the shared `requirements.txt`)

torch isn't listed as a direct dependency (its install command depends on your CUDA
version), but a CPU-only build may already get pulled in transitively by another
package's dependency chain when you `pip install -r requirements.txt` — check
`pip show torch` before assuming you need this step. If you want GPU acceleration, install
the CUDA-matched wheel explicitly (this replaces whatever transitive build is present):

```bash
.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu124

# Pre-fetch both models into the local Hugging Face cache (one-time, safe to re-run)
.venv\Scripts\python.exe -m news_nlp.setup
```

## Running

```bash
.venv\Scripts\python.exe apps\news_nlp_api.py
# -> http://127.0.0.1:8003/docs
```

## Testing

```bash
.venv\Scripts\python.exe -m pytest tests/news_nlp -q
```

Requires torch installed (see Setup above). 43/43 pass once it's present (one previously
stale test, `test_main.py`, was removed — it exercised the old `news-nlp/main.py`
uvicorn-launcher module, whose one line folded into `apps/news_nlp_api.py`'s
`if __name__ == "__main__":` block during migration, so there's no separate `main` module
left to import). `test_db_module.py::test_db_path_points_to_project_root_data_dir` also
needed its own `parents[N]` index bumped by one, for the same reason `db.DB_PATH` did —
see the file-mapping note in the root README about the `src/news_nlp/` nesting being one
level deeper than the original `news-nlp/src/`.
