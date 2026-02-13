import re
from pathlib import Path


def test_compose_wires_per_service_legacy_gate_env_vars() -> None:
    compose_text = Path("docker-compose.yml").read_text()

    assert (
        "- ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER=${ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER:-false}" in compose_text
    )
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

    for service_name in ("price_target_labeler", "option_quote_tracker"):
        block = _service_block(compose_text, service_name)
        assert "restart: on-failure" in block


def test_legacy_label_stack_services_are_profiled_for_opt_in() -> None:
    compose_text = Path("docker-compose.yml").read_text()

    for service_name in (
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
    assert "- GATEWAY_API_KEY=${DATA_GATEWAY_API_KEY:-gw_orion_trading_key_55555}" in block
    assert (
        "- ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH=${ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH:-false}"
        in block
    )


def test_ingestion_wires_gateway_api_key_env() -> None:
    compose_text = Path("docker-compose.yml").read_text()
    block = _service_block(compose_text, "ingestion")
    assert "- GATEWAY_API_KEY=${DATA_GATEWAY_API_KEY:-gw_orion_trading_key_55555}" in block


def test_pattern_miner_is_profiled_with_legacy_label_stack() -> None:
    compose_text = Path("docker-compose.yml").read_text()
    block = _service_block(compose_text, "pattern-miner")
    assert 'profiles: [ "legacy-labels" ]' in block
    assert "- ORION_ENABLE_LEGACY_PATTERN_MINER=${ORION_ENABLE_LEGACY_PATTERN_MINER:-true}" in block
    assert "- ORION_ENABLE_LEGACY_PATTERN_MINER_TRAINING=${ORION_ENABLE_LEGACY_PATTERN_MINER_TRAINING:-true}" in block
    assert (
        "- ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING=${ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING:-true}" in block
    )
    assert "- ORION_PATTERN_MINER_TRAINING_SOURCE=${ORION_PATTERN_MINER_TRAINING_SOURCE:-heber_gold}" in block
    assert "- ORION_EXIT_CLASSIFIER_TRAINING_SOURCE=${ORION_EXIT_CLASSIFIER_TRAINING_SOURCE:-heber_gold}" in block


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
        "price_target_labeler",
        "option_quote_tracker",
        "pattern-miner",
        "nightly-backfill",
        "quality-guardrails",
    ):
        block = _service_block(compose_text, service_name)
        assert "- ORION_ENABLE_LEGACY_LABEL_PIPELINES=${ORION_ENABLE_LEGACY_LABEL_PIPELINES:-false}" in block

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


def test_compose_does_not_include_decommissioned_flow_labeler_service() -> None:
    compose_text = Path("docker-compose.yml").read_text()
    assert "\n  labeler:\n" not in compose_text
    assert "ORION_ENABLE_LEGACY_FLOW_LABELER" not in compose_text


def test_heber_data_root_is_mounted_for_heber_consumers() -> None:
    compose_text = Path("docker-compose.yml").read_text()

    for service_name in ("execution", "feature_enrichment", "eod-agent"):
        block = _service_block(compose_text, service_name)
        assert "- /Volumes/heber/data:/Volumes/heber/data:ro" in block


def test_mcp_server_wires_sec_contact_email() -> None:
    compose_text = Path("docker-compose.yml").read_text()
    block = _service_block(compose_text, "mcp-server")
    assert "- SEC_CONTACT_EMAIL=${SEC_CONTACT_EMAIL:-alerts@empire.local}" in block


def test_mcp_server_wires_explicit_auth_env_contract() -> None:
    compose_text = Path("docker-compose.yml").read_text()
    block = _service_block(compose_text, "mcp-server")
    assert "- MCP_API_KEY=${MCP_API_KEY:-}" in block
    assert "- MCP_API_KEY_HEADER=${MCP_API_KEY_HEADER:-X-MCP-API-Key}" in block
    assert "- MCP_AUTH_REQUIRED=${MCP_AUTH_REQUIRED:-false}" in block


def test_compose_drops_obsolete_top_level_version_key() -> None:
    compose_text = Path("docker-compose.yml").read_text()
    assert not compose_text.lstrip().startswith("version:")


def test_mcp_server_healthcheck_uses_liveness_endpoint() -> None:
    compose_text = Path("docker-compose.yml").read_text()
    block = _service_block(compose_text, "mcp-server")
    assert "healthcheck:" in block
    assert 'test: [ "CMD", "curl", "-f", "http://localhost:8001/health/live" ]' in block
    assert '"http://localhost:8001/health" ]' not in block


def test_long_running_worker_services_override_http_healthcheck() -> None:
    compose_text = Path("docker-compose.yml").read_text()

    for service_name in (
        "feature_enrichment",
        "execution",
        "position-monitor",
        "eod-agent",
        "indexer",
    ):
        block = _service_block(compose_text, service_name)
        assert "healthcheck:" in block
        assert 'test: [ "CMD-SHELL", "kill -0 1" ]' in block


def test_worker_healthcheck_blocks_do_not_probe_localhost_8000() -> None:
    compose_text = Path("docker-compose.yml").read_text()

    for service_name in (
        "feature_enrichment",
        "execution",
        "position-monitor",
        "eod-agent",
        "indexer",
    ):
        block = _service_block(compose_text, service_name)
        assert "localhost:8000/health" not in block


def test_default_stack_includes_ingestion_service_with_heber_mount() -> None:
    compose_text = Path("docker-compose.yml").read_text()
    block = _service_block(compose_text, "ingestion")

    assert "restart: unless-stopped" in block
    assert 'command: [ "python", "-m", "orion.ingestion" ]' in block
    assert "- /Volumes/heber/data:/Volumes/heber/data:ro" in block
    assert "profiles:" not in block
