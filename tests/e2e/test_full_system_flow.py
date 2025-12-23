from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from orion.core.solver_schema import SolverConfig
from orion.execution.execution_engine import ExecutionEngine
from orion.execution.risk_manager import RiskManager
from orion.processing.feature_engine import FeatureEngine
from orion.processing.ingest_pipeline import ingest_bronze_events
from orion.processing.persistence import persist_bronze_events, persist_silver_from_bronze
from orion.processing.rule_engine import RuleEngine
from orion.processing.signal_engine import SignalEngine

# Import Core Logic
from orion.storage.models import BronzeEvent
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_solvers import Solver, SolverMetrics
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.mark.asyncio
async def test_full_system_flow():
    """
    Grand Unified E2E Test.
    Verifies the vertical slice:
    Ingest (Bronze) -> Silver -> Feature Engine -> Rules -> Candidates -> Signal Engine (Solver) -> Execution -> Order
    """

    # 1. Setup In-Memory DB & Patching
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    # Patch Global DB
    import orion.storage.db

    orion.storage.db.engine = test_engine
    orion.storage.db.async_session_factory = test_session_factory

    # CRITICAL: Patch consumers that use "from orion.storage.db import async_session_factory"
    # to avoid stale references since we are not reloading everything.
    import orion.core.solver_router

    orion.core.solver_router.async_session_factory = test_session_factory

    import orion.execution.execution_engine

    orion.execution.execution_engine.async_session_factory = test_session_factory

    import orion.processing.persistence

    orion.processing.persistence.async_session_factory = test_session_factory

    # Create Tables
    from orion.storage.db import Base

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2. Seed Data: Active Solver
    solver_config = SolverConfig(
        version_id="e2e_solver",
        base_strategy_name="E2EStrat",
        universe={"ticker_allowlist": ["SPY"]},
        entry_logic={"rules": [{"rule_name": "AlwaysTrueRule", "params": {}}], "combination_method": "OR"},
        exit_logic={"take_profit_pct": 0.01},
        risk_per_trade_bps=50,
    )

    async with test_session_factory() as session:
        session.add(
            Solver(
                solver_id="e2e_solver",
                family_name="E2E",
                config=solver_config.model_dump(mode="json"),
                is_active=True,
                stage="paper",
            )
        )
        session.add(
            SolverMetrics(
                id="m_1", solver_id="e2e_solver", sharpe_ratio=2.0, info_ratio=2.0, num_trades=10, oos_expect_bp=10.0
            )
        )

        # Seed System Health (Required for ExecutionEngine)
        from orion.storage.models import SystemStatus

        session.add(
            SystemStatus(
                key="global_health", status="HEALTHY", details="E2E Test", last_updated_utc=datetime.now(timezone.utc)
            )
        )

        await session.commit()

    # 3. Step 1: Ingestion (Bronze Event)
    now = datetime.now(timezone.utc)
    raw_event = BronzeEvent(
        event_id="evt_1",
        source="UW",
        event_type="UW_FLOW",
        ticker="SPY",
        event_ts_utc=now,
        received_ts_utc=now,
        payload={
            "ticker": "SPY",
            "premium": 100000,
            "aggressor": "ASK",
            "trade_type": "SWEEP",  # Matches logic for flow?
            "put_call": "CALL",
            "strike": 500.0,
            "price": 1.0,
            "size": 100,
            "expiry": (now + timedelta(days=30)).isoformat(),
        },
        schema_version="v1",
    )

    async with test_session_factory() as session:
        # Ingest
        unique = await ingest_bronze_events(session, [raw_event], run_id="e2e_run", trace_id="e2e_trace")
        await persist_bronze_events(session, unique)
        await persist_silver_from_bronze(session, unique)  # Creates SilverFlow
        await session.commit()

    # 4. Step 2: Processing (Features & Rules)
    # Feature Engine
    fe = FeatureEngine()

    from orion.storage.models_silver import SilverOptionFlow

    async with test_session_factory() as session:
        silver_flows = (
            (await session.execute(pytest.importorskip("sqlalchemy").select(SilverOptionFlow))).scalars().all()
        )

    assert len(silver_flows) == 1

    # Process (Pass Bronze Events)
    signals = fe.process_uw_flow_events(unique)  # Returns SilverSignal objects
    assert len(signals) > 0

    # Rule Engine
    with patch("orion.processing.rule_engine.RuleEngine.process_signals") as mock_rules:
        # Return a dummy Candidate linked to our signal
        cand = CandidateTrade(
            candidate_id="cand_1",
            ticker="SPY",
            timestamp_utc=now,
            rule_id="AlwaysTrueRule",
            direction="LONG",
            confidence=1.0,
            evidence={"signal_id": signals[0].signal_id},
        )
        mock_rules.return_value = [cand]

        re = RuleEngine()
        candidates = re.process_signals(signals)

    # PERSIST CANDIDATES (Required for FKs and overall flow)
    from orion.processing.persistence import persist_candidates

    async with test_session_factory() as session:
        await persist_candidates(session, candidates)
        await session.commit()

    # 5. Step 3: Signal Engine (Decide & Route)
    se = SignalEngine()

    # We need to context switch to Execution here?
    # SignalEngine.decide() uses SolverRouter internally.
    # We check if it picks our "e2e_solver".

    # Mocking context for router if needed, but SignalEngine should build it.
    # SignalEngine.decide(candidate) -> StrategyDecision

    decision = await se.decide(candidates[0])

    assert decision.decision in ["EXECUTE", "SKIP"]
    assert decision.strategy_version_id == "e2e_solver"
    # model_version is None unless ML model is used and populated
    assert decision.model_version is None or decision.model_version == "e2e_solver"

    # Save Decision (usually done by main loop)
    # But Execution needs it passed or persisted?
    async with test_session_factory() as session:
        session.add(decision)
        await session.commit()

    # ExecutionEngine.execute_order takes (decision, candidate) objects.

    # 6. Step 4: Execution
    # Mock Alpaca
    with (
        patch("orion.execution.execution_engine.AlpacaTradingConnector") as MockConn,
        patch("orion.execution.execution_engine.AlpacaMarketConnector") as MockMarket,
    ):
        mock_conn = MockConn.return_value
        mock_market = MockMarket.return_value
        mock_market.get_latest_price.return_value = 500.0

        # Configure submit_limit_order return value to be serializable
        mock_order = MagicMock()
        mock_order.id = "mock_order_123"
        mock_order.status = "new"
        mock_order.model_dump.return_value = {"id": "mock_order_123", "status": "new"}
        mock_conn.submit_limit_order.return_value = mock_order

        ee = ExecutionEngine()
        ee.connector = mock_conn
        ee.market_connector = mock_market
        ee.risk_manager = MagicMock(spec=RiskManager)  # Trust risk for now or mock permissive
        ee.risk_manager.check_order.return_value = True
        ee.risk_manager.calculate_size.return_value = 10
        ee.risk_manager.ticker_exposures = {}  # Mock exposure dict
        ee.risk_manager.config = MagicMock()
        ee.risk_manager.config.enable_shorting = True  # Ensure shorting check passes if needed

        await ee.execute_order(decision, candidates[0])

        # Verify call to Alpaca (only if EXECUTE)
        if decision.decision == "EXECUTE":
            mock_conn.submit_limit_order.assert_called()

        assert decision.executed_successfully == "TRUE"

    # 7. Step 5: Persistence Verification
    # Check that OrderRecord was created
    # Check that OrderRecord was created
    from orion.storage.models_execution import OrderRecord
    from sqlalchemy import select

    async with test_session_factory() as session:
        orders = (await session.execute(select(OrderRecord))).scalars().all()
        assert len(orders) == 1
        assert orders[0].ticker == "SPY"
        # OrderRecord doesn't have solver_id, TradeJournalEntry does.
        # But verifying OrderRecord exists is sufficient for this vertical slice check.

    await test_engine.dispose()
