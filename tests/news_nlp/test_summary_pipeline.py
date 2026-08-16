from conftest import seed_article

from news_nlp import pipeline, db


class WordCountTokenizer:
    """Minimal stand-in for a real tokenizer: chunk_text only needs
    .encode(text, add_special_tokens=False) to count tokens, and none of the
    text used in these tests has a single "sentence" long enough to trigger
    chunk_text's hard-split fallback (which needs return_offsets_mapping)."""

    def encode(self, text, add_special_tokens=False):
        return text.split()


def make_recording_summarizer(monkeypatch, replies=None):
    """Monkeypatch pipeline._summarize_one to avoid loading a real model,
    and record every call. `replies` (if given) is consumed one-per-call;
    otherwise every call returns a fixed placeholder."""
    calls = []
    replies = iter(replies) if replies is not None else None

    def fake(text, tokenizer, model, device):
        calls.append(text)
        return next(replies) if replies is not None else "X"

    monkeypatch.setattr(pipeline, "_summarize_one", fake)
    return calls


# --- hierarchical_summarize -----------------------------------------------


def test_hierarchical_summarize_single_chunk_is_a_passthrough(monkeypatch):
    calls = make_recording_summarizer(monkeypatch, replies=["Short summary."])

    summary, num_chunks = pipeline.hierarchical_summarize(
        "One short sentence.", WordCountTokenizer(), model=None, device=None, max_input_tokens=100
    )

    assert summary == "Short summary."
    assert num_chunks == 1
    assert len(calls) == 1


def test_hierarchical_summarize_empty_text_returns_no_chunks(monkeypatch):
    calls = make_recording_summarizer(monkeypatch)

    summary, num_chunks = pipeline.hierarchical_summarize(
        "", WordCountTokenizer(), model=None, device=None, max_input_tokens=100
    )

    assert summary == ""
    assert num_chunks == 0
    assert calls == []


def test_hierarchical_summarize_multi_chunk_triggers_a_reduce_pass(monkeypatch):
    # Each 3-word sentence is exactly one chunk at max_input_tokens=3 --
    # 3 sentences -> 3 leaf chunks -> 3 first-pass summaries -> those get
    # joined ("X X X") and summarized once more since len(summaries) > 1.
    calls = make_recording_summarizer(monkeypatch)
    text = "AAA BBB CCC. DDD EEE FFF. GGG HHH III."

    summary, num_chunks = pipeline.hierarchical_summarize(
        text, WordCountTokenizer(), model=None, device=None, max_input_tokens=3
    )

    assert num_chunks == 3          # leaf-level chunk count, not the reduced count
    assert len(calls) == 4          # 3 leaf summaries + 1 reduce pass
    assert summary == "X"           # the reduce pass's own (stubbed) output


def test_hierarchical_summarize_reduce_pass_summarizes_the_joined_chunk_summaries(monkeypatch):
    calls = make_recording_summarizer(monkeypatch, replies=["S1", "S2", "S3", "final"])
    text = "AAA BBB CCC. DDD EEE FFF. GGG HHH III."

    summary, num_chunks = pipeline.hierarchical_summarize(
        text, WordCountTokenizer(), model=None, device=None, max_input_tokens=3
    )

    assert num_chunks == 3
    assert calls[-1] == "S1 S2 S3"  # the reduce pass's input was the joined leaf summaries
    assert summary == "final"


# --- run_company_summary_stage --------------------------------------------


def test_run_company_summary_stage_skips_loading_model_when_nothing_pending(conn, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("model should not be loaded when there is nothing to process")

    monkeypatch.setattr(pipeline.AutoTokenizer, "from_pretrained", fail_if_called)
    monkeypatch.setattr(pipeline.AutoModelForSeq2SeqLM, "from_pretrained", fail_if_called)

    calls = []
    pipeline.run_company_summary_stage(conn, on_progress=lambda *a: calls.append(a))

    assert calls == [("company_summary", 0, 0)]


def test_run_company_summary_stage_writes_a_summary_per_pending_article(conn, monkeypatch):
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

    monkeypatch.setattr(pipeline.AutoTokenizer, "from_pretrained", lambda *_a, **_k: WordCountTokenizer())
    monkeypatch.setattr(pipeline.AutoModelForSeq2SeqLM, "from_pretrained", lambda *_a, **_k: FakeModel())
    make_recording_summarizer(monkeypatch, replies=["Generated summary."])

    pipeline.run_company_summary_stage(conn)

    detail = db.get_article_detail(conn, 1)
    assert detail["summary"]["summary_text"] == "Generated summary."
    assert detail["summary"]["model_name"] == pipeline.SUMMARY_MODEL


class FakeModel:
    def to(self, device):
        return self

    def eval(self):
        return self


# --- run_sector_summary_stage ----------------------------------------------


def test_run_sector_summary_stage_skips_loading_model_when_nothing_pending(conn, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("model should not be loaded when there is nothing to process")

    monkeypatch.setattr(pipeline.AutoTokenizer, "from_pretrained", fail_if_called)
    monkeypatch.setattr(pipeline.AutoModelForSeq2SeqLM, "from_pretrained", fail_if_called)

    calls = []
    pipeline.run_sector_summary_stage(conn, on_progress=lambda *a: calls.append(a))

    assert calls == [("sector_summary", 0, 0)]


def test_run_sector_summary_stage_writes_one_summary_per_group(conn, monkeypatch):
    seed_article(conn, id=1, company="3M", ticker="MMM", pub_date="2026-08-03T00:00:00Z")
    db.write_company_summary(conn, 1, "3M did well this week.", 1, "facebook/bart-large-cnn")
    conn.commit()

    monkeypatch.setattr(pipeline.AutoTokenizer, "from_pretrained", lambda *_a, **_k: WordCountTokenizer())
    monkeypatch.setattr(pipeline.AutoModelForSeq2SeqLM, "from_pretrained", lambda *_a, **_k: FakeModel())
    make_recording_summarizer(monkeypatch, replies=["Sector did well this week."])

    pipeline.run_sector_summary_stage(conn)

    results = db.list_sector_summaries(conn)
    assert len(results) == 1
    assert results[0]["summary_text"] == "Sector did well this week."
    assert results[0]["num_articles"] == 1
    assert results[0]["num_companies"] == 1
