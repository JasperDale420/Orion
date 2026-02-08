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


def test_agent_settings_env_mapping():
    """Verify OPENAI_API_KEY maps to agent_settings.openai_api_key."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-123"}):
        from orion.config import AgentSettings

        s = AgentSettings()
        assert s.openai_api_key == "sk-test-123"


def test_gateway_settings_env_mapping_primary_names():
    """Verify DATA_GATEWAY_* env vars map into centralized system settings."""
    with patch.dict(
        os.environ,
        {
            "DATA_GATEWAY_URL": "http://gateway.internal:8080",
            "DATA_GATEWAY_API_KEY": "gw-key-123",
            "ORION_USE_GATEWAY": "false",
        },
        clear=True,
    ):
        from orion.config import SystemSettings

        s = SystemSettings()
        assert s.data_gateway_url == "http://gateway.internal:8080"
        assert s.data_gateway_api_key == "gw-key-123"
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


def test_legacy_label_gate_settings_env_mapping():
    """Verify centralized legacy gate env vars map into typed system settings."""
    with patch.dict(
        os.environ,
        {
            "ORION_ENABLE_LEGACY_LABEL_PIPELINES": "false",
            "ORION_ENABLE_LEGACY_FLOW_LABELER": "true",
            "ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER": "false",
            "ORION_ENABLE_LEGACY_PRICE_TARGET_LABELER": "true",
        },
        clear=True,
    ):
        from orion.config import SystemSettings

        s = SystemSettings()
        assert s.legacy_label_pipelines_enabled is False
        assert s.legacy_flow_labeler_enabled is True
        assert s.legacy_option_quote_tracker_enabled is False
        assert s.legacy_price_target_labeler_enabled is True


@pytest.mark.asyncio
async def test_eod_review_uses_config():
    """Verify EODReviewAgent uses configured paths."""

    # Mock settings import
    mock_settings = MagicMock()
    mock_settings.artifacts_dir = "/tmp/mock_artifacts"
    mock_settings.openai_api_key = "sk-mock"
    mock_settings.model_name = "gpt-mock"

    with patch.dict(
        "sys.modules", {"orion.config": MagicMock(system_settings=mock_settings, agent_settings=mock_settings)}
    ):
        from orion.agents.eod_review_agent import EODReviewAgent

        # Mock run_codex_completion
        with patch("orion.agents.eod_review_agent.run_codex_completion", new_callable=AsyncMock) as mock_codex:
            mock_codex.return_value = '{"analysis": "foo"}'

            agent = EODReviewAgent()

            # Use AsyncMock for async methods
            agent._gather_data = AsyncMock(return_value=({}, ""))
            agent._fetch_rag_context = AsyncMock(return_value="")
            # _generate_analysis returns dict
            agent._generate_analysis = AsyncMock(return_value={"analysis": "foo"})
            agent.proposal_builder = MagicMock()

            # We mock os.makedirs and open to check paths
            with patch("os.makedirs") as mock_makedirs, patch("builtins.open", new_callable=MagicMock):
                await agent.run_review(target_date=None)

                # Verify makedirs called with correct path
                expected_dir = "/tmp/mock_artifacts/reports"
                mock_makedirs.assert_called_with(expected_dir, exist_ok=True)
