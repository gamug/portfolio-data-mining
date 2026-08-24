"""One-time fine-tuning: SEC-BERT-BASE -> token classification on FiNER-ORD.

FiNER-ORD (gtfintechlab/finer-ord) ships as one row per token, grouped by
(doc_idx, sent_idx). This script regroups rows into sentences, aligns labels
to WordPiece subwords, fine-tunes nlpaueb/sec-bert-base with a token
classification head, and saves the checkpoint to models/sec-bert-base-finer-ord/.

Run once, offline, before the batch pipeline. Not part of run_pipeline.py.
"""

from collections.abc import Callable
from typing import Any

import evaluate
import numpy as np
from datasets import Dataset, load_dataset
from transformers import (
    AutoModelForTokenClassification,
    AutoTokenizer,
    DataCollatorForTokenClassification,
    Trainer,
    TrainingArguments,
)

MODEL_NAME = "nlpaueb/sec-bert-base"
OUTPUT_DIR = "models/sec-bert-base-finer-ord"

# FiNER-ORD's gold_label ints, per its dataset card:
# {'O': 0, 'PER_B': 1, 'PER_I': 2, 'LOC_B': 3, 'LOC_I': 4, 'ORG_B': 5, 'ORG_I': 6}
# Renamed here to standard B-/I- prefix form for seqeval compatibility; the
# integer ids and their meaning are unchanged.
LABEL_LIST = ["O", "B-PER", "I-PER", "B-LOC", "I-LOC", "B-ORG", "I-ORG"]
ID2LABEL = dict(enumerate(LABEL_LIST))
LABEL2ID = {v: k for k, v in ID2LABEL.items()}

# HuggingFace's standard "ignore this token" sentinel for token classification
# labels (subword continuations, padding) -- excluded from loss and metrics.
IGNORED_LABEL_ID = -100


def group_into_sentences(hf_split: Any) -> list[dict[str, Any]]:
    """Regroup token-level rows into per-sentence {tokens, ner_tags} examples,
    preserving original row order within each (doc_idx, sent_idx) group."""
    df = hf_split.to_pandas()
    # A handful of rows have a null gold_token in the source dataset (e.g. one
    # in train, label 'O'); dropping them is safe and keeps token/label lists aligned.
    df = df.dropna(subset=["gold_token"])
    sentences = []
    for _, g in df.groupby(["doc_idx", "sent_idx"], sort=False):
        sentences.append(
            {
                "tokens": g["gold_token"].tolist(),
                "ner_tags": g["gold_label"].tolist(),
            }
        )
    return sentences


def build_dataset() -> dict[str, Any]:
    raw = load_dataset("gtfintechlab/finer-ord")
    return {
        split: Dataset.from_list(group_into_sentences(raw[split]))
        for split in ("train", "validation", "test")
    }


def make_tokenize_fn(tokenizer: Any) -> Callable[[dict[str, Any]], Any]:
    def tokenize_and_align_labels(batch: dict[str, Any]) -> Any:
        tokenized = tokenizer(
            batch["tokens"], is_split_into_words=True, truncation=True, max_length=512
        )
        all_labels = []
        for i, labels in enumerate(batch["ner_tags"]):
            word_ids = tokenized.word_ids(batch_index=i)
            prev_word_id = None
            label_ids = []
            for word_id in word_ids:
                if word_id is None:
                    label_ids.append(IGNORED_LABEL_ID)  # special tokens ([CLS]/[SEP]/pad)
                elif word_id != prev_word_id:
                    label_ids.append(labels[word_id])  # first subword of a token
                else:
                    label_ids.append(IGNORED_LABEL_ID)  # subsequent subwords: ignored in loss
                prev_word_id = word_id
            all_labels.append(label_ids)
        tokenized["labels"] = all_labels
        return tokenized

    return tokenize_and_align_labels


def make_compute_metrics() -> Callable[[Any], dict[str, float]]:
    seqeval = evaluate.load("seqeval")

    def compute_metrics(eval_pred: Any) -> dict[str, float]:
        predictions, labels = eval_pred
        predictions = np.argmax(predictions, axis=2)

        true_predictions = [
            [ID2LABEL[p] for p, lbl in zip(pred, label, strict=False) if lbl != IGNORED_LABEL_ID]
            for pred, label in zip(predictions, labels, strict=False)
        ]
        true_labels = [
            [ID2LABEL[lbl] for p, lbl in zip(pred, label, strict=False) if lbl != IGNORED_LABEL_ID]
            for pred, label in zip(predictions, labels, strict=False)
        ]
        results = seqeval.compute(predictions=true_predictions, references=true_labels)
        return {
            "precision": results["overall_precision"],
            "recall": results["overall_recall"],
            "f1": results["overall_f1"],
            "accuracy": results["overall_accuracy"],
        }

    return compute_metrics


def main() -> None:
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForTokenClassification.from_pretrained(
        MODEL_NAME, num_labels=len(LABEL_LIST), id2label=ID2LABEL, label2id=LABEL2ID
    )

    ds = build_dataset()
    tokenize_fn = make_tokenize_fn(tokenizer)
    tokenized_ds = {
        split: ds[split].map(tokenize_fn, batched=True, remove_columns=ds[split].column_names)
        for split in ds
    }

    data_collator = DataCollatorForTokenClassification(tokenizer=tokenizer)

    args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        eval_strategy="epoch",
        save_strategy="epoch",
        learning_rate=3e-5,
        per_device_train_batch_size=16,
        per_device_eval_batch_size=32,
        num_train_epochs=8,
        weight_decay=0.01,
        fp16=True,
        load_best_model_at_end=True,
        metric_for_best_model="f1",
        save_total_limit=2,
        logging_steps=50,
        report_to=[],
        seed=42,
    )

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=tokenized_ds["train"],
        eval_dataset=tokenized_ds["validation"],
        data_collator=data_collator,
        processing_class=tokenizer,
        compute_metrics=make_compute_metrics(),
    )

    trainer.train()

    print("\n=== Test set evaluation ===")
    test_metrics = trainer.evaluate(tokenized_ds["test"])
    print(test_metrics)

    trainer.save_model(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)
    print(f"\nSaved fine-tuned model to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
