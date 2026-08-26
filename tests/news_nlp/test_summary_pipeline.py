import sqlite3
from typing import Any

import pytest
import torch
from conftest import seed_article

from news_nlp import db, pipeline
from news_nlp.categories import CATEGORY_SLUGS


def seed_sentiment(
    conn: sqlite3.Connection, article_id: int, label: str = "positive", score: float = 0.9
) -> None:
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (?, ?, ?, 0.9, 0.05, 0.05, 'test-model', '2023-01-02T00:00:00Z')""",
        (article_id, label, score),
    )


def seed_category(
    conn: sqlite3.Connection,
    article_id: int,
    label: str = "earnings_performance",
    score: float = 0.9,
) -> None:
    scores = dict.fromkeys(CATEGORY_SLUGS, 0.05)
    scores[label] = score
    db.write_category(
        conn, article_id, label=label, score=score, scores=scores, model_name="test-model"
    )


# hierarchical_summarize_batch()/_summarize_batch() are always monkeypatched
# below, so these tests never touch a real device -- this stands in for the
# type only.
_FAKE_DEVICE = torch.device("cpu")


class WordCountTokenizer:
    """Minimal stand-in for a real tokenizer: chunk_text only needs
    .encode(text, add_special_tokens=False) to count tokens, and none of the
    text used in these tests has a single "sentence" long enough to trigger
    chunk_text's hard-split fallback (which needs return_offsets_mapping)."""

    def encode(self, text: str, add_special_tokens: bool = False) -> list[str]:
        return text.split()


def make_recording_summarizer(
    monkeypatch: pytest.MonkeyPatch, replies: list[str] | None = None
) -> list[list[str]]:
    """Monkeypatch pipeline._summarize_batch to avoid loading a real model,
    and record every call's input batch. `replies` (if given) is consumed
    one-per-text across all calls, in order; otherwise every text gets a
    fixed placeholder ("X")."""
    calls: list[list[str]] = []
    replies_iter = iter(replies) if replies is not None else None

    def fake(texts: list[str], tokenizer: Any, model: Any, device: torch.device) -> list[str]:
        calls.append(list(texts))
        if replies_iter is not None:
            return [next(replies_iter) for _ in texts]
        return ["X" for _ in texts]

    monkeypatch.setattr(pipeline, "_summarize_batch", fake)
    return calls


# --- hierarchical_summarize_batch -------------------------------------------


def test_hierarchical_summarize_batch_single_chunk_is_a_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = make_recording_summarizer(monkeypatch, replies=["Short summary."])

    results = pipeline.hierarchical_summarize_batch(
        ["One short sentence."],
        WordCountTokenizer(),
        model=None,
        device=_FAKE_DEVICE,
        max_input_tokens=100,
    )

    assert results == [("Short summary.", 1)]
    assert len(calls) == 1  # one batch call


def test_hierarchical_summarize_batch_empty_text_returns_no_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = make_recording_summarizer(monkeypatch)

    results = pipeline.hierarchical_summarize_batch(
        [""], WordCountTokenizer(), model=None, device=_FAKE_DEVICE, max_input_tokens=100
    )

    assert results == [("", 0)]
    assert calls == []


def test_hierarchical_summarize_batch_empty_input_list_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = make_recording_summarizer(monkeypatch)

    results = pipeline.hierarchical_summarize_batch(
        [], WordCountTokenizer(), model=None, device=_FAKE_DEVICE, max_input_tokens=100
    )

    assert results == []
    assert calls == []


def test_hierarchical_summarize_batch_multi_chunk_triggers_a_reduce_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Each 3-word sentence is exactly one chunk at max_input_tokens=3 --
    # 3 sentences -> 3 leaf chunks -> 3 first-pass summaries -> those get
    # joined ("X X X") and summarized once more since len(summaries) > 1.
    calls = make_recording_summarizer(monkeypatch)
    text = "AAA BBB CCC. DDD EEE FFF. GGG HHH III."

    results = pipeline.hierarchical_summarize_batch(
        [text], WordCountTokenizer(), model=None, device=_FAKE_DEVICE, max_input_tokens=3
    )

    summary, num_chunks = results[0]
    assert num_chunks == 3  # leaf-level chunk count, not the reduced count
    assert summary == "X"  # the reduce pass's own (stubbed) output
    assert len(calls) == 2  # one batch call for the 3 leaf chunks, one for the reduce pass
    assert len(calls[0]) == 3
    assert calls[1] == ["X X X"]  # the reduce pass's input was the joined leaf summaries


