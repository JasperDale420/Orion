from __future__ import annotations

import pytest

from orion import main_feature_enrichment as feature_enrichment


def test_gateway_contract_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "orion.main_feature_enrichment.system_settings.data_gateway_url",
        "http://gateway:8080",
        raising=False,
    )
    monkeypatch.setattr(
        "orion.main_feature_enrichment.system_settings.data_gateway_api_key",
        "",
        raising=False,
    )

    with pytest.raises(ValueError, match="DATA_GATEWAY_API_KEY/GATEWAY_API_KEY"):
        feature_enrichment._gateway_runtime_contract()


def test_gateway_contract_returns_normalized_url_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "orion.main_feature_enrichment.system_settings.data_gateway_url",
        "http://gateway:8080/",
        raising=False,
    )
    monkeypatch.setattr(
        "orion.main_feature_enrichment.system_settings.data_gateway_api_key",
        "test-key",
        raising=False,
    )

    url, key = feature_enrichment._gateway_runtime_contract()
    assert url == "http://gateway:8080"
    assert key == "test-key"
