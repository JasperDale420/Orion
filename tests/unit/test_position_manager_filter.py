import pytest
import datetime
import uuid
from orion.storage.models_gold import CandidateTrade, StrategyDecision, ExitDecision
from orion.storage.db import async_session_factory
from orion.execution.position_manager import PositionManager

@pytest.mark.asyncio
async def test_fetch_open_positions_filters_exited(setup_test_db):
    """
    Verify that PositionManager.initialize() filters out positions that have a corresponding ExitDecision.
    """

    # 1. Setup Data
    open_candidate_id = str(uuid.uuid4())
    closed_candidate_id = str(uuid.uuid4())

    now = datetime.datetime.now(datetime.timezone.utc)

    async with async_session_factory() as session:
        # --- Open Position ---
        c1 = CandidateTrade(
            candidate_id=open_candidate_id,
            ticker="OPEN_POS",
            timestamp_utc=now,
            rule_id="rule1",
            direction="LONG",
            confidence=0.9,
            evidence={}
        )
        d1 = StrategyDecision(
            decision_id=str(uuid.uuid4()),
            candidate_id=open_candidate_id,
            timestamp_utc=now,
            ticker="OPEN_POS",
            strategy_version_id="v1",
            decision="EXECUTE",
            executed_successfully="TRUE",
            execution_params={"limit_price": 100}
        )

        # --- Closed Position ---
        c2 = CandidateTrade(
            candidate_id=closed_candidate_id,
            ticker="CLOSED_POS",
            timestamp_utc=now,
            rule_id="rule1",
            direction="LONG",
            confidence=0.9,
            evidence={}
        )
        d2 = StrategyDecision(
            decision_id=str(uuid.uuid4()),
            candidate_id=closed_candidate_id,
            timestamp_utc=now,
            ticker="CLOSED_POS",
            strategy_version_id="v1",
            decision="EXECUTE",
            executed_successfully="TRUE",
            execution_params={"limit_price": 100}
        )
        e2 = ExitDecision(
            exit_id=str(uuid.uuid4()),
            ticker="CLOSED_POS",
            candidate_id=closed_candidate_id,
            rule_id="exit_rule",
            exit_reason="TP",
            exit_ts_utc=now
        )

        session.add_all([c1, d1, c2, d2, e2])
        await session.commit()

    # 2. Initialize PositionManager
    pm = PositionManager()
    await pm.initialize()

    # 3. Assertions
    open_positions = pm.get_open_positions()

    tickers = [p.ticker for p in open_positions]

    # Debug output in case of failure
    print(f"Loaded tickers: {tickers}")

    assert "OPEN_POS" in tickers, "OPEN_POS should be loaded"
    assert "CLOSED_POS" not in tickers, "CLOSED_POS should NOT be loaded (it has an ExitDecision)"
    assert len(open_positions) == 1, f"Expected 1 position, got {len(open_positions)}"
