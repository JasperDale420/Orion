import json
from unittest.mock import AsyncMock, patch

import pytest

from orion.agents.meta_agent import MetaAgent
from orion.core.solver_schema import SolverConfig


@pytest.fixture
def mock_solver_config():
    # Minimal config for testing
    return SolverConfig(
        version_id="test_v1",
        base_strategy_name="momentum_v1",
        family_name="momentum_test",
        rules=["rule_1"],
        features={"window_features": []},
        entry_logic={"signal_threshold": 0.5},
        exit_logic={"take_profit_atr_multiple": 2.0},
        risk={"risk_per_trade_bps": 100},
    )


@pytest.mark.asyncio
async def test_propose_edits(mock_solver_config):
    # Mock codex CLI response
    mock_response = json.dumps(
        {
            "variants": [
                {
                    "ops": [
                        {
                            "op": "modify_param",
                            "param_name": "exit_logic.take_profit_atr_multiple",
                            "new_value": 2.5,
                            "reasoning": "Increase profit target",
                        }
                    ],
                    "rationale": "More aggressive",
                }
            ]
        }
    )

    with (
        patch(
            "orion.agents.meta_agent.run_codex_completion",
            new_callable=AsyncMock,
        ) as mock_codex,
        patch("orion.config.agent_settings.model_name", "glm-5.1"),
    ):
        mock_codex.return_value = mock_response

        agent = MetaAgent()
        edits = await agent.propose_edits(mock_solver_config)

        # Verify codex was called
        mock_codex.assert_called_once()
        call_kwargs = mock_codex.call_args.kwargs
        assert call_kwargs["model"] == "glm-5.1"

        # Verify output
        assert len(edits) == 1
        assert edits[0].ops[0].param_name == "exit_logic.take_profit_atr_multiple"


@pytest.mark.asyncio
async def test_run_is_not_generic_entry_point() -> None:
    agent = MetaAgent()

    with pytest.raises(NotImplementedError, match="propose_edits"):
        await agent.run({})
