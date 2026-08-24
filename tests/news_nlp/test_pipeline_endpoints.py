import threading
import time

import pytest
from fastapi.testclient import TestClient

import apps.news_nlp_api as app_module
from news_nlp.pipeline import ProgressCallback


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_pipeline_status_idle_initially(client: TestClient) -> None:
    resp = client.get("/pipeline/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "idle"
    assert body["processed"] == 0
    assert body["total"] == 0


def _poll_until_not_running(client: TestClient, attempts: int = 100) -> dict:
    status = {}
    for _ in range(attempts):
        status = client.get("/pipeline/status").json()
        if status["status"] != "running":
            break
        time.sleep(0.01)
    return status


def test_pipeline_run_completes_and_updates_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run_pipeline(
        limit: int | None = None,
        summarize: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        assert on_progress is not None
        on_progress("sentiment", 0, 2)
        on_progress("sentiment", 1, 2)
        on_progress("sentiment", 2, 2)

    monkeypatch.setattr(app_module.pipeline, "run_pipeline", fake_run_pipeline)

    resp = client.post("/pipeline/run", json={"limit": 2})
    assert resp.status_code == 202
    # The stub pipeline is synchronous and trivial, so the background thread
    # can legitimately finish before this response body is read back.
    assert resp.json()["status"] in ("running", "done")

    status = _poll_until_not_running(client)
    assert status["status"] == "done"
    assert status["processed"] == 2
    assert status["total"] == 2


def test_pipeline_run_defaults_summarize_to_false(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    received = {}

    def fake_run_pipeline(
        limit: int | None = None,
        summarize: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        received["summarize"] = summarize

    monkeypatch.setattr(app_module.pipeline, "run_pipeline", fake_run_pipeline)

    resp = client.post("/pipeline/run", json={})
    assert resp.status_code == 202

    _poll_until_not_running(client)
    assert received["summarize"] is False


def test_pipeline_run_passes_summarize_flag_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    received = {}

    def fake_run_pipeline(
        limit: int | None = None,
        summarize: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        received["summarize"] = summarize

    monkeypatch.setattr(app_module.pipeline, "run_pipeline", fake_run_pipeline)

    resp = client.post("/pipeline/run", json={"summarize": True})
    assert resp.status_code == 202

    _poll_until_not_running(client)
    assert received["summarize"] is True


def test_pipeline_run_conflict_returns_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_run_pipeline(
        limit: int | None = None,
        summarize: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(app_module.pipeline, "run_pipeline", slow_run_pipeline)

    resp1 = client.post("/pipeline/run", json={})
    assert resp1.status_code == 202
    assert started.wait(timeout=5)

    resp2 = client.post("/pipeline/run", json={})
    assert resp2.status_code == 409

    release.set()


def test_pipeline_run_error_sets_error_status_and_allows_retry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_run_pipeline(
        limit: int | None = None,
        summarize: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module.pipeline, "run_pipeline", failing_run_pipeline)

    resp = client.post("/pipeline/run", json={})
    assert resp.status_code == 202

    status = _poll_until_not_running(client)
    assert status["status"] == "error"
    assert status["error"] == "boom"

    monkeypatch.setattr(
        app_module.pipeline,
        "run_pipeline",
        lambda limit=None, summarize=False, on_progress=None: None,  # type: ignore[misc]
    )
    resp_retry = client.post("/pipeline/run", json={})
    assert resp_retry.status_code == 202
