"""One-time setup: pre-download the models the pipeline needs.

Run once, manually, before the first pipeline run (or after cloning this
repo fresh):
    .venv/Scripts/python.exe -m news_nlp.setup

Only fetches files into the local Hugging Face cache -- does not load
anything into memory or touch the GPU. Safe to re-run: huggingface_hub
skips files that are already cached.
"""

from huggingface_hub import snapshot_download
from transformers import AutoConfig

from .pipeline import CATEGORY_MODEL, NER_MODEL, SENTIMENT_MODEL, SUMMARY_MODEL

MODELS = (SENTIMENT_MODEL, NER_MODEL, CATEGORY_MODEL, SUMMARY_MODEL)


def download_models() -> None:
    for repo_id in MODELS:
        print(f"Fetching {repo_id}...")
        local_path = snapshot_download(repo_id)
        AutoConfig.from_pretrained(repo_id)
        print(f"OK: {repo_id} cached at {local_path}")
    print("Setup complete.")


if __name__ == "__main__":
    download_models()
