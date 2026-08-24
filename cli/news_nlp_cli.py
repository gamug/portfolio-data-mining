#!/usr/bin/env python
"""CLI entrypoint: run the sentiment + NER + category (+ optional
summarization) batch pipeline directly, no FastAPI/uvicorn involved (for
that, see apps/news_nlp_api.py instead). Wraps news_nlp.pipeline.run_pipeline()
directly with real --limit/--summarize flags; see docs/modules/news-nlp.md.

Sentiment (FinBERT) -> NER (SEC-BERT) -> Category (zero-shot DeBERTa-v3
against a fixed 10-category taxonomy; see docs/category-taxonomy.md) always
run. Pass --summarize to also run c_summary (one summary per article) ->
sector_summary (one summary per gics_sub_industry per closed calendar week,
reduced from that week's c_summary rows) -- off by default, since
summarization loads its own model on top of the 6GB VRAM budget the other
three stages already use.

(src/news_nlp/pipeline.py also has its own bare `if __name__ == "__main__":`
usable via `python -m news_nlp.pipeline [limit]` — kept for backward
compatibility, but this is the documented entrypoint going forward.)

Usage:
    .venv\\Scripts\\python.exe cli\\news_nlp_cli.py --limit 50
    .venv\\Scripts\\python.exe cli\\news_nlp_cli.py   # process every pending article
    .venv\\Scripts\\python.exe cli\\news_nlp_cli.py --summarize   # also run c_summary/sector_summary
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from news_nlp.pipeline import run_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max rows to process per stage (articles for sentiment/NER/category/c_summary; "
        "sector/week groups for sector_summary). Default: all pending.",
    )
    parser.add_argument(
        "--summarize",
        action="store_true",
        help="Also run the c_summary/sector_summary stages. Off by default -- "
        "these stages load their own summarization model in addition to "
        "the sentiment/NER models.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_pipeline(limit=args.limit, summarize=args.summarize)


if __name__ == "__main__":
    main()
