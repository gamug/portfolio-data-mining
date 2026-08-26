"""One-shot batch pipeline: FinBERT sentiment + fine-tuned NER + zero-shot
category classification over `articles`.

Run manually whenever new articles need processing:
    .venv/Scripts/python.exe -m news_nlp.pipeline

Idempotent/resumable: only processes articles missing from the results
tables. Loads one model onto the GPU at a time (sentiment, then NER, then
category) to stay well within a 6GB VRAM budget, and frees each model before
loading the next. If a stage has nothing pending, it skips loading that
stage's model entirely.
"""

import gc
import sqlite3
import sys
from collections.abc import Callable
from typing import Any

import torch
from tqdm import tqdm
from transformers import (
    AutoModelForSeq2SeqLM,
    AutoModelForSequenceClassification,
    AutoModelForTokenClassification,
    AutoTokenizer,
)

from . import db
from .categories import CATEGORY_LABELS, OTHER_LABEL
from .chunking import chunk_text, merge_char_spans

SENTIMENT_MODEL = "ProsusAI/finbert"
NER_MODEL = "gamug/sec-bert-finer-ord-ner"
CATEGORY_MODEL = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"
SUMMARY_MODEL = "sshleifer/distilbart-cnn-12-6"

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# (stage_name, processed_count, total_count) -> None
ProgressCallback = Callable[[str, int, int], None]


def _warn_if_cpu() -> None:
    """Print a hard-to-miss banner when DEVICE resolved to CPU. Each stage's
    own "=== ... on {DEVICE} ===" banner already says so, but that's easy to
    miss in the moment -- the usual symptom is just "the pipeline seems to be
    hanging", discovered hours into a run, not a line read at the top. Called
    once from run_pipeline() (not at import time) so importing this module
    without running it stays silent.

    A CPU fallback here isn't a driver/GPU problem -- torch.cuda.is_available()
    is False whenever the installed torch build has no CUDA support compiled
    in at all, which is what a plain `pip install torch` (or a transitive
    dependency pulling it in) gives you. The fix is reinstalling torch from
    the CUDA-specific index documented in requirements.txt, not anything
    driver-side.
    """
    if DEVICE.type == "cpu":
        print(
            "\n" + "!" * 78 + "\n! WARNING: CUDA is not available -- this run will use the CPU.\n"
            "! Sentiment/NER/category/summarization models are dramatically slower\n"
            "! on CPU. If this machine has an NVIDIA GPU, torch is very likely\n"
            "! installed as the plain CPU-only wheel instead of a CUDA build --\n"
            "! reinstall it with:\n"
            "!     .venv\\Scripts\\python.exe -m pip install torch "
            "--index-url https://download.pytorch.org/whl/cu124\n" + "!" * 78 + "\n"
        )


# 9 mutually-exclusive labels via softmax over entailment logits gives a
# uniform-chance baseline of ~0.11; requiring the winner to clear 0.4
# (~3.6x baseline) routes genuinely ambiguous/generic articles to "other"
# without being so strict that on-topic articles with modest lexical overlap
# to their hypothesis get miscategorized. Named constant specifically so
# it's cheap to retune later using article_category's stored per-label score
# distribution -- see docs/category-taxonomy.md.
CATEGORY_CONFIDENCE_THRESHOLD = 0.4
# Tighter than the 510 used by sentiment/NER's single-sequence chunking:
# this stage tokenizes (premise, hypothesis) *pairs*, so the premise needs
# to leave headroom for the hypothesis text plus special tokens within the
# model's 512-token cap.
CATEGORY_PREMISE_MAX_TOKENS = 460
# Articles classified per forward pass, not just labels-per-article: each
# article already batches its own 9 (premise, hypothesis) pairs in one call,
# but 9 rows is too small a batch to keep a GPU busy. Grouping
# CATEGORY_BATCH_SIZE articles' pairs into one call (8 * 9 = 72 rows) gets
# real throughput out of the GPU without materially raising peak VRAM --
# still one model on the card at a time, just a wider batch through it.
CATEGORY_BATCH_SIZE = 8

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
# generate() with beam search is far more memory-intensive per row than a
# single classification forward pass (run_category_stage's forward-only
# CATEGORY_BATCH_SIZE=8), so this stays smaller despite the same
# one-model-at-a-time VRAM budget -- tune down further if a 6GB card OOMs.
SUMMARY_BATCH_SIZE = 4


