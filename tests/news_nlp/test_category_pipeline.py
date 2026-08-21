import torch
from conftest import seed_article

from news_nlp import pipeline, db


# --- classify_category_scores (pure function, no model needed) ------------


def test_classify_category_scores_picks_highest_entailment_and_returns_full_distribution():
    entail_logits = [0.0] * 9
    entail_logits[1] = 5.0  # index 1 == mergers_acquisitions, see pipeline.CATEGORY_LABELS

    label, score, scores = pipeline.classify_category_scores(entail_logits)

    assert label == "mergers_acquisitions"
    assert score > pipeline.CATEGORY_CONFIDENCE_THRESHOLD
    assert set(scores) == {slug for slug, _, _ in pipeline.CATEGORY_LABELS}
    assert abs(sum(scores.values()) - 1.0) < 1e-6


def test_classify_category_scores_falls_back_to_other_below_threshold():
    entail_logits = [0.0] * 9  # uniform distribution -> ~0.111 each, below the 0.4 threshold

    label, score, scores = pipeline.classify_category_scores(entail_logits)

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

    def encode(self, text, add_special_tokens=False):
        return text.split()

    def __call__(self, premises, hypotheses, **kwargs):
        return FakeBatchEncoding()


class FakeBatchEncoding(dict):
    def to(self, device):
        return self


class FakeCategoryModel:
    """Returns a canned (9, 3) logits tensor regardless of input -- column
    order [contradiction, neutral, entailment], matching a typical MNLI
    label2id. `entail_values` supplies the entailment column, one value per
    pipeline.CATEGORY_LABELS slug in order."""

    def __init__(self, entail_values):
        self.config = type("Config", (), {"label2id": {"contradiction": 0, "neutral": 1, "entailment": 2}})()
        self._logits = torch.zeros(len(entail_values), 3)
        self._logits[:, 2] = torch.tensor(entail_values)

    def to(self, device):
        return self

    def eval(self):
        return self

    def __call__(self, **kwargs):
        return type("Output", (), {"logits": self._logits})()


def test_run_category_stage_skips_loading_model_when_nothing_pending(conn, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("model should not be loaded when there is nothing to process")

    monkeypatch.setattr(pipeline.AutoTokenizer, "from_pretrained", fail_if_called)
    monkeypatch.setattr(pipeline.AutoModelForSequenceClassification, "from_pretrained", fail_if_called)

    calls = []
    pipeline.run_category_stage(conn, on_progress=lambda *a: calls.append(a))

    assert calls == [("category", 0, 0)]


def test_run_category_stage_writes_winning_label_and_full_distribution(conn, monkeypatch):
    seed_article(conn, id=1, title="Deal News", body_text="Company X announced a merger today.")
    conn.commit()

    entail_values = [0.0] * 9
    entail_values[1] = 5.0  # mergers_acquisitions dominates

    monkeypatch.setattr(pipeline.AutoTokenizer, "from_pretrained", lambda *_a, **_k: WordCountTokenizer())
    monkeypatch.setattr(
        pipeline.AutoModelForSequenceClassification, "from_pretrained",
        lambda *_a, **_k: FakeCategoryModel(entail_values),
    )

    pipeline.run_category_stage(conn)

    detail = db.get_article_detail(conn, 1)
    assert detail["category"]["label"] == "mergers_acquisitions"
    assert detail["category"]["model_name"] == pipeline.CATEGORY_MODEL
    assert detail["category"]["mergers_acquisitions"] == detail["category"]["score"]
    # every one of the 9 distribution columns round-trips through the DB
    for slug, _, _ in pipeline.CATEGORY_LABELS:
        assert slug in detail["category"]


def test_run_category_stage_assigns_other_below_threshold(conn, monkeypatch):
    seed_article(conn, id=1, title="Roundup", body_text="Markets were mixed today across sectors.")
    conn.commit()

    entail_values = [0.0] * 9  # uniform -> below CATEGORY_CONFIDENCE_THRESHOLD

    monkeypatch.setattr(pipeline.AutoTokenizer, "from_pretrained", lambda *_a, **_k: WordCountTokenizer())
    monkeypatch.setattr(
        pipeline.AutoModelForSequenceClassification, "from_pretrained",
        lambda *_a, **_k: FakeCategoryModel(entail_values),
    )

    pipeline.run_category_stage(conn)

    detail = db.get_article_detail(conn, 1)
    assert detail["category"]["label"] == "other"
    assert detail["category"]["score"] > 0  # near-miss score preserved, not zeroed out
