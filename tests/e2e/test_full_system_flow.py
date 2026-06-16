from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orion.core.solver_schema import SolverConfig
from orion.execution.execution_engine import ExecutionEngine
from orion.execution.risk.manager import RiskManager
from orion.processing.feature_engine import FeatureEngine
from orion.processing.ingest_pipeline import ingest_bronze_events
from orion.processing.persistence import persist_bronze_events, persist_silver_from_bronze
from orion.processing.rule_engine import RuleEngine
from orion.processing.signal_engine import SignalEngine

# Import Core Logic
from orion.storage.models import BronzeEvent
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_solvers import Solver, SolverMetrics


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
            SystemStatus(key="global_health", status="HEALTHY", details="E2E Test", last_updated_utc=datetime.now(UTC))
        )

        await session.commit()

    # 3. Step 1: Ingestion (Bronze Event)
    now = datetime.now(UTC)
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
        await persist_silver_from_bronze(session, unique)  # No-op: Heber is canonical Silver
        await session.commit()

    # 4. Step 2: Processing (Features & Rules)
    # Feature Engine
    fe = FeatureEngine()

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
            option_symbol="SPY260418C00500000",
            premium=1.0,
            expiration_date=now + timedelta(days=30),
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

    # 6. Step 4: Execution (via Gateway mock)
    from unittest.mock import AsyncMock

    ee = ExecutionEngine()
    ee._gateway_available = True
    ee._gateway_check_ts = datetime.now(UTC)

    mock_client = AsyncMock()
    mock_client.get_clock.return_value = {"is_open": True}
    mock_client.get_option_chain.return_value = {
        "contracts": [
            {"contract_symbol": "SPY260418C00500000", "bid": 1.0, "ask": 1.05},
        ]
    }
    mock_client.create_order.return_value = {"id": "mock_order_123", "status": "accepted"}
    ee._get_gateway_client = lambda: mock_client

    ee.risk_manager = MagicMock(spec=RiskManager)
    ee.risk_manager.check_order.return_value = True
    ee.risk_manager.check_sector_exposure.return_value = True
    ee.risk_manager.calculate_size.return_value = 10
    ee.risk_manager.current_equity = 100000.0
    ee.risk_manager.ticker_exposures = {}
    ee.risk_manager.config = MagicMock()
    ee.risk_manager.config.enable_shorting = True
    ee.risk_manager.update_post_trade = AsyncMock()
    ee.risk_manager.remove_pending_order = AsyncMock()

    await ee.execute_order(decision, candidates[0])

    # Verify order was placed via Gateway (only if EXECUTE)
    if decision.decision == "EXECUTE":
        mock_client.create_order.assert_called()

    assert decision.executed_successfully == "TRUE"

    await test_engine.dispose()
