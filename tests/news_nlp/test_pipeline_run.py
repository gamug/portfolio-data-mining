"""run_pipeline's stage-gating: sentiment/NER/category always run, the two
summary stages (c_summary, sector_summary) are opt-in via `summarize`."""

import pytest

from news_nlp import pipeline


class FakeConn:
    def close(self) -> None:
        pass


def _stub_out_stages(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    monkeypatch.setattr(pipeline.db, "connect", FakeConn)
    monkeypatch.setattr(pipeline.db, "init_schema", lambda conn: None)
    monkeypatch.setattr(pipeline, "run_sentiment_stage", lambda *a, **k: calls.append("sentiment"))
    monkeypatch.setattr(pipeline, "run_ner_stage", lambda *a, **k: calls.append("ner"))
    monkeypatch.setattr(pipeline, "run_category_stage", lambda *a, **k: calls.append("category"))
    monkeypatch.setattr(
        pipeline, "run_company_summary_stage", lambda *a, **k: calls.append("company_summary")
    )
    monkeypatch.setattr(
        pipeline, "run_sector_summary_stage", lambda *a, **k: calls.append("sector_summary")
    )


def test_run_pipeline_skips_summary_stages_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _stub_out_stages(monkeypatch, calls)

    pipeline.run_pipeline()

    assert calls == ["sentiment", "ner", "category"]


def test_run_pipeline_runs_summary_stages_when_summarize_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _stub_out_stages(monkeypatch, calls)

    pipeline.run_pipeline(summarize=True)

    assert calls == ["sentiment", "ner", "category", "company_summary", "sector_summary"]


# --- _warn_if_cpu -------------------------------------------------------


def test_warn_if_cpu_prints_banner_when_device_is_cpu(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pipeline, "DEVICE", pipeline.torch.device("cpu"))

    pipeline._warn_if_cpu()

    out = capsys.readouterr().out
    assert "CUDA is not available" in out
    assert "cu124" in out


def test_warn_if_cpu_silent_when_device_is_cuda(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pipeline, "DEVICE", pipeline.torch.device("cuda"))

    pipeline._warn_if_cpu()

    assert capsys.readouterr().out == ""


def test_run_pipeline_warns_once_on_cpu(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    _stub_out_stages(monkeypatch, calls)
    monkeypatch.setattr(pipeline, "DEVICE", pipeline.torch.device("cpu"))

    pipeline.run_pipeline()

    out = capsys.readouterr().out
    assert out.count("CUDA is not available") == 1
