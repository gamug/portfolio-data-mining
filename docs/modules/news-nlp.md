# news_nlp — pipeline stage 3: sentiment + NER

**Source:** `news-nlp/` → `src/news_nlp/` + `apps/news_nlp_api.py` + `tests/news_nlp/`

Runs FinBERT sentiment analysis and a fine-tuned NER model (SEC-BERT-BASE trained on
FiNER-ORD, published at
[gamug/sec-bert-finer-ord-ner](https://huggingface.co/gamug/sec-bert-finer-ord-ner)) over
the `articles` rows [extractor](news-crawler.md) wrote into the shared `data/urls.db`,
writing results into two new tables it owns (`article_sentiment`, `article_entities`) and
exposing everything through a FastAPI service. There is no scraper here — `articles` is
treated as pre-populated input.

- **Sentence-aware chunking** (`src/news_nlp/chunking.py`) — articles run up to ~13K
  words, far past BERT's 512-token limit; chunks are packed on sentence boundaries.
- **Idempotent, resumable batch processing** — each stage only processes articles missing
  from its results table.
- **6GB-VRAM-friendly** — only one model resident on the GPU at a time (sentiment runs to
  completion, frees the GPU, then NER loads).

## Setup (module-specific — torch isn't in the shared `requirements.txt`)

```bash
# torch install depends on your CUDA version — install it separately
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

Requires torch installed (see Setup above) — not run as part of this integration's
verification pass for that reason. `src/news_nlp/` and `apps/news_nlp_api.py` are
confirmed syntactically valid (`py_compile`) and their non-torch import paths (`db`,
`corrections`) resolve; `pipeline.py`'s `import torch` / `transformers` imports are
exercised only once torch is installed per the step above.