def free_gpu() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def run_sentiment_stage(
    conn: sqlite3.Connection, limit: int | None = None, on_progress: ProgressCallback | None = None
) -> None:
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
            label = max(class_probs, key=class_probs.__getitem__)

            db.write_sentiment(
                conn,
                article_id,
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


def merge_bio_predictions(
    pred_ids: list[int],
    offsets: list[tuple[int, int]],
    probs: list[list[float]],
    id2label: dict[int, str],
) -> list[dict[str, Any]]:
    """Convert token-level BIO predictions (with char offsets local to the
    chunk) into merged entity spans local to the chunk."""
    entities: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for i, (pred_id, (start, end)) in enumerate(zip(pred_ids, offsets, strict=False)):
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
            current = {
                "entity_type": tag_type,
                "start_char": start,
                "end_char": end,
                "scores": [score],
            }
        else:
            current["end_char"] = end
            current["scores"].append(score)

    if current:
        entities.append(current)
    return entities


def run_ner_stage(
    conn: sqlite3.Connection, limit: int | None = None, on_progress: ProgressCallback | None = None
) -> None:
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
                ch.text,
                return_tensors="pt",
                truncation=True,
                max_length=512,
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
                article_entities.append(
                    {
                        "entity_type": e["entity_type"],
                        "text": body_text[start:end],
                        "start_char": start,
                        "end_char": end,
                        "score": sum(e["scores"]) / len(e["scores"]),
                    }
                )

        article_entities = merge_char_spans(article_entities)
        db.write_entities(conn, article_id, article_entities, model_name=NER_MODEL)
        conn.commit()

        if on_progress:
            on_progress("ner", idx, total)

    del model, tokenizer
    free_gpu()


def classify_category_scores(entail_logits: list[float]) -> tuple[str, float, dict]:
    """Turn 9 entailment logits (one per CATEGORY_LABELS slug, same order)
    into (label, winning_score, {slug: prob}). Split out from
    run_category_stage so the classification math is testable without a
    real model, same spirit as merge_bio_predictions being split out of
    run_ner_stage.

    `winning_score` always reflects the best-scoring slug's probability,
    even when the returned label is OTHER_LABEL -- that's what makes
    low-confidence "other" picks auditable (label='other' with a score just
    under the threshold is a near-miss; a low score alongside a flat
    distribution is not).
    """
    probs = torch.softmax(torch.tensor(entail_logits), dim=0).tolist()
    scores = {slug: p for (slug, _, _), p in zip(CATEGORY_LABELS, probs, strict=False)}
    winner_slug = max(scores, key=scores.__getitem__)
    winner_score = scores[winner_slug]
    label = winner_slug if winner_score >= CATEGORY_CONFIDENCE_THRESHOLD else OTHER_LABEL
    return label, winner_score, scores


def run_category_stage(
    conn: sqlite3.Connection, limit: int | None = None, on_progress: ProgressCallback | None = None
) -> None:
    rows = db.fetch_pending_category_articles(conn, limit=limit)
    total = len(rows)
    print(f"\n=== Category stage ({CATEGORY_MODEL}) on {DEVICE} ===")
    print(f"{total} article(s) pending category classification")
    if on_progress:
        on_progress("category", 0, total)
    if total == 0:
        return

    tokenizer = AutoTokenizer.from_pretrained(CATEGORY_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(CATEGORY_MODEL).to(DEVICE).eval()
    entailment_id = next(v for k, v in model.config.label2id.items() if k.lower() == "entailment")

    hypotheses = [f"This example is about {phrase}." for _, _, phrase in CATEGORY_LABELS]
    n_labels = len(hypotheses)

    idx = 0
    with tqdm(total=total, desc="category") as pbar:
        for batch_start in range(0, total, CATEGORY_BATCH_SIZE):
            batch_rows = rows[batch_start : batch_start + CATEGORY_BATCH_SIZE]

            # Title + lead chunk of body, not full-article chunking: each
            # label needs its own (premise, hypothesis) forward pass, so
            # chunking the whole article the way sentiment/NER do would cost
            # 9x per chunk -- unaffordable for a stage that now runs on every
            # article. News is inverted-pyramid, so the opening sentences
            # almost always establish the dominant topic.
            premises = []
            for _article_id, title, body_text in batch_rows:
                chunks = chunk_text(
                    f"{title}. {body_text}", tokenizer, max_tokens=CATEGORY_PREMISE_MAX_TOKENS
                )
                premises.append(chunks[0].text if chunks else title)

            # Flatten to one (premise, hypothesis) pair per label per article
            # in the batch, so one forward pass classifies every article in
            # `batch_rows` at once -- the whole point of batching across
            # articles instead of just across an article's own 9 labels.
            batch_premises = [p for p in premises for _ in range(n_labels)]
            batch_hypotheses = hypotheses * len(premises)

            inputs = tokenizer(
                batch_premises,
                batch_hypotheses,
                return_tensors="pt",
                truncation="only_first",
                padding=True,
                max_length=512,
            ).to(DEVICE)
            with torch.no_grad():
                logits = model(**inputs).logits

            # reshape, not view: the entailment column is a strided slice of
            # `logits`, not contiguous, and view() requires contiguity.
            entail_logits = logits[:, entailment_id].reshape(len(premises), n_labels)

            for (article_id, _, _), article_logits in zip(batch_rows, entail_logits, strict=False):
                label, score, scores = classify_category_scores(article_logits.tolist())
                db.write_category(
                    conn,
                    article_id,
                    label=label,
                    score=score,
                    scores=scores,
                    model_name=CATEGORY_MODEL,
                )

                idx += 1
                pbar.update(1)
                if on_progress:
                    on_progress("category", idx, total)

            conn.commit()

    del model, tokenizer
    free_gpu()


def _summarize_batch(
    texts: list[str], tokenizer: Any, model: Any, device: torch.device
) -> list[str]:
    """Run SUMMARY_MODEL (distilbart-cnn-12-6) generation on a batch of
    chunks that already fit within the model's input cap, in one forward
    pass -- the same batching principle run_category_stage applies by
    pooling multiple articles' (premise, hypothesis) pairs into one call.
    Split out as its own function so tests can monkeypatch it and exercise
    hierarchical_summarize_batch's chunk/reduce control flow without loading
    a real model."""
    inputs = tokenizer(
        texts, return_tensors="pt", truncation=True, max_length=1024, padding=True
    ).to(device)
    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_length=SUMMARY_MAX_OUTPUT_TOKENS,
            min_length=SUMMARY_MIN_OUTPUT_TOKENS,
            num_beams=4,
        )
    return [s.strip() for s in tokenizer.batch_decode(output_ids, skip_special_tokens=True)]


def _summarize_in_batches(
    texts: list[str], tokenizer: Any, model: Any, device: torch.device, batch_size: int
) -> list[str]:
    """Run _summarize_batch over `texts` in chunks of `batch_size`,
    concatenating results in order -- the actual generate() call count stays
    bounded by batch_size regardless of how many texts are pending."""
    results: list[str] = []
    for start in range(0, len(texts), batch_size):
        results.extend(
            _summarize_batch(texts[start : start + batch_size], tokenizer, model, device)
        )
    return results


def _leaf_summarize_batch(
    texts: list[str],
    tokenizer: Any,
    model: Any,
    device: torch.device,
    max_input_tokens: int,
    batch_size: int,
) -> tuple[list[list[str]], list[int]]:
    """Chunk every text on sentence boundaries (chunk_text) and
    batch-summarize the whole pool of leaf chunks together. Returns
    (summaries_per_text, num_chunks), each indexed the same as `texts`."""
    per_text_chunks = [chunk_text(t, tokenizer, max_tokens=max_input_tokens) for t in texts]
    num_chunks = [len(c) for c in per_text_chunks]

    flat_texts: list[str] = []
    owner: list[int] = []
    for i, chunks in enumerate(per_text_chunks):
        for ch in chunks:
            flat_texts.append(ch.text)
            owner.append(i)

    flat_summaries = _summarize_in_batches(flat_texts, tokenizer, model, device, batch_size)

    summaries_per_text: list[list[str]] = [[] for _ in texts]
    for o, s in zip(owner, flat_summaries, strict=True):
        summaries_per_text[o].append(s)

    return summaries_per_text, num_chunks


def _reduce_pass(
    pending: set[int],
    summaries_per_text: list[list[str]],
    tokenizer: Any,
    model: Any,
    device: torch.device,
    max_input_tokens: int,
    batch_size: int,
) -> None:
    """Run one reduce pass in place over every text index in `pending`: join
    each one's current summaries, re-chunk, and batch-summarize the pooled
    result across all of them -- same batching principle as the leaf pass."""
    flat_texts: list[str] = []
    owner: list[int] = []
    for i in sorted(pending):
        combined = " ".join(summaries_per_text[i])
        for ch in chunk_text(combined, tokenizer, max_tokens=max_input_tokens):
            flat_texts.append(ch.text)
            owner.append(i)

    flat_summaries = _summarize_in_batches(flat_texts, tokenizer, model, device, batch_size)

    regrouped: dict[int, list[str]] = {i: [] for i in pending}
    for o, s in zip(owner, flat_summaries, strict=True):
        regrouped[o].append(s)
    for i in pending:
        summaries_per_text[i] = regrouped[i]


def hierarchical_summarize_batch(
    texts: list[str],
    tokenizer: Any,
    model: Any,
    device: torch.device,
    max_input_tokens: int = SUMMARY_MAX_INPUT_TOKENS,
    batch_size: int = SUMMARY_BATCH_SIZE,
) -> list[tuple[str, int]]:
    """Batched chunk-then-reduce summarization: chunks and reduces every
    text in `texts` independently (same per-text contract as a single-text
    version would have -- sentence-boundary chunking via chunk_text, then a
    recursive reduce pass over each text's own joined chunk-summaries until
    they collapse to one), but pools the model calls across every text still
    pending at each pass into batch_size-sized generate() calls instead of
    one call per text -- the same batching principle run_category_stage
    applies to its classification forward pass. Returns (summary_text,
    num_chunks) pairs in the same order as `texts`, where num_chunks is each
    text's own leaf-level chunk count (>1 means that text needed a reduce
    pass).
    """
    n = len(texts)
    if n == 0:
        return []

    summaries_per_text, num_chunks = _leaf_summarize_batch(
        texts, tokenizer, model, device, max_input_tokens, batch_size
    )

    passes = 0
    pending = {i for i in range(n) if len(summaries_per_text[i]) > 1}
    while pending and passes < MAX_REDUCE_PASSES:
        _reduce_pass(
            pending, summaries_per_text, tokenizer, model, device, max_input_tokens, batch_size
        )
        passes += 1
        pending = {i for i in pending if len(summaries_per_text[i]) > 1}

    if pending:
        # MAX_REDUCE_PASSES exhausted without collapsing to one chunk for
        # some texts -- force a final pass; generate()'s own truncation=True
        # keeps this bounded even though it means the tail gets dropped.
        forced_positions = sorted(pending)
        forced_texts = [" ".join(summaries_per_text[i]) for i in forced_positions]
        forced_summaries = _summarize_in_batches(forced_texts, tokenizer, model, device, batch_size)
        for i, s in zip(forced_positions, forced_summaries, strict=True):
            summaries_per_text[i] = [s]

    return [
        (summaries_per_text[i][0] if summaries_per_text[i] else "", num_chunks[i]) for i in range(n)
    ]


def run_company_summary_stage(
    conn: sqlite3.Connection, limit: int | None = None, on_progress: ProgressCallback | None = None
) -> None:
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

    idx = 0
    with tqdm(total=total, desc="company_summary") as pbar:
        for batch_start in range(0, total, SUMMARY_BATCH_SIZE):
            batch_rows = rows[batch_start : batch_start + SUMMARY_BATCH_SIZE]
            texts = [db.build_company_summary_input(row) for row in batch_rows]
            results = hierarchical_summarize_batch(texts, tokenizer, model, DEVICE)

            for row, (summary_text, num_chunks) in zip(batch_rows, results, strict=True):
                if summary_text:
                    db.write_company_summary(
                        conn, row["article_id"], summary_text, num_chunks, SUMMARY_MODEL
                    )
                idx += 1
                pbar.update(1)
                if on_progress:
                    on_progress("company_summary", idx, total)

            conn.commit()

    del model, tokenizer
    free_gpu()


def run_sector_summary_stage(
    conn: sqlite3.Connection, limit: int | None = None, on_progress: ProgressCallback | None = None
) -> None:
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

    idx = 0
    with tqdm(total=total, desc="sector_summary") as pbar:
        for batch_start in range(0, total, SUMMARY_BATCH_SIZE):
            batch_groups = groups[batch_start : batch_start + SUMMARY_BATCH_SIZE]

            # Resolve each group's contributing rows/entity stats first
            # (cheap DB reads) -- a group with nothing to summarize (all its
            # articles excluded, see fetch_company_summaries_for_sector_week)
            # is skipped, same as the old per-group `if rows:` guard.
            resolved: list[tuple[list[sqlite3.Row], list[dict]] | None] = []
            for group in batch_groups:
                group_rows = db.fetch_company_summaries_for_sector_week(
                    conn, group["gics_sector"], group["gics_sub_industry"], group["week_start"]
                )
                if group_rows:
                    entity_stats = db.fetch_sector_week_entity_stats(
                        conn, group["gics_sector"], group["gics_sub_industry"], group["week_start"]
                    )
                    resolved.append((group_rows, entity_stats))
                else:
                    resolved.append(None)

            # One batched model call for every group in this batch that has
            # something to summarize -- mirrors run_category_stage pooling
            # multiple articles' forward passes into one call. The model
            # only ever sees these small, aggregate-stats-only seeds -- never
            # the raw per-company c_summary text -- so it has nothing to
            # blend across companies or categories.
            pending_positions = [i for i, r in enumerate(resolved) if r is not None]
            seeds = [
                db.build_sector_intro_seed(
                    batch_groups[i]["gics_sector"],
                    batch_groups[i]["gics_sub_industry"],
                    batch_groups[i]["week_start"],
                    batch_groups[i]["week_end"],
                    resolved[i][0],  # type: ignore[index]
                )
                for i in pending_positions
            ]
            intro_results = hierarchical_summarize_batch(seeds, tokenizer, model, DEVICE)
            intro_by_pos = dict(
                zip(pending_positions, (text for text, _ in intro_results), strict=True)
            )

            for i, group in enumerate(batch_groups):
                item = resolved[i]
                if item is not None:
                    group_rows, entity_stats = item
                    intro_text = db.clean_generated_text(intro_by_pos[i])
                    summary_text = db.compose_sector_summary(
                        group["gics_sector"],
                        group["gics_sub_industry"],
                        group["week_start"],
                        group["week_end"],
                        intro_text,
                        group_rows,
                        entity_stats,
                    )
                    facts = db.build_sector_facts(
                        group["gics_sector"],
                        group["gics_sub_industry"],
                        group["week_start"],
                        group["week_end"],
                        group_rows,
                        entity_stats,
                    )
                    db.write_sector_summary(
                        conn,
                        group["gics_sector"],
                        group["gics_sub_industry"],
                        group["week_start"],
                        group["week_end"],
                        summary_text,
                        num_articles=len(group_rows),
                        num_companies=len({r["company"] for r in group_rows}),
                        model_name=SUMMARY_MODEL,
                        facts=facts,
                        intro_text=intro_text,
                    )
                idx += 1
                pbar.update(1)
                if on_progress:
                    on_progress("sector_summary", idx, total)

            conn.commit()

    del model, tokenizer
    free_gpu()


def run_pipeline(
    limit: int | None = None,
    summarize: bool = False,
    on_progress: ProgressCallback | None = None,
) -> None:
    _warn_if_cpu()
    conn = db.connect()
    db.init_schema(conn)
    try:
        run_sentiment_stage(conn, limit=limit, on_progress=on_progress)
        run_ner_stage(conn, limit=limit, on_progress=on_progress)
        run_category_stage(conn, limit=limit, on_progress=on_progress)
        if summarize:
            run_company_summary_stage(conn, limit=limit, on_progress=on_progress)
            run_sector_summary_stage(conn, limit=limit, on_progress=on_progress)
    finally:
        conn.close()
    print("\nPipeline run complete.")


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run_pipeline(limit=limit)


if __name__ == "__main__":
    main()
