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

        # We need to mock AsyncOpenAI to prevent network calls
        with patch("orion.agents.eod_review_agent.AsyncOpenAI") as mock_openai:
            agent = EODReviewAgent()

            # Check API Key usage
            mock_openai.assert_called_with(api_key="sk-mock")

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
