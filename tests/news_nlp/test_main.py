def test_main_calls_uvicorn_run_with_app_target(monkeypatch):
    import main as main_module

    calls = []
    monkeypatch.setattr(main_module.uvicorn, "run", lambda *a, **k: calls.append((a, k)))

    main_module.main()

    assert calls == [(("app:app",), {"host": "127.0.0.1", "port": 8000, "reload": False})]
