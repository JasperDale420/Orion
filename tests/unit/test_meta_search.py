from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.agents.meta_search_agent import MetaSearchAgent
from orion.core.solver_schema import EditOp, EditOpType, SolverConfig, SolverEdit
from orion.storage.models_solvers import SolverMetrics, SolverRun


# Test Data
def get_base_config():
    return SolverConfig(version_id="base_v1")


@pytest.mark.asyncio
async def test_meta_search_cycle():
    """
    Verifies that run_evolution_cycle loads base solver, generates variants, and saves experiment.
    """

    # 1. Mock DB
    mock_session = AsyncMock()
    mock_base_solver = MagicMock()
    mock_base_solver.solver_id = "base_v1"
    mock_base_solver.family_name = "BaseStrat"
    mock_base_solver.config = get_base_config().model_dump(mode="json")
    mock_base_solver.sharpe_ratio = 0.0

    # Setup Select Results: base solver, then no base metrics
    mock_result_base = MagicMock()
    mock_result_base.scalars.return_value.first.return_value = mock_base_solver

    mock_result_metrics = MagicMock()
    mock_result_metrics.scalars.return_value.first.return_value = None

    mock_session.execute.side_effect = [mock_result_base, mock_result_metrics]

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__.return_value = mock_session
    mock_factory.return_value.__aexit__.return_value = None

    # 2. Patch DB and Backtest dependencies
    with patch("orion.agents.meta_search_agent.async_session_factory", side_effect=mock_factory):
        agent = MetaSearchAgent()

        edit = SolverEdit(
            base_solver_id="base_v1",
            new_solver_id="variant_v1",
            generated_by="test",
            ops=[
                EditOp(
                    op=EditOpType.MODIFY_RISK,
                    param_name="risk_per_trade_bps",
                    old_value=100,
                    new_value=50,
                    reasoning="Reduce risk",
                )
            ],
        )

        agent.meta_agent.propose_edits = AsyncMock(return_value=[edit])

        def _apply_edit(base: SolverConfig, edit_obj: SolverEdit) -> SolverConfig:
            return base.model_copy(update={"version_id": edit_obj.new_solver_id})

        agent.apply_edit = _apply_edit  # type: ignore[assignment]
        agent.evaluate_variant = AsyncMock(
            return_value=(
                SolverRun(id="run_1", solver_id="variant_v1", dataset_tag="validation"),
                SolverMetrics(sharpe_ratio=1.0, profit_factor=1.2, max_dd_pct=1.0, info_ratio=1.0, stability_score=0.8),
            )
        )

        # 3. Run
        await agent.run_evolution_cycle("base_v1")

        # 4. Verify
        assert mock_session.add.call_count >= 1
        assert mock_session.commit.called
