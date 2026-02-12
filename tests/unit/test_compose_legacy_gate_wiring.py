import re
from pathlib import Path


def test_compose_wires_per_service_legacy_gate_env_vars() -> None:
    compose_text = Path("docker-compose.yml").read_text()

    assert (
        "- ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER=${ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER:-false}" in compose_text
    )
    assert "- ORION_ENABLE_LEGACY_FLOW_LABELER=${ORION_ENABLE_LEGACY_FLOW_LABELER:-false}" in compose_text
    assert (
        "- ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER=${ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER:-false}" in compose_text
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
    assert "- ORION_ENABLE_LEGACY_PATTERN_MINER_TRAINING=${ORION_ENABLE_LEGACY_PATTERN_MINER_TRAINING:-true}" in block
    assert (
        "- ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING=${ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING:-true}" in block
    )


def test_nightly_backfill_and_quality_guardrails_wire_specific_legacy_gates() -> None:
    compose_text = Path("docker-compose.yml").read_text()

    nightly_block = _service_block(compose_text, "nightly-backfill")
    assert "- ORION_ENABLE_LEGACY_NIGHTLY_BACKFILL=${ORION_ENABLE_LEGACY_NIGHTLY_BACKFILL:-false}" in nightly_block

    guardrails_block = _service_block(compose_text, "quality-guardrails")
    assert (
        "- ORION_ENABLE_LEGACY_QUALITY_GUARDRAILS=${ORION_ENABLE_LEGACY_QUALITY_GUARDRAILS:-false}" in guardrails_block
    )


def test_compose_default_legacy_profile_preserves_model_storage_paths() -> None:
    compose_text = Path("docker-compose.yml").read_text()

    for service_name in (
        "labeler",
        "price_target_labeler",
        "option_quote_tracker",
        "pattern-miner",
        "nightly-backfill",
        "quality-guardrails",
    ):
        block = _service_block(compose_text, service_name)
        assert "- ORION_ENABLE_LEGACY_LABEL_PIPELINES=${ORION_ENABLE_LEGACY_LABEL_PIPELINES:-false}" in block

    labeler_block = _service_block(compose_text, "labeler")
    assert "- ORION_ENABLE_LEGACY_FLOW_LABELER=${ORION_ENABLE_LEGACY_FLOW_LABELER:-false}" in labeler_block

    price_target_block = _service_block(compose_text, "price_target_labeler")
    assert (
        "- ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER=${ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER:-false}"
        in price_target_block
    )

    option_quote_block = _service_block(compose_text, "option_quote_tracker")
    assert (
        "- ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER=${ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER:-false}"
        in option_quote_block
    )

    pattern_miner_block = _service_block(compose_text, "pattern-miner")
    assert "- ORION_ENABLE_LEGACY_PATTERN_MINER=${ORION_ENABLE_LEGACY_PATTERN_MINER:-true}" in pattern_miner_block
    assert (
        "- ORION_ENABLE_LEGACY_PATTERN_MINER_TRAINING=${ORION_ENABLE_LEGACY_PATTERN_MINER_TRAINING:-true}"
        in pattern_miner_block
    )
    assert (
        "- ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING=${ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING:-true}"
        in pattern_miner_block
    )

    nightly_block = _service_block(compose_text, "nightly-backfill")
    assert "- ORION_ENABLE_LEGACY_NIGHTLY_BACKFILL=${ORION_ENABLE_LEGACY_NIGHTLY_BACKFILL:-false}" in nightly_block

    guardrails_block = _service_block(compose_text, "quality-guardrails")
    assert (
        "- ORION_ENABLE_LEGACY_QUALITY_GUARDRAILS=${ORION_ENABLE_LEGACY_QUALITY_GUARDRAILS:-false}" in guardrails_block
    )
