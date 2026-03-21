import os
import shutil
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from orion.agents.meta_search_agent import MetaSearchAgent
from orion.core.solver_schema import SolverConfig
from orion.storage.models_solvers import Solver, SolverEdits, SolverMetrics, SolverRun


@pytest.mark.asyncio
async def test_ingest_proposals():
    """
    Tests that YAML proposals are correctly ingested into SolverEdits table.
    """
    agent = MetaSearchAgent()

    # Setup temp proposals dir
    test_dir = "tests_proposals_tmp"
    os.makedirs(test_dir, exist_ok=True)

    solver_id = "test_solver_v1"

    # Create valid proposal
    proposal_data = {
        "meta": {"run_id": "test_run", "status": "PROPOSED"},
        "proposal": {
            "type": "solver_edit",
            "target_solver_id": solver_id,
            "ops": [
                {
                    "op": "modify_param",
                    "param_name": "risk_per_trade_bps",
                    "new_value": 50,
                    "old_value": 100,
                    "reasoning": "Reduce risk",
                }
            ],
        },
    }

    with open(os.path.join(test_dir, "test_prop.yaml"), "w") as f:
        yaml.dump(proposal_data, f)

    # Mock DB Session
    mock_session = AsyncMock()
    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__.return_value = mock_session
    mock_factory.return_value.__aexit__.return_value = None

    try:
        with patch("orion.agents.meta_search_agent.async_session_factory", side_effect=mock_factory):
            # Run Ingestion
            await agent.ingest_proposals(test_dir)

            # Verify DB Add
            assert mock_session.add.call_count == 1
            args, _ = mock_session.add.call_args
            edit_obj = args[0]
            assert isinstance(edit_obj, SolverEdits)
            assert edit_obj.base_solver_id == solver_id
            assert edit_obj.generated_by == "llm_eod_agent"
            assert edit_obj.reward is None

            # Verify file moved
            assert not os.path.exists(os.path.join(test_dir, "test_prop.yaml"))
            assert os.path.exists(os.path.join(test_dir, "processed", "test_prop.yaml"))

    finally:
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)


@pytest.mark.asyncio
async def test_process_pending_edits():
    """
    Tests that pending edits are picked up, applied, and evaluated.
    """
    agent = MetaSearchAgent()

    # Mock evaluate_variant to avoid complex backtest dependencies
    mock_run = SolverRun(id="run_1", solver_id="new_v2", dataset_tag="validation")
    mock_metrics = SolverMetrics(
        sharpe_ratio=1.5, profit_factor=1.2, max_dd_pct=1.0, info_ratio=1.5, stability_score=0.8
    )
    agent.evaluate_variant = AsyncMock(return_value=(mock_run, mock_metrics))

    # Setup Data objects
    base_id = "base_v1"
    config = SolverConfig(version_id=base_id)

    mock_base_solver = MagicMock()
    mock_base_solver.solver_id = base_id
    mock_base_solver.family_name = "test_fam"
    mock_base_solver.config = config.model_dump(mode="json")
    mock_base_solver.sharpe_ratio = 0.0

    edit_id = str(uuid.uuid4())
    mock_edit = SolverEdits(
        id=edit_id,
        base_solver_id=base_id,
        new_solver_id="new_v2",
        edit_json={
            "ops": [
                {
                    "op": "modify_risk",
                    "param_name": "risk_per_trade_bps",
                    "new_value": 200,
                    "old_value": 100,
                    "reasoning": "More risk",
                }
            ]
        },
        generated_by="test_agent",
        reward=None,
    )

    # Mock DB Session
    mock_session = AsyncMock()

    # Mock Results for Selects
    # 1. Select Pending Edits -> [mock_edit]
    # 2. Select Base Solver -> [mock_base_solver]
    # We can use side_effect on scalars().all() or first()

    # It's tricky to mock consecutive execute() calls differently.
    # Pattern: mock_session.execute returns an object whose scalars().all() returns X
    # We can set side_effect on mock_session.execute

    mock_result_edits = MagicMock()
    mock_result_edits.scalars.return_value.all.return_value = [mock_edit]

    mock_result_base = MagicMock()
    mock_result_base.scalars.return_value.first.return_value = mock_base_solver

    mock_result_base_metrics = MagicMock()
    mock_result_base_metrics.scalars.return_value.first.return_value = None

    mock_session.execute.side_effect = [
        mock_result_edits,
        mock_result_base,
        mock_result_base_metrics,
        MagicMock(),
    ]  # Additional calls if any

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__.return_value = mock_session
    mock_factory.return_value.__aexit__.return_value = None

    with patch("orion.agents.meta_search_agent.async_session_factory", side_effect=mock_factory):
        # Run Processing
        await agent.process_pending_edits()

        # Verify
        # 1. New Solver Added
        # 2. Metrics Added
        # 3. Edit Reward Updated

        # Check add calls
        # We expect: session.add(new_solver), session.add(solver_run), session.add(metrics)
        assert mock_session.add.call_count >= 3

        # Verify Reward Update (In-memory object mutation)
        assert mock_edit.reward is not None
        assert mock_edit.reward > 0.0

        # Verify New Solver Config
        # We can inspect args to session.add
        added_solvers = [args[0] for args, _ in mock_session.add.call_args_list if isinstance(args[0], Solver)]
        assert len(added_solvers) == 1
        new_s = added_solvers[0]
        assert new_s.family_name.startswith("test_fam_eod_")
        # Check config change
        # Logic: 100 -> 200
        assert new_s.config["risk"]["risk_per_trade_bps"] == 200
