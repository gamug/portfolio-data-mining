"""One-shot batch pipeline: FinBERT sentiment + fine-tuned NER over `articles`.

Run manually whenever new articles need processing:
    .venv/Scripts/python.exe -m news_nlp.pipeline

Idempotent/resumable: only processes articles missing from the results
tables. Loads one model onto the GPU at a time (sentiment, then NER) to stay
well within a 6GB VRAM budget, and frees each model before loading the next.
If a stage has nothing pending, it skips loading that stage's model entirely.
"""
import gc
import sys

import torch
from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    AutoModelForTokenClassification,
)
from tqdm import tqdm

from . import db
from .chunking import chunk_text, merge_char_spans

SENTIMENT_MODEL = "ProsusAI/finbert"
NER_MODEL = "gamug/sec-bert-finer-ord-ner"

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def free_gpu():
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def run_sentiment_stage(conn, limit=None, on_progress=None):
    rows = db.fetch_pending_articles(conn, "article_sentiment", limit=limit)
    total = len(rows)
    print(f"\n=== Sentiment stage ({SENTIMENT_MODEL}) on {DEVICE} ===")
    print(f"{total} article(s) pending sentiment analysis")
    if on_progress:
        on_progress("sentiment", 0, total)
    if total == 0:
        return

    tokenizer = AutoTokenizer.from_pretrained(SENTIMENT_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(SENTIMENT_MODEL).to(DEVICE).eval()
    id2label = {int(k): v.lower() for k, v in model.config.id2label.items()}

    for idx, (article_id, body_text) in enumerate(tqdm(rows, desc="sentiment"), start=1):
        chunks = chunk_text(body_text, tokenizer, max_tokens=510)
        if chunks:
            probs_sum = torch.zeros(len(id2label))
            total_weight = 0
            for ch in chunks:
                inputs = tokenizer(
                    ch.text, return_tensors="pt", truncation=True, max_length=512
                ).to(DEVICE)
                n_tokens = inputs["input_ids"].shape[1]
                with torch.no_grad():
                    logits = model(**inputs).logits[0]
                    probs = torch.softmax(logits, dim=-1).cpu()
                probs_sum += probs * n_tokens
                total_weight += n_tokens

            avg_probs = (probs_sum / total_weight).tolist()
            class_probs = {id2label[i]: p for i, p in enumerate(avg_probs)}
            label = max(class_probs, key=class_probs.get)

            db.write_sentiment(
                conn, article_id,
                label=label,
                score=class_probs[label],
                positive=class_probs.get("positive", 0.0),
                negative=class_probs.get("negative", 0.0),
                neutral=class_probs.get("neutral", 0.0),
                model_name=SENTIMENT_MODEL,
            )
            conn.commit()

        if on_progress:
            on_progress("sentiment", idx, total)

    del model, tokenizer
    free_gpu()


def merge_bio_predictions(pred_ids, offsets, probs, id2label):
    """Convert token-level BIO predictions (with char offsets local to the
    chunk) into merged entity spans local to the chunk."""
    entities = []
    current = None
    for i, (pred_id, (start, end)) in enumerate(zip(pred_ids, offsets)):
        if start == end:  # special/padding token
            continue
        label = id2label[pred_id]
        score = probs[i][pred_id]

        if label == "O":
            if current:
                entities.append(current)
                current = None
            continue

        bio, tag_type = label.split("-", 1)
        if bio == "B" or current is None or current["entity_type"] != tag_type:
            if current:
                entities.append(current)
            current = {"entity_type": tag_type, "start_char": start, "end_char": end, "scores": [score]}
        else:
            current["end_char"] = end
            current["scores"].append(score)

    if current:
        entities.append(current)
    return entities


def run_ner_stage(conn, limit=None, on_progress=None):
    rows = db.fetch_pending_articles(conn, "article_entities", limit=limit)
    total = len(rows)
    print(f"\n=== NER stage ({NER_MODEL}) on {DEVICE} ===")
    print(f"{total} article(s) pending NER")
    if on_progress:
        on_progress("ner", 0, total)
    if total == 0:
        return

    tokenizer = AutoTokenizer.from_pretrained(NER_MODEL)
    model = AutoModelForTokenClassification.from_pretrained(NER_MODEL).to(DEVICE).eval()
    id2label = {int(k): v for k, v in model.config.id2label.items()}

    for idx, (article_id, body_text) in enumerate(tqdm(rows, desc="ner"), start=1):
        chunks = chunk_text(body_text, tokenizer, max_tokens=510)
        article_entities = []

        for ch in chunks:
            inputs = tokenizer(
                ch.text, return_tensors="pt", truncation=True, max_length=512,
                return_offsets_mapping=True,
            )
            offsets = inputs.pop("offset_mapping")[0].tolist()
            inputs = {k: v.to(DEVICE) for k, v in inputs.items()}
            with torch.no_grad():
                logits = model(**inputs).logits[0]
                probs = torch.softmax(logits, dim=-1).cpu()
                pred_ids = probs.argmax(-1).tolist()

            chunk_entities = merge_bio_predictions(pred_ids, offsets, probs.tolist(), id2label)
            for e in chunk_entities:
                start = ch.start_char + e["start_char"]
                end = ch.start_char + e["end_char"]
                article_entities.append({
                    "entity_type": e["entity_type"],
                    "text": body_text[start:end],
                    "start_char": start,
                    "end_char": end,
                    "score": sum(e["scores"]) / len(e["scores"]),
                })

        article_entities = merge_char_spans(article_entities)
        db.write_entities(conn, article_id, article_entities, model_name=NER_MODEL)
        conn.commit()

        if on_progress:
            on_progress("ner", idx, total)

    del model, tokenizer
    free_gpu()


def run_pipeline(limit=None, on_progress=None):
    conn = db.connect()
    db.init_schema(conn)
    try:
        run_sentiment_stage(conn, limit=limit, on_progress=on_progress)
        run_ner_stage(conn, limit=limit, on_progress=on_progress)
    finally:
        conn.close()
    print("\nPipeline run complete.")


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run_pipeline(limit=limit)


if __name__ == "__main__":
    main()
