import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# We need to import module to patch it properly
import orion.execution.execution_engine
import orion.main_execution
import orion.storage.db
from orion.storage.db import Base
from orion.storage.models import SystemStatus
from orion.storage.models_gold import CandidateTrade, StrategyDecision


@pytest.mark.asyncio
async def test_execution_loop_flow():
    print("--- STARTING TEST ---")

    # 1. Setup In-Memory DB
    from sqlalchemy.pool import StaticPool

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    # Patch Global DB engine (Source of Truth)
    orion.storage.db.engine = test_engine
    orion.storage.db.async_session_factory = test_session_factory

    # RELOAD MODULES to pick up the patched factory
    import importlib

    importlib.reload(orion.execution.execution_engine)
    importlib.reload(orion.main_execution)

    # Re-import classes from reloaded modules
    from orion.execution.execution_engine import ExecutionEngine
    from orion.processing.signal_engine import SignalEngine

    # Create Tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("--- DB SETUP COMPLETE ---")

    # 2. Seed Data
    candidate_id = f"test_cand_{uuid.uuid4()}"
    candidate = CandidateTrade(
        candidate_id=candidate_id,
        ticker="SPY",
        timestamp_utc=datetime.now(UTC),
        rule_id="test_rule",
        direction="LONG",
        confidence=0.9,
        source="TEST",
        execution_params={"limit_price": 100.0},
        evidence={"test": True},
    )

    # SYSTEM STATUS
    status = SystemStatus(key="global_health", status="HEALTHY", last_updated_utc=datetime.now(UTC), details="Test")

    async with test_session_factory() as session:
        session.add(candidate)
        session.add(status)

        # 2.1 Seed Active Solver for SignalEngine to find
        from orion.storage.models_solvers import Solver

        # Patch system_settings.orion_stage to ensure router compatibility
        with patch("orion.config.system_settings.orion_stage", "paper"):
            solver = Solver(
                solver_id="V1_TEST_SOLVER",
                family_name="mean_reversion",
                is_active=True,
                stage="paper",
                config={
                    "version_id": "V1_TEST_SOLVER",
                    "solver_id": "V1_TEST_SOLVER",
                    "family_name": "mean_reversion",
                    "universe": {"ticker_allowlist": None, "ticker_blocklist": [], "required_regime": None},
                    "entry_logic": {"id": "entry_1", "description": "test", "type": "composite", "conditions": []},
                    "exit_logic": {
                        "id": "exit_1",
                        "description": "test",
                        "type": "fixed_risk_reward",
                        "fixed_sl_pct": 0.02,
                        "fixed_tp_pct": 0.04,
                    },
                    "risk": {"risk_per_trade_bps": 50, "max_open_trades": 5},
                    "features": {
                        "feature_set_id": "v1_basic",
                        "window_features": ["volatility_10d"],
                        "event_features": [],
                    },
                },
                sharpe_ratio=2.0,
                info_ratio=1.5,
                profit_factor=2.0,
                stability_score=0.9,
                max_dd_pct=10.0,
                oos_expect_bp=10.0,
            )
            session.add(solver)

            # 2.2 Seed SolverMetrics (Required by Router)
            from orion.core.feature_registry import FeatureRegistry
            from orion.storage.models_solvers import SolverMetrics

            # Allow any feature set ID for test
            with patch.object(FeatureRegistry, "validate_id", return_value=True):
                metrics = SolverMetrics(
                    id=str(uuid.uuid4()),
                    solver_id="V1_TEST_SOLVER",
                    dataset_tag="test",
                    sharpe_ratio=2.0,
                    info_ratio=1.5,
                    profit_factor=2.0,
                    stability_score=0.9,
                    max_dd_pct=10.0,
                    oos_expect_bp=10.0,
                    evaluated_at_utc=datetime.now(UTC),
                )
                session.add(metrics)
                await session.commit()

            # DEBUG DB CONTENT
            async with test_session_factory() as session:
                (await session.execute(select(CandidateTrade))).scalars().all()

                metrics = (await session.execute(select(SolverMetrics))).scalars().all()

            # 3. Test Fetch
            pending = await orion.main_execution.fetch_pending_candidates()
            assert len(pending) == 1
            assert pending[0].candidate_id == candidate_id

            # 4. Policy Decision (Now SignalEngine)
            target_candidate = pending[0]
            # Need to mock stage within SignalEngine too if it reads it there
            with (
                patch("orion.config.system_settings.orion_stage", "paper"),
                patch("orion.core.feature_registry.FeatureRegistry.validate_id", return_value=True),
                patch("orion.core.solver_router.async_session_factory", test_session_factory),
            ):
                signal_engine = SignalEngine()

                # Mock FeatureEngine to prevent real DB calls/computation failures
                signal_engine.feature_engine = AsyncMock()
                signal_engine.feature_engine.compute.return_value = {"session_volatility": 0.01}
                signal_engine.feature_engine.hydrate_history.return_value = None

                decision = await signal_engine.decide(target_candidate)

            assert decision.decision == "EXECUTE"
            assert decision.strategy_version_id == "V1_TEST_SOLVER"

    # DEBUG DB CONTENT
    # 5. Save Decision
    await orion.main_execution.save_decision(decision, target_candidate)

    async with test_session_factory() as session:
        res = await session.execute(select(StrategyDecision))
        rows = res.scalars().all()
        assert len(rows) == 1
        assert rows[0].executed_successfully == "PENDING"

    print("--- DECISION SAVED ---")

    # Verify SystemStatus is visible
    async with test_session_factory() as session:
        stat = (await session.execute(select(SystemStatus))).scalars().first()
        if stat:
            print(f"--- SYSTEM STATUS DEBUG: {stat.key} {stat.status} {stat.last_updated_utc} ---")
        else:
            print("--- SYSTEM STATUS DEBUG: NONE FOUND ---")

    # 6. Execution Engine (Mocked)
    # Note: ExecutionEngine class typically imports connectors at top level.
    # We need to patch them.
    with (
        patch("orion.execution.execution_engine.AlpacaTradingConnector") as MockConnector,  # noqa: N806
        patch("orion.execution.execution_engine.AlpacaMarketConnector") as MockMarket,  # noqa: N806
    ):
        mock_conn = MockConnector.return_value
        mock_market = MockMarket.return_value
        mock_market.get_latest_price.return_value = 100.0

        execution = ExecutionEngine()
        execution.connector = mock_conn
        execution.market_connector = mock_market

        # Bypass Risk checks for test
        execution.risk_manager.check_order = MagicMock(return_value=True)
        execution.risk_manager.calculate_size = MagicMock(return_value=10)

        # Execute
        print("--- CALLING EXECUTE ---")
        await execution.execute_order(decision, target_candidate)
        print("--- EXECUTE CALLED ---")

        mock_conn.submit_limit_order.assert_called_once()
        print("--- ORDER SUBMITTED ---")

    # 7. Update Status
    await orion.main_execution.update_decision_status(decision.decision_id, "TRUE")

    async with test_session_factory() as session:
        stmt = select(StrategyDecision).where(StrategyDecision.decision_id == decision.decision_id)
        final = (await session.execute(stmt)).scalars().first()
        assert final.executed_successfully == "TRUE"

    print("--- STATUS UPDATED ---")
    await test_engine.dispose()
