def test_configure_logging_passes_service_name(monkeypatch):
    import orion.main_pattern_miner as pattern_miner

    captured: list[str] = []

    def fake_setup_logging(service_name: str) -> None:
        captured.append(service_name)

    monkeypatch.setattr(pattern_miner, "setup_logging", fake_setup_logging)

    pattern_miner.configure_logging()

    assert captured == ["orion"]
