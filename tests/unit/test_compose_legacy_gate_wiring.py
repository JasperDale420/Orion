import re
from pathlib import Path


def test_compose_wires_per_service_legacy_gate_env_vars() -> None:
    compose_text = Path("docker-compose.yml").read_text()

    assert (
        "- ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER=${ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER:-true}" in compose_text
    )
    assert "- ORION_ENABLE_LEGACY_FLOW_LABELER=${ORION_ENABLE_LEGACY_FLOW_LABELER:-true}" in compose_text
    assert (
        "- ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER=${ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER:-true}" in compose_text
    )


def _service_block(compose_text: str, service_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {service_name}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)",
        compose_text,
    )
    assert match is not None
    return match.group(1)


def test_legacy_label_services_use_on_failure_restart_policy() -> None:
    compose_text = Path("docker-compose.yml").read_text()

    for service_name in ("labeler", "price_target_labeler", "option_quote_tracker"):
        block = _service_block(compose_text, service_name)
        assert "restart: on-failure" in block


def test_legacy_label_stack_services_are_profiled_for_opt_in() -> None:
    compose_text = Path("docker-compose.yml").read_text()

    for service_name in (
        "labeler",
        "price_target_labeler",
        "option_quote_tracker",
        "nightly-backfill",
        "quality-guardrails",
    ):
        block = _service_block(compose_text, service_name)
        assert 'profiles: [ "legacy-labels" ]' in block


def test_feature_enrichment_wires_gateway_api_key_env() -> None:
    compose_text = Path("docker-compose.yml").read_text()
    block = _service_block(compose_text, "feature_enrichment")
    assert "- GATEWAY_API_KEY=${GATEWAY_API_KEY}" in block


def test_pattern_miner_is_profiled_with_legacy_label_stack() -> None:
    compose_text = Path("docker-compose.yml").read_text()
    block = _service_block(compose_text, "pattern-miner")
    assert 'profiles: [ "legacy-labels" ]' in block
    assert "- ORION_ENABLE_LEGACY_PATTERN_MINER=${ORION_ENABLE_LEGACY_PATTERN_MINER:-true}" in block
