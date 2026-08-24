import pytest

from news_nlp import setup


def test_download_models_fetches_every_model_and_verifies_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_calls: list[str] = []
    config_calls: list[str] = []

    def fake_snapshot_download(repo_id: str) -> str:
        snapshot_calls.append(repo_id)
        return f"/fake/cache/{repo_id}"

    monkeypatch.setattr(setup, "snapshot_download", fake_snapshot_download)
    monkeypatch.setattr(setup.AutoConfig, "from_pretrained", config_calls.append)

    setup.download_models()

    assert snapshot_calls == list(setup.MODELS)
    assert config_calls == list(setup.MODELS)
    assert setup.CATEGORY_MODEL in setup.MODELS