def test_hierarchical_summarize_batch_reduce_pass_summarizes_the_joined_chunk_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = make_recording_summarizer(monkeypatch, replies=["S1", "S2", "S3", "final"])
    text = "AAA BBB CCC. DDD EEE FFF. GGG HHH III."

    results = pipeline.hierarchical_summarize_batch(
        [text], WordCountTokenizer(), model=None, device=_FAKE_DEVICE, max_input_tokens=3
    )

    summary, num_chunks = results[0]
    assert num_chunks == 3
    assert calls[-1] == ["S1 S2 S3"]  # the reduce pass's input was the joined leaf summaries
    assert summary == "final"


def test_hierarchical_summarize_batch_pools_multiple_texts_into_one_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = make_recording_summarizer(monkeypatch, replies=["Summary A.", "Summary B."])

    results = pipeline.hierarchical_summarize_batch(
        ["Text A.", "Text B."],
        WordCountTokenizer(),
        model=None,
        device=_FAKE_DEVICE,
        max_input_tokens=100,
        batch_size=4,
    )

    assert results == [("Summary A.", 1), ("Summary B.", 1)]
    assert len(calls) == 1  # both texts' single leaf chunk pooled into one generate() call
    assert calls[0] == ["Text A.", "Text B."]


def test_hierarchical_summarize_batch_respects_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = make_recording_summarizer(monkeypatch)

    pipeline.hierarchical_summarize_batch(
        ["Text A.", "Text B.", "Text C."],
        WordCountTokenizer(),
        model=None,
        device=_FAKE_DEVICE,
        max_input_tokens=100,
        batch_size=2,
    )

    assert [len(c) for c in calls] == [2, 1]  # 3 texts split into batches of at most 2


# --- run_company_summary_stage --------------------------------------------


