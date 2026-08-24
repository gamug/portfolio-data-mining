import sqlite3
from typing import Any

import pytest

from news_nlp import pipeline


def test_run_sentiment_stage_reports_empty_progress_without_loading_model(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("model should not be loaded when there is nothing to process")

    monkeypatch.setattr(pipeline.AutoTokenizer, "from_pretrained", fail_if_called)
    monkeypatch.setattr(
        pipeline.AutoModelForSequenceClassification, "from_pretrained", fail_if_called
    )

    calls = []
    pipeline.run_sentiment_stage(conn, on_progress=lambda *a: calls.append(a))

    assert calls == [("sentiment", 0, 0)]


def test_run_ner_stage_reports_empty_progress_without_loading_model(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("model should not be loaded when there is nothing to process")

    monkeypatch.setattr(pipeline.AutoTokenizer, "from_pretrained", fail_if_called)
    monkeypatch.setattr(pipeline.AutoModelForTokenClassification, "from_pretrained", fail_if_called)

    calls = []
    pipeline.run_ner_stage(conn, on_progress=lambda *a: calls.append(a))

    assert calls == [("ner", 0, 0)]


def test_ner_model_points_at_published_hub_repo() -> None:
    assert pipeline.NER_MODEL == "gamug/sec-bert-finer-ord-ner"
    assert not hasattr(pipeline, "NER_MODEL_PATH")
