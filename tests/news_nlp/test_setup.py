from news_nlp import setup


def test_download_models_fetches_every_model_and_verifies_config(monkeypatch):
    snapshot_calls = []
    config_calls = []

    monkeypatch.setattr(
        setup, "snapshot_download",
        lambda repo_id: snapshot_calls.append(repo_id) or f"/fake/cache/{repo_id}",
    )
    monkeypatch.setattr(
        setup.AutoConfig, "from_pretrained",
        lambda repo_id: config_calls.append(repo_id),
    )

    setup.download_models()

    assert snapshot_calls == list(setup.MODELS)
    assert config_calls == list(setup.MODELS)