def test_run_company_summary_stage_skips_loading_model_when_nothing_pending(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("model should not be loaded when there is nothing to process")

    monkeypatch.setattr(pipeline.AutoTokenizer, "from_pretrained", fail_if_called)
    monkeypatch.setattr(pipeline.AutoModelForSeq2SeqLM, "from_pretrained", fail_if_called)

    calls = []
    pipeline.run_company_summary_stage(conn, on_progress=lambda *a: calls.append(a))

    assert calls == [("company_summary", 0, 0)]


def test_run_company_summary_stage_writes_a_summary_per_pending_article(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_article(conn, id=1)
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (1, 'positive', 0.9, 0.9, 0.05, 0.05, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO article_entities (article_id, entity_type, text, start_char, end_char, score, model_name, processed_at)
           VALUES (1, 'ORG', '3M', 0, 2, 0.9, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.commit()

    monkeypatch.setattr(
        pipeline.AutoTokenizer, "from_pretrained", lambda *_a, **_k: WordCountTokenizer()
    )
    monkeypatch.setattr(
        pipeline.AutoModelForSeq2SeqLM, "from_pretrained", lambda *_a, **_k: FakeModel()
    )
    make_recording_summarizer(monkeypatch, replies=["Generated summary."])

    pipeline.run_company_summary_stage(conn)

    detail = db.get_article_detail(conn, 1)
    assert detail is not None
    assert detail["summary"]["summary_text"] == "Generated summary."
    assert detail["summary"]["model_name"] == pipeline.SUMMARY_MODEL


def test_run_company_summary_stage_pools_multiple_articles_into_one_model_call(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in (1, 2):
        seed_article(conn, id=i)
        conn.execute(
            """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
               VALUES (?, 'positive', 0.9, 0.9, 0.05, 0.05, 'test-model', '2023-01-02T00:00:00Z')""",
            (i,),
        )
        conn.execute(
            """INSERT INTO article_entities (article_id, entity_type, text, start_char, end_char, score, model_name, processed_at)
               VALUES (?, 'ORG', '3M', 0, 2, 0.9, 'test-model', '2023-01-02T00:00:00Z')""",
            (i,),
        )
    conn.commit()

    monkeypatch.setattr(
        pipeline.AutoTokenizer, "from_pretrained", lambda *_a, **_k: WordCountTokenizer()
    )
    monkeypatch.setattr(
        pipeline.AutoModelForSeq2SeqLM, "from_pretrained", lambda *_a, **_k: FakeModel()
    )
    calls = make_recording_summarizer(monkeypatch, replies=["Summary 1.", "Summary 2."])

    pipeline.run_company_summary_stage(conn)

    # Both pending articles fit within SUMMARY_BATCH_SIZE, so they're pooled
    # into a single generate() call instead of one call per article.
    assert len(calls) == 1
    assert len(calls[0]) == 2
    detail_1 = db.get_article_detail(conn, 1)
    detail_2 = db.get_article_detail(conn, 2)
    assert detail_1 is not None
    assert detail_2 is not None
    assert detail_1["summary"]["summary_text"] == "Summary 1."
    assert detail_2["summary"]["summary_text"] == "Summary 2."


class FakeModel:
    def to(self, device: Any) -> "FakeModel":
        return self

    def eval(self) -> "FakeModel":
        return self


# --- run_sector_summary_stage ----------------------------------------------


def test_run_sector_summary_stage_skips_loading_model_when_nothing_pending(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("model should not be loaded when there is nothing to process")

    monkeypatch.setattr(pipeline.AutoTokenizer, "from_pretrained", fail_if_called)
    monkeypatch.setattr(pipeline.AutoModelForSeq2SeqLM, "from_pretrained", fail_if_called)

    calls = []
    pipeline.run_sector_summary_stage(conn, on_progress=lambda *a: calls.append(a))

    assert calls == [("sector_summary", 0, 0)]


def test_run_sector_summary_stage_writes_one_summary_per_group(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_article(conn, id=1, company="3M", ticker="MMM", pub_date="2026-08-03T00:00:00Z")
    seed_sentiment(conn, 1)
    seed_category(conn, 1)
    db.write_company_summary(conn, 1, "3M did well this week.", 1, "facebook/bart-large-cnn")
    conn.commit()

    monkeypatch.setattr(
        pipeline.AutoTokenizer, "from_pretrained", lambda *_a, **_k: WordCountTokenizer()
    )
    monkeypatch.setattr(
        pipeline.AutoModelForSeq2SeqLM, "from_pretrained", lambda *_a, **_k: FakeModel()
    )
    calls = make_recording_summarizer(monkeypatch, replies=["This week saw strong activity."])

    pipeline.run_sector_summary_stage(conn)

    results = db.list_sector_summaries(conn)
    assert len(results) == 1
    assert results[0]["num_articles"] == 1
    assert results[0]["num_companies"] == 1
    assert "This week saw strong activity." in results[0]["summary_text"]
    assert "3M did well this week." in results[0]["summary_text"]  # company bullet still present
    # The model is only ever shown the aggregate-stats intro seed, never the
    # raw company c_summary text -- the fix for the original "frankenstein"
    # blending bug.
    assert len(calls) == 1
    assert "3M did well this week." not in calls[0][0]
    assert "MMM" not in calls[0][0]


def test_run_sector_summary_stage_pools_multiple_groups_into_one_model_call(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_article(
        conn,
        id=1,
        company="3M",
        ticker="MMM",
        gics_sector="Industrials",
        gics_sub_industry="Industrial Conglomerates",
        pub_date="2026-08-03T00:00:00Z",
    )
    seed_article(
        conn,
        id=2,
        company="Nvidia",
        ticker="NVDA",
        gics_sector="Information Technology",
        gics_sub_industry="Semiconductors",
        pub_date="2026-08-03T00:00:00Z",
    )
    seed_sentiment(conn, 1)
    seed_sentiment(conn, 2)
    seed_category(conn, 1)
    seed_category(conn, 2)
    db.write_company_summary(conn, 1, "3M did well this week.", 1, "facebook/bart-large-cnn")
    db.write_company_summary(conn, 2, "Nvidia did well this week.", 1, "facebook/bart-large-cnn")
    conn.commit()

    monkeypatch.setattr(
        pipeline.AutoTokenizer, "from_pretrained", lambda *_a, **_k: WordCountTokenizer()
    )
    monkeypatch.setattr(
        pipeline.AutoModelForSeq2SeqLM, "from_pretrained", lambda *_a, **_k: FakeModel()
    )
    calls = make_recording_summarizer(monkeypatch, replies=["Industrials intro.", "Tech intro."])

    pipeline.run_sector_summary_stage(conn)

    results = db.list_sector_summaries(conn)
    assert len(results) == 2
    # Both groups' intro seeds fit within SUMMARY_BATCH_SIZE, so they're
    # pooled into a single generate() call instead of one call per group.
    assert len(calls) == 1
    assert len(calls[0]) == 2
