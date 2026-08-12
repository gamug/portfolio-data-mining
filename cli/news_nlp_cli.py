#!/usr/bin/env python
"""CLI entrypoint: run the sentiment + NER batch pipeline directly —
pipeline stage 3, no FastAPI/uvicorn involved (for that, see
apps/news_nlp_api.py instead). Wraps news_nlp.pipeline.run_pipeline()
directly with a real --limit flag; see docs/modules/news-nlp.md.

(src/news_nlp/pipeline.py also has its own bare `if __name__ == "__main__":`
usable via `python -m news_nlp.pipeline [limit]` — kept for backward
compatibility, but this is the documented entrypoint going forward.)

Usage:
    .venv\\Scripts\\python.exe cli\\news_nlp_cli.py --limit 50
    .venv\\Scripts\\python.exe cli\\news_nlp_cli.py   # process every pending article
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
        help="Max articles to process per stage (sentiment, then NER). Default: all pending.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_pipeline(limit=args.limit)


if __name__ == "__main__":
    main()
