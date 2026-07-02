import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ...
# Set env vars BEFORE importing config validation logic generally
# But for pydantic settings, we can patch os.environ or pass _env_file
# Simple way: Patch os.environ dict


def test_system_settings_env_mapping():
    """Verify ORION_STAGE maps to system_settings.orion_stage."""
    with patch.dict(os.environ, {"ORION_STAGE": "live", "ORION_ARTIFACTS_DIR": "/tmp/artifacts"}):
        from orion.config import SystemSettings

        s = SystemSettings()
        assert s.orion_stage == "live"
        assert s.artifacts_dir == "/tmp/artifacts"


def test_system_settings_api_key_env_mapping():
    """Verify ORION_API_KEY maps to system_settings.api_key."""
    fake_orion_api_key = "sk-orion-123"  # pragma: allowlist secret
    with patch.dict(os.environ, {"ORION_API_KEY": fake_orion_api_key}, clear=True):
        from orion.config import SystemSettings

        s = SystemSettings()
        assert s.api_key == fake_orion_api_key


def test_gateway_settings_env_mapping_primary_names():
    """Verify DATA_GATEWAY_* env vars map into centralized system settings."""
    fake_gateway_api_key = "gw-key-123"  # pragma: allowlist secret
    with patch.dict(
        os.environ,
        {
            "DATA_GATEWAY_URL": "http://gateway.internal:8080",
            "DATA_GATEWAY_API_KEY": fake_gateway_api_key,
            "ORION_USE_GATEWAY": "false",
        },
        clear=True,
    ):
        from orion.config import SystemSettings

        s = SystemSettings()
        assert s.data_gateway_url == "http://gateway.internal:8080"
        assert s.data_gateway_api_key == fake_gateway_api_key
        assert s.orion_use_gateway is False


def test_gateway_settings_env_mapping_legacy_aliases():
    """Verify legacy GATEWAY_* env vars are still accepted."""
    with patch.dict(
        os.environ,
        {
            "GATEWAY_URL": "http://legacy-gateway:8080",
            "GATEWAY_API_KEY": "legacy-key",
        },
        clear=True,
    ):
        from orion.config import SystemSettings

        s = SystemSettings()
        assert s.data_gateway_url == "http://legacy-gateway:8080"
        assert s.data_gateway_api_key == "legacy-key"


def test_heber_settings_env_mapping():
    """Verify Heber env vars map into centralized system settings."""
    with patch.dict(
        os.environ,
        {
            "HEBER_CATALOG_URL": "http://heber-catalog:8085/api/v1",
            "HEBER_DATA_ROOT": "/tmp/heber-data",
        },
        clear=True,
    ):
        from orion.config import SystemSettings

        s = SystemSettings()
        assert s.heber_catalog_url == "http://heber-catalog:8085/api/v1"
        assert str(s.heber_data_root) == "/tmp/heber-data"


def test_legacy_label_gate_fields_removed():
    """Verify legacy gate fields have been removed from SystemSettings."""
    with patch.dict(os.environ, {}, clear=True):
        from orion.config import SystemSettings

        s = SystemSettings()
        assert not hasattr(s, "legacy_label_pipelines_enabled")
        assert not hasattr(s, "legacy_option_quote_tracker_enabled")
        assert not hasattr(s, "legacy_pattern_miner_enabled")
        assert not hasattr(s, "pattern_miner_training_source")
        assert not hasattr(s, "exit_classifier_training_source")


def test_system_settings_no_longer_exposes_decommissioned_flow_labeler_gate() -> None:
    with patch.dict(os.environ, {}, clear=True):
        from orion.config import SystemSettings

        s = SystemSettings()
        assert not hasattr(s, "legacy_flow_labeler_enabled")
