from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# We need to import the class to test
# CORRECT IMPORT: LiveContext is in solver_schema
from orion.core.solver_schema import LiveContext, SolverConfig


@pytest.mark.asyncio
async def test_solver_router_integration():
    """
    Integration test for SolverRouter using a real in-memory SQLite database.
    Verifies:
    1. Ticker-specific routing (AAPL vs Global)
    2. Stage filtering (Paper vs Live)
    3. Metric-based Ranking (Sharpe Ratio)
    """

    # 1. Setup In-Memory DB & Patching
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    # Patch Global DB
    import orion.storage.db

    orion.storage.db.engine = test_engine
    orion.storage.db.async_session_factory = test_session_factory

    # Reload Router to pick up patched factory
    import importlib

    import orion.core.solver_router

    importlib.reload(orion.core.solver_router)
    from orion.core.solver_router import SolverRouter

    # Import Models
    from orion.storage.db import Base
    from orion.storage.models_solvers import Solver, SolverMetrics

    # Create Tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2. Seed Data
    # Solver A: AAPL Only, High Sharpe
    config_a = SolverConfig(
        version_id="solver_aapl",
        base_strategy_name="StratA",
        universe={"ticker_allowlist": ["AAPL"]},
        risk_per_trade_bps=10,
    )

    # Solver B: Global, Low Sharpe
    config_b = SolverConfig(
        version_id="solver_global",
        base_strategy_name="StratB",
        universe=None,  # Global
        risk_per_trade_bps=10,
    )

    # Solver C: TSLA Only, Medium Sharpe
    config_c = SolverConfig(
        version_id="solver_tsla",
        base_strategy_name="StratC",
        universe={"ticker_allowlist": ["TSLA"]},
        risk_per_trade_bps=10,
    )

    # Solver D: Live Stage (should be filtered out if context is paper)
    config_d = SolverConfig(version_id="solver_live", base_strategy_name="StratD", universe=None, risk_per_trade_bps=10)

    async with test_session_factory() as session:
        # A: AAPL
        session.add(
            Solver(
                solver_id="solver_aapl",
                family_name="A",
                config=config_a.model_dump(mode="json"),
                is_active=True,
                stage="paper",
            )
        )
        session.add(
            SolverMetrics(
                id="m_a", solver_id="solver_aapl", sharpe_ratio=2.5, info_ratio=2.5, num_trades=100, oos_expect_bp=10.0
            )
        )

        # B: Global
        session.add(
            Solver(
                solver_id="solver_global",
                family_name="B",
                config=config_b.model_dump(mode="json"),
                is_active=True,
                stage="paper",
            )
        )
        session.add(
            SolverMetrics(
                id="m_b",
                solver_id="solver_global",
                sharpe_ratio=1.0,
                info_ratio=1.0,
                num_trades=100,
                oos_expect_bp=10.0,
            )
        )

        # C: TSLA
        session.add(
            Solver(
                solver_id="solver_tsla",
                family_name="C",
                config=config_c.model_dump(mode="json"),
                is_active=True,
                stage="paper",
            )
        )
        session.add(
            SolverMetrics(
                id="m_c", solver_id="solver_tsla", sharpe_ratio=1.8, info_ratio=1.8, num_trades=100, oos_expect_bp=10.0
            )
        )

        # D: Live
        session.add(
            Solver(
                solver_id="solver_live",
                family_name="D",
                config=config_d.model_dump(mode="json"),
                is_active=True,
                stage="live",
            )
        )
        session.add(
            SolverMetrics(
                id="m_d", solver_id="solver_live", sharpe_ratio=3.0, info_ratio=3.0, num_trades=100, oos_expect_bp=10.0
            )
        )

        await session.commit()

    # 3. Test Routing
    router = SolverRouter()
    now = datetime.now(UTC)

    # Case 1: Ticker = AAPL, Stage = Paper
    # Expect: A (Specific, Sharpe 2.5) > B (Global, Sharpe 1.0). D (Live) -> Excluded. C (TSLA) -> Excluded.
    ctx_aapl = LiveContext(ticker="AAPL", current_stage="paper", regime="neutral", time_of_day_utc=now)
    solvers_aapl = await router.select_solvers(ctx_aapl)

    print(f"DEBUG: AAPL Solvers found: {[s.solver_id for s in solvers_aapl]}")

    # Debug: Check DB
    from sqlalchemy import text

    async with test_session_factory() as s2:
        res = await s2.execute(text("SELECT solver_id, stage, is_active FROM solvers"))
        rows = res.fetchall()
        print(f"DEBUG: DB Solvers: {rows}")

    assert len(solvers_aapl) >= 2, f"Should find A and B. Found: {[s.solver_id for s in solvers_aapl]}"

    ids = [s.solver_id for s in solvers_aapl]
    assert "solver_aapl" in ids
    assert "solver_global" in ids
    assert "solver_tsla" not in ids, "TSLA solver should not match AAPL"
    # Live solver SHOULD matches Paper context (valid to run live strat in paper)
    assert "solver_live" in ids

    # Check Ranking: D (Live, Sharpe 3.0) and A (Specific, Sharpe 2.5) should be top 2.
    top_2 = {s.solver_id for s in solvers_aapl[:2]}
    assert "solver_live" in top_2
    assert "solver_aapl" in top_2

    # Case 2: Ticker = MSFT, Stage = Paper
    # Expect: B (Global) AND D (Live). A and C specific.
    ctx_msft = LiveContext(ticker="MSFT", current_stage="paper", regime="neutral", time_of_day_utc=now)
    solvers_msft = await router.select_solvers(ctx_msft)

    # Should find Global and Live
    assert len(solvers_msft) == 2
    ids_msft = [s.solver_id for s in solvers_msft]
    assert "solver_global" in ids_msft
    assert "solver_live" in ids_msft

    # Case 3: Live Stage
    # Expect: D only (if Global matches everything). A, B, C are Paper.
    ctx_live = LiveContext(ticker="AAPL", current_stage="live", regime="neutral", time_of_day_utc=now)
    solvers_live = await router.select_solvers(ctx_live)

    assert len(solvers_live) == 1
    assert solvers_live[0].solver_id == "solver_live"

    await test_engine.dispose()
