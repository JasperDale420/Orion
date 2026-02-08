from pathlib import Path


def test_compose_wires_per_service_legacy_gate_env_vars() -> None:
    compose_text = Path("docker-compose.yml").read_text()

    assert "- ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER=${ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER:-true}" in compose_text
    assert "- ORION_ENABLE_LEGACY_FLOW_LABELER=${ORION_ENABLE_LEGACY_FLOW_LABELER:-true}" in compose_text
    assert "- ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER=${ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER:-true}" in compose_text
