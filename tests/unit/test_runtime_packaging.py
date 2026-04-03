from pathlib import Path


def test_dockerfile_uses_uv_lock_and_uv_sync() -> None:
    dockerfile = Path("Dockerfile").read_text()

    assert "COPY pyproject.toml uv.lock ./" in dockerfile
    assert "uv sync --frozen --no-dev --no-install-project" in dockerfile
    assert "poetry install" not in dockerfile
    assert "poetry.lock" not in dockerfile


def test_main_data_quality_configure_logging_passes_service_name(monkeypatch) -> None:
    import orion.main_data_quality as main_data_quality

    captured: list[str] = []

    def fake_setup_logging(service_name: str) -> None:
        captured.append(service_name)

    monkeypatch.setattr(main_data_quality, "setup_logging", fake_setup_logging)

    main_data_quality.configure_logging()

    assert captured == ["orion-data-quality"]
