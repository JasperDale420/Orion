from pathlib import Path
import re


def test_compose_wires_per_service_legacy_gate_env_vars() -> None:
    compose_text = Path("docker-compose.yml").read_text()

    assert "- ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER=${ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER:-true}" in compose_text
    assert "- ORION_ENABLE_LEGACY_FLOW_LABELER=${ORION_ENABLE_LEGACY_FLOW_LABELER:-true}" in compose_text
    assert "- ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER=${ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER:-true}" in compose_text


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
