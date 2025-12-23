import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# MetaAgent currently has an optional dependency on litellm; tests should not require it.
sys.modules.setdefault("litellm", MagicMock())

from orion.agents.meta_search_agent import MetaSearchAgent
from orion.storage.models_solvers import PromotionRecommendation, Solver, SolverEdits, SolverMetrics

# --- Test Ingestion ---


@pytest.mark.asyncio
async def test_ingest_proposals_creates_db_records(tmp_path):
    # Setup temp dir
    proposals_dir = tmp_path / "proposals"
    proposals_dir.mkdir()

    # Create valid YAML
    yaml_content = {
        "meta": {"status": "PROPOSED"},
        "proposal": {
            "type": "solver_edit",
            "target_solver_id": "base_v1",
            "ops": [{"op": "modify_param", "param_name": "x", "new_value": 1, "reasoning": "test"}],
        },
    }

    file_path = proposals_dir / "proposal_1.yaml"
    with open(file_path, "w") as f:
        yaml.dump(yaml_content, f)

    # Mock Session
    mock_session = AsyncMock()
    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__.return_value = mock_session
    mock_factory.return_value.__aexit__.return_value = None

    with patch("orion.agents.meta_search_agent.async_session_factory", side_effect=mock_factory):
        agent = MetaSearchAgent()

        # Override dir path in logic? OR pass it if arg exists.
        # ingest_proposals(proposals_dir)
        await agent.ingest_proposals(str(proposals_dir))

        # Verify Session Add (SolverEdits)
        assert mock_session.add.called
        args, _ = mock_session.add.call_args
        obj = args[0]
        assert isinstance(obj, SolverEdits)
        assert obj.base_solver_id == "base_v1"
        assert obj.edit_json["ops"][0]["op"] == "modify_param"

        # Verify File Moved (Mocking os.rename might be safer, but we used real fs for temp)
        # But method uses os.rename. os.rename works on tmp_path objects too.
        # But we need to ensure method looks in the RIGHT dir.
        # We passed 'proposals_dir' string.
        # Processed dir check
        processed_path = proposals_dir / "processed" / "proposal_1.yaml"
        assert processed_path.exists()


# --- Test Promotion Logic ---


@pytest.mark.asyncio
async def test_scan_for_promotions_upgrades_solver():
    """
    Verifies that a solver meeting criteria generates a PromotionRecommendation (no auto stage mutation).
    """
    # Mock Solver (Shadow)
    solver = MagicMock(spec=Solver)
    solver.solver_id = "solver_shadow"
    solver.stage = "shadow"
    solver.is_active = False  # Often inactive in shadow until manual override or auto-promote?
    # Actually auto-promote can set active.

    # Mock Metrics (Great Performance)
    metrics = MagicMock(spec=SolverMetrics)
    metrics.num_trades = 60  # > 50 (Shadow->Paper req)
    metrics.profit_factor = 1.5  # > - (Shadow->Paper req?)
    metrics.max_dd_pct = 0.5
    metrics.sharpe_ratio = 2.0
    metrics.metrics_json = {}

    # Mock DB
    mock_session = AsyncMock()

    # Result for Solvers
    mock_solvers_res = MagicMock()
    mock_solvers_res.scalars.return_value.all.return_value = [solver]

    # Result for Metrics
    mock_metrics_res = MagicMock()
    mock_metrics_res.scalars.return_value.first.return_value = metrics

    # Result for existing recommendation lookup
    mock_rec_res = MagicMock()
    mock_rec_res.scalars.return_value.first.return_value = None

    mock_session.execute.side_effect = [mock_solvers_res, mock_metrics_res, mock_rec_res]

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__.return_value = mock_session
    mock_factory.return_value.__aexit__.return_value = None

    # IMPORTANT: We need promotion_rules to return 'promote'
    # We can rely on real logic OR patch it.
    # Real logic is safer for integration test.
    # Shadow->Paper default: min_days=10, min_trades=50.
    # We mocked trades=60. profit_factor=1.5.
    # Should work.

    with patch("orion.agents.meta_search_agent.async_session_factory", side_effect=mock_factory):
        agent = MetaSearchAgent()
        await agent.scan_for_promotions()

        # Verify no direct stage mutation (PRDv2 FR 5.5.2)
        assert solver.stage == "shadow"
        assert solver.is_active is False

        # Verify a PromotionRecommendation was added
        added = [call.args[0] for call in mock_session.add.call_args_list if call.args]
        assert any(isinstance(x, PromotionRecommendation) for x in added)

        assert mock_session.commit.called


@pytest.mark.asyncio
async def test_scan_for_promotions_demotes_solver():
    """
    Verifies that a failing solver is disabled and a demotion recommendation is created (no auto stage mutation).
    """
    # Mock Solver (Paper)
    solver = MagicMock(spec=Solver)
    solver.solver_id = "solver_paper_fail"
    solver.stage = "paper"
    solver.is_active = True

    # Mock Metrics (Terrible Drawdown)
    metrics = MagicMock(spec=SolverMetrics)
    metrics.max_dd_pct = 5.0  # > 3.5 (Demotion limit)
    metrics.num_trades = 100
    metrics.profit_factor = 0.8
    metrics.sharpe_ratio = 0.1
    metrics.metrics_json = {}

    # Mock DB
    mock_session = AsyncMock()
    mock_solvers_res = MagicMock()
    mock_solvers_res.scalars.return_value.all.return_value = [solver]
    mock_metrics_res = MagicMock()
    mock_metrics_res.scalars.return_value.first.return_value = metrics
    mock_rec_res = MagicMock()
    mock_rec_res.scalars.return_value.first.return_value = None
    mock_session.execute.side_effect = [mock_solvers_res, mock_metrics_res, mock_rec_res]

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__.return_value = mock_session
    mock_factory.return_value.__aexit__.return_value = None

    with patch("orion.agents.meta_search_agent.async_session_factory", side_effect=mock_factory):
        agent = MetaSearchAgent()
        await agent.scan_for_promotions()

        # Safety: stop trading immediately, but do not mutate stage automatically
        assert solver.stage == "paper"
        assert solver.is_active is False

        added = [call.args[0] for call in mock_session.add.call_args_list if call.args]
        assert any(isinstance(x, PromotionRecommendation) for x in added)

        assert mock_session.commit.called
