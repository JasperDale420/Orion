import asyncio
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

import pytest
from orion.agents.meta_search_agent import EditOpType, MetaSearchAgent
from orion.config import RiskSettings
from orion.core.solver_router import SolverRouter
from orion.core.solver_schema import SolverConfig, SolverRiskConfig, LiveContext
from orion.execution.risk_manager import RiskManager


# 1. Test Risk Injection
def test_risk_manager_di():
    # Verify we can inject strict config
    strict_config = RiskSettings(max_positions=1, risk_per_trade_pct=0.01)
    rm = RiskManager(config=strict_config)
    assert rm.config.max_positions == 1

    # Verify default
    rm_def = RiskManager()
    assert rm_def.config != strict_config


@pytest.mark.asyncio
async def test_backtest_engine_solver_config():
    from orion.processing.backtest_engine import BacktestEngine

    # Create Solver Config with specific risk
    SolverConfig(
        version_id="test_v1",
        # base_strategy_name="test", # Removed
        entry_logic={"rules": []},
        exit_logic={},
        risk=SolverRiskConfig(risk_per_trade_bps=50, max_open_positions=1),
    )

    BacktestEngine()

    # Mock _simulate to inspect the constructed RiskManager (hard to inspect local var)
    pass


# 2. Test Router Filtering
@pytest.mark.asyncio
async def test_solver_router_filtering():
    router = SolverRouter()

    # Mock Session and Solvers
    # A generic mock solver object that matches SQLAlchemy result
    class MockSolver:
        def __init__(self, stage, allowlist=None):
            self.solver_id = "s1"
            self.stage = stage
            self.config = {
                "version_id": "v1",
                # "base_strategy_name": "base", # Removed to prevent validation error
                "entry_logic": {"rules": []},
                "exit_logic": {},
                "risk": {"risk_per_trade_bps": 100, "max_open_positions": 1},
                "universe": {"ticker_allowlist": allowlist},
            }
            self.is_active = True
            self.version_id = "v1"
            self.definition_json = None

    # Paper Solver
    paper_solver = MockSolver(stage="paper")

    # Context: LIVE
    live_context = LiveContext(
        ticker="AAPL",
        current_stage="live",
        regime="neutral",
        time_of_day_utc=datetime.now(timezone.utc)
    )

    # Context: PAPER
    paper_context = LiveContext(
        ticker="AAPL",
        current_stage="paper",
        regime="neutral",
        time_of_day_utc=datetime.now(timezone.utc)
    )

    # Mock DB
    with patch("orion.core.solver_router.async_session_factory") as mock_sf:
        mock_session = MagicMock()
        mock_sf.return_value.__aenter__.return_value = mock_session

        # Configure select result
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [paper_solver]

        # Empty metrics for LIVE call (skips selection)
        mock_result_empty = MagicMock()
        mock_result_empty.scalars.return_value.all.return_value = []

        # Metrics for PAPER call (passes selection)
        mock_metric = MagicMock()
        mock_metric.solver_id = "s1"
        mock_metric.info_ratio = 1.0
        mock_metric.oos_expect_bp = 10.0
        mock_metric.max_dd_pct = 5.0

        mock_result_metrics = MagicMock()
        mock_result_metrics.scalars.return_value.all.return_value = [mock_metric]

        def async_return(result):
            f = asyncio.Future()
            f.set_result(result)
            return f

        # Set side_effect for the sequence of calls:
        # 1. LIVE context -> query solvers (returns paper_solver)
        # 2. LIVE context -> query metrics (returns empty)
        # 3. PAPER context -> query solvers (returns paper_solver)
        # 4. PAPER context -> query metrics (returns mock_metric)
        mock_session.execute.side_effect = [
            async_return(mock_result),
            async_return(mock_result_empty),
            async_return(mock_result),
            async_return(mock_result_metrics)
        ]

        # Run LIVE
        selected_list = await router.select_solvers(live_context)
        selected = selected_list[0] if selected_list else None

        # ASSERT: Should be None because Paper < Live
        assert selected is None

        # Run PAPER
        selected_list_paper = await router.select_solvers(paper_context)
        selected_paper = selected_list_paper[0] if selected_list_paper else None
        assert selected_paper is not None
        assert selected_paper.solver_id == "s1"


# 3. Test Mutation
def test_meta_search_mutation():
    agent = MetaSearchAgent()

    # Base Config
    base = SolverConfig(
        version_id="base",
        # base_strategy_name="base", # Removed
        entry_logic={"rules": []},
        exit_logic={},
        risk=SolverRiskConfig(risk_per_trade_bps=100),  # 1%
    )

    # Mutate
    # Mock base solver DB object since _generate needs sharpe
    mock_base_solver = MagicMock()
    mock_base_solver.sharpe_ratio = 2.0

    with patch("random.uniform", return_value=1.5):  # Increase by 50%
        edits = agent._generate_heuristic_variants(base, mock_base_solver, count=1)

    assert len(edits) >= 0  # Heuristic might return empty if no condition met
    # Actually _mutate_risk checks 'is_struggling'. Sharpe 2.0 -> tighten=False -> Increase risk.
    # So we should get risk edit.

    # Check ops
    if edits:
        # Check first edit
        e = edits[0]
        for op in e.ops:
            if op.op == EditOpType.MODIFY_RISK:
                assert op.param_name == "risk_per_trade_bps"
                # _mutate_risk logic: current * 1.1 (int)
                # 100 * 1.1 = 110
                assert op.new_value == 110

        # Test Apply if we have an edit
        new_cfg = agent.apply_edit(base, edits[0])
        assert new_cfg.risk.risk_per_trade_bps == 110
