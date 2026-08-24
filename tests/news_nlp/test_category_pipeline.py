import sqlite3
from typing import Any

import pytest
import torch
from conftest import seed_article

from news_nlp import db, pipeline

# --- classify_category_scores (pure function, no model needed) ------------


def test_classify_category_scores_picks_highest_entailment_and_returns_full_distribution() -> None:
    entail_logits = [0.0] * 9
    entail_logits[1] = 5.0  # index 1 == mergers_acquisitions, see pipeline.CATEGORY_LABELS

    label, score, scores = pipeline.classify_category_scores(entail_logits)

    assert label == "mergers_acquisitions"
    assert score > pipeline.CATEGORY_CONFIDENCE_THRESHOLD
    assert set(scores) == {slug for slug, _, _ in pipeline.CATEGORY_LABELS}
    assert abs(sum(scores.values()) - 1.0) < 1e-6


def test_classify_category_scores_falls_back_to_other_below_threshold() -> None:
    entail_logits = [0.0] * 9  # uniform distribution -> ~0.111 each, below the 0.4 threshold

    label, score, _scores = pipeline.classify_category_scores(entail_logits)

    assert label == "other"
    assert score < pipeline.CATEGORY_CONFIDENCE_THRESHOLD
    # `score` still reflects the (sub-threshold) winning slug's own probability,
    # not zero -- that's what makes a near-miss "other" distinguishable from a
    # genuinely flat one when auditing later.
    assert score > 0


# --- run_category_stage -----------------------------------------------------


class WordCountTokenizer:
    """Minimal stand-in for a real tokenizer: chunk_text only needs
    .encode(text, add_special_tokens=False) to count tokens, and the
    __call__ signature run_category_stage uses for batched NLI pairs."""

    def encode(self, text: str, add_special_tokens: bool = False) -> list[str]:
        return text.split()

    def __call__(self, premises: Any, hypotheses: Any, **kwargs: Any) -> "FakeBatchEncoding":
        return FakeBatchEncoding()


class FakeBatchEncoding(dict):
    def to(self, device: Any) -> "FakeBatchEncoding":
        return self


class FakeCategoryModel:
    """Returns a canned (9, 3) logits tensor regardless of input -- column
    order [contradiction, neutral, entailment], matching a typical MNLI
    label2id. `entail_values` supplies the entailment column, one value per
    pipeline.CATEGORY_LABELS slug in order."""

    def __init__(self, entail_values: list[float]) -> None:
        self.config = type(
            "Config", (), {"label2id": {"contradiction": 0, "neutral": 1, "entailment": 2}}
        )()
        self._logits = torch.zeros(len(entail_values), 3)
        self._logits[:, 2] = torch.tensor(entail_values)

    def to(self, device: Any) -> "FakeCategoryModel":
        return self

    def eval(self) -> "FakeCategoryModel":
        return self

    def __call__(self, **kwargs: Any) -> Any:
        return type("Output", (), {"logits": self._logits})()


