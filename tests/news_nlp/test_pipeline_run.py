"""run_pipeline's stage-gating: sentiment/NER always run, the two summary
stages (c_summary, sector_summary) are opt-in via `summarize`."""
from news_nlp import pipeline


class FakeConn:
    def close(self):
        pass


def _stub_out_stages(monkeypatch, calls):
    monkeypatch.setattr(pipeline.db, "connect", lambda: FakeConn())
    monkeypatch.setattr(pipeline.db, "init_schema", lambda conn: None)
    monkeypatch.setattr(pipeline, "run_sentiment_stage", lambda *a, **k: calls.append("sentiment"))
    monkeypatch.setattr(pipeline, "run_ner_stage", lambda *a, **k: calls.append("ner"))
    monkeypatch.setattr(pipeline, "run_company_summary_stage", lambda *a, **k: calls.append("company_summary"))
    monkeypatch.setattr(pipeline, "run_sector_summary_stage", lambda *a, **k: calls.append("sector_summary"))


def test_run_pipeline_skips_summary_stages_by_default(monkeypatch):
    calls = []
    _stub_out_stages(monkeypatch, calls)

    pipeline.run_pipeline()

    assert calls == ["sentiment", "ner"]


def test_run_pipeline_runs_summary_stages_when_summarize_true(monkeypatch):
    calls = []
    _stub_out_stages(monkeypatch, calls)

    pipeline.run_pipeline(summarize=True)

    assert calls == ["sentiment", "ner", "company_summary", "sector_summary"]
