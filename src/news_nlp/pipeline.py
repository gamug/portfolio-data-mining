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
    AutoModelForSeq2SeqLM,
)
from tqdm import tqdm

from . import db
from .chunking import chunk_text, merge_char_spans

SENTIMENT_MODEL = "ProsusAI/finbert"
NER_MODEL = "gamug/sec-bert-finer-ord-ner"
SUMMARY_MODEL = "facebook/bart-large-cnn"

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# BART-large-cnn's own cap is 1024 tokens; 1000 leaves headroom for the
# BOS/EOS tokens the tokenizer adds on top of chunk_text's count.
SUMMARY_MAX_INPUT_TOKENS = 1000
# Matches bart-large-cnn's published default generation config.
SUMMARY_MAX_OUTPUT_TOKENS = 142
SUMMARY_MIN_OUTPUT_TOKENS = 56
# Safety valve for the recursive reduce below -- each pass's summaries are
# far shorter than what fed them, so this converges in 1-2 passes in
# practice; this just bounds the pathological case.
MAX_REDUCE_PASSES = 6


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


def _summarize_one(text: str, tokenizer, model, device) -> str:
    """Run bart-large-cnn generation on a single chunk that already fits
    within the model's input cap. Split out as its own function so tests can
    monkeypatch it and exercise hierarchical_summarize's chunk/reduce control
    flow without loading a real model."""
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=1024).to(device)
    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_length=SUMMARY_MAX_OUTPUT_TOKENS,
            min_length=SUMMARY_MIN_OUTPUT_TOKENS,
            num_beams=4,
        )
    return tokenizer.decode(output_ids[0], skip_special_tokens=True).strip()


def hierarchical_summarize(text, tokenizer, model, device, max_input_tokens=SUMMARY_MAX_INPUT_TOKENS):
    """Chunk `text` on sentence boundaries (chunk_text), summarize each
    chunk, and if more than one chunk resulted, recursively summarize the
    concatenated chunk-summaries until they collapse into a single pass.
    Returns (summary_text, num_chunks) where num_chunks is the leaf-level
    chunk count (>1 means a reduce pass happened).
    """
    chunks = chunk_text(text, tokenizer, max_tokens=max_input_tokens)
    if not chunks:
        return "", 0

    num_chunks = len(chunks)
    summaries = [_summarize_one(ch.text, tokenizer, model, device) for ch in chunks]

    passes = 0
    while len(summaries) > 1 and passes < MAX_REDUCE_PASSES:
        combined = " ".join(summaries)
        next_chunks = chunk_text(combined, tokenizer, max_tokens=max_input_tokens)
        summaries = [_summarize_one(ch.text, tokenizer, model, device) for ch in next_chunks]
        passes += 1

    if len(summaries) > 1:
        # MAX_REDUCE_PASSES exhausted without collapsing to one chunk --
        # force a final pass; generate()'s own truncation=True keeps this
        # bounded even though it means the tail gets dropped.
        return _summarize_one(" ".join(summaries), tokenizer, model, device), num_chunks

    return summaries[0], num_chunks


def run_company_summary_stage(conn, limit=None, on_progress=None):
    rows = db.fetch_pending_company_summaries(conn, limit=limit)
    total = len(rows)
    print(f"\n=== Company summary stage ({SUMMARY_MODEL}) on {DEVICE} ===")
    print(f"{total} article(s) pending c_summary")
    if on_progress:
        on_progress("company_summary", 0, total)
    if total == 0:
        return

    tokenizer = AutoTokenizer.from_pretrained(SUMMARY_MODEL)
    model = AutoModelForSeq2SeqLM.from_pretrained(SUMMARY_MODEL).to(DEVICE).eval()

    for idx, row in enumerate(tqdm(rows, desc="company_summary"), start=1):
        text = db.build_company_summary_input(row)
        summary_text, num_chunks = hierarchical_summarize(text, tokenizer, model, DEVICE)
        if summary_text:
            db.write_company_summary(conn, row["article_id"], summary_text, num_chunks, SUMMARY_MODEL)
            conn.commit()

        if on_progress:
            on_progress("company_summary", idx, total)

    del model, tokenizer
    free_gpu()


def run_sector_summary_stage(conn, limit=None, on_progress=None):
    groups = db.fetch_pending_sector_weeks(conn, limit=limit)
    total = len(groups)
    print(f"\n=== Sector summary stage ({SUMMARY_MODEL}) on {DEVICE} ===")
    print(f"{total} sector/week group(s) pending sector_summary")
    if on_progress:
        on_progress("sector_summary", 0, total)
    if total == 0:
        return

    tokenizer = AutoTokenizer.from_pretrained(SUMMARY_MODEL)
    model = AutoModelForSeq2SeqLM.from_pretrained(SUMMARY_MODEL).to(DEVICE).eval()

    for idx, group in enumerate(tqdm(groups, desc="sector_summary"), start=1):
        company_rows = db.fetch_company_summaries_for_sector_week(
            conn, group["gics_sector"], group["gics_sub_industry"], group["week_start"]
        )
        if company_rows:
            text = db.build_sector_summary_input(group["gics_sector"], group["gics_sub_industry"], company_rows)
            summary_text, _ = hierarchical_summarize(text, tokenizer, model, DEVICE)
            if summary_text:
                db.write_sector_summary(
                    conn, group["gics_sector"], group["gics_sub_industry"],
                    group["week_start"], group["week_end"], summary_text,
                    num_articles=len(company_rows),
                    num_companies=len({r["company"] for r in company_rows}),
                    model_name=SUMMARY_MODEL,
                )
                conn.commit()

        if on_progress:
            on_progress("sector_summary", idx, total)

    del model, tokenizer
    free_gpu()


def run_pipeline(limit=None, on_progress=None):
    conn = db.connect()
    db.init_schema(conn)
    try:
        run_sentiment_stage(conn, limit=limit, on_progress=on_progress)
        run_ner_stage(conn, limit=limit, on_progress=on_progress)
        run_company_summary_stage(conn, limit=limit, on_progress=on_progress)
        run_sector_summary_stage(conn, limit=limit, on_progress=on_progress)
    finally:
        conn.close()
    print("\nPipeline run complete.")


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run_pipeline(limit=limit)


if __name__ == "__main__":
    main()