def test_run_category_stage_skips_loading_model_when_nothing_pending(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("model should not be loaded when there is nothing to process")

    monkeypatch.setattr(pipeline.AutoTokenizer, "from_pretrained", fail_if_called)
    monkeypatch.setattr(
        pipeline.AutoModelForSequenceClassification, "from_pretrained", fail_if_called
    )

    calls = []
    pipeline.run_category_stage(conn, on_progress=lambda *a: calls.append(a))

    assert calls == [("category", 0, 0)]


def test_run_category_stage_writes_winning_label_and_full_distribution(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_article(conn, id=1, title="Deal News", body_text="Company X announced a merger today.")
    conn.commit()

    entail_values = [0.0] * 9
    entail_values[1] = 5.0  # mergers_acquisitions dominates

    monkeypatch.setattr(
        pipeline.AutoTokenizer, "from_pretrained", lambda *_a, **_k: WordCountTokenizer()
    )
    monkeypatch.setattr(
        pipeline.AutoModelForSequenceClassification,
        "from_pretrained",
        lambda *_a, **_k: FakeCategoryModel(entail_values),
    )

    pipeline.run_category_stage(conn)

    detail = db.get_article_detail(conn, 1)
    assert detail is not None
    assert detail["category"]["label"] == "mergers_acquisitions"
    assert detail["category"]["model_name"] == pipeline.CATEGORY_MODEL
    assert detail["category"]["mergers_acquisitions"] == detail["category"]["score"]
    # every one of the 9 distribution columns round-trips through the DB
    for slug, _, _ in pipeline.CATEGORY_LABELS:
        assert slug in detail["category"]


class BatchAwareTokenizer:
    """Like WordCountTokenizer, but the batch encoding it returns carries how
    many (premise, hypothesis) pairs it was actually given -- lets the fake
    model below assert it received every article's pairs in one call."""

    def encode(self, text: str, add_special_tokens: bool = False) -> list[str]:
        return text.split()

    def __call__(self, premises: Any, hypotheses: Any, **kwargs: Any) -> "FakeBatchEncoding":
        assert len(premises) == len(hypotheses)
        return FakeBatchEncoding({"n_pairs": len(premises)})


class FakeBatchAwareCategoryModel:
    """entail_matrix: one 9-length entailment-logit list per article, in
    fetch order. Flattened once; each call consumes exactly as many entries
    as the pair count it's told about, so a real cross-article batch (one
    call covering several articles) gets the right slice for each of them,
    and call_count proves how many forward passes it actually took."""

    def __init__(self, entail_matrix: list[list[float]]) -> None:
        flat = [v for row in entail_matrix for v in row]
        self._flat = torch.tensor(flat)
        self._consumed = 0
        self.call_count = 0
        self.config = type(
            "Config", (), {"label2id": {"contradiction": 0, "neutral": 1, "entailment": 2}}
        )()

    def to(self, device: Any) -> "FakeBatchAwareCategoryModel":
        return self

    def eval(self) -> "FakeBatchAwareCategoryModel":
        return self

    def __call__(self, **kwargs: Any) -> Any:
        self.call_count += 1
        n_pairs = kwargs["n_pairs"]
        entail = self._flat[self._consumed : self._consumed + n_pairs]
        self._consumed += n_pairs
        logits = torch.zeros(n_pairs, 3)
        logits[:, 2] = entail
        return type("Output", (), {"logits": logits})()


def test_run_category_stage_batches_multiple_articles_in_one_forward_pass(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_article(conn, id=1, title="Deal News", body_text="Company X announced a merger today.")
    seed_article(
        conn, id=2, title="Earnings Beat", body_text="Company Y reported record quarterly earnings."
    )
    seed_article(conn, id=3, title="Roundup", body_text="Markets were mixed today across sectors.")
    conn.commit()

    entail_matrix = [[0.0] * 9 for _ in range(3)]
    entail_matrix[0][1] = 5.0  # article 1 -> mergers_acquisitions
    entail_matrix[1][0] = 5.0  # article 2 -> earnings_performance
    # article 3 left uniform -> falls back to "other"

    monkeypatch.setattr(
        pipeline.AutoTokenizer, "from_pretrained", lambda *_a, **_k: BatchAwareTokenizer()
    )
    fake_model = FakeBatchAwareCategoryModel(entail_matrix)
    monkeypatch.setattr(
        pipeline.AutoModelForSequenceClassification, "from_pretrained", lambda *_a, **_k: fake_model
    )

    assert pipeline.CATEGORY_BATCH_SIZE >= 3, (
        "test assumes all 3 seeded articles land in a single batch"
    )
    pipeline.run_category_stage(conn)

    # All 3 articles fit under CATEGORY_BATCH_SIZE, so this must be exactly
    # one forward pass covering all of them, not one call per article.
    assert fake_model.call_count == 1

    detail_1 = db.get_article_detail(conn, 1)
    detail_2 = db.get_article_detail(conn, 2)
    detail_3 = db.get_article_detail(conn, 3)
    assert detail_1 is not None and detail_1["category"]["label"] == "mergers_acquisitions"
    assert detail_2 is not None and detail_2["category"]["label"] == "earnings_performance"
    assert detail_3 is not None and detail_3["category"]["label"] == "other"


def test_run_category_stage_assigns_other_below_threshold(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_article(conn, id=1, title="Roundup", body_text="Markets were mixed today across sectors.")
    conn.commit()

    entail_values = [0.0] * 9  # uniform -> below CATEGORY_CONFIDENCE_THRESHOLD

    monkeypatch.setattr(
        pipeline.AutoTokenizer, "from_pretrained", lambda *_a, **_k: WordCountTokenizer()
    )
    monkeypatch.setattr(
        pipeline.AutoModelForSequenceClassification,
        "from_pretrained",
        lambda *_a, **_k: FakeCategoryModel(entail_values),
    )

    pipeline.run_category_stage(conn)

    detail = db.get_article_detail(conn, 1)
    assert detail is not None
    assert detail["category"]["label"] == "other"
    assert detail["category"]["score"] > 0  # near-miss score preserved, not zeroed out
