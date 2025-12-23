import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Configure Env BEFORE imports
os.environ["ORION_ALPACA_API_KEY"] = "mock_key"
os.environ["ORION_ALPACA_SECRET_KEY"] = "mock_secret"
os.environ["ORION_ALPACA_PAPER"] = "True"
os.environ["ORION_RISK_ENABLE_SHORTING"] = "False"

# --- MOCK DB INFRASTRUCTURE ---
GLOBAL_DB_STATE = []


class MockResult:
    def __init__(self, data):
        self._data = data

    def scalars(self):
        return self

    def all(self):
        return self._data

    def scalar_one_or_none(self):
        return self._data[0] if self._data else None


class MockAsyncSession:
    def __init__(self):
        self.store = []
        self.committed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    def add(self, obj):
        self.store.append(obj)

    async def commit(self):
        global GLOBAL_DB_STATE
        GLOBAL_DB_STATE.extend(self.store)
        self.committed.extend(self.store)
        self.store = []

    async def execute(self, stmt):
        str_stmt = str(stmt)
        global GLOBAL_DB_STATE

        # Naive Query Parsing for Smoke Test
        if "candidate_trades" in str_stmt and "strategy_decisions" in str_stmt:
            cands = [o for o in GLOBAL_DB_STATE if isinstance(o, CandidateTrade)]
            decs = [o for o in GLOBAL_DB_STATE if isinstance(o, StrategyDecision)]
            dec_cand_ids = {d.candidate_id for d in decs}

            pending = [c for c in cands if c.candidate_id not in dec_cand_ids]
            return MockResult(pending)

        if "FROM strategy_decisions" in str_stmt:
            decs = [o for o in GLOBAL_DB_STATE if isinstance(o, StrategyDecision)]
            return MockResult(decs)

        return MockResult([])


def mock_session_factory():
    return MockAsyncSession()


# Import Models
# (We assume these exist)
from orion.storage.models_gold import CandidateTrade, StrategyDecision

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("smoke_test")


async def seed_candidate():
    test_id = "smoke_test_" + uuid.uuid4().hex[:8]

    candidate = CandidateTrade(
        candidate_id=test_id,
        ticker="SPY",
        timestamp_utc=datetime.now(timezone.utc),
        rule_id="smoke_test_rule",
        direction="LONG",
        confidence=0.99,
        evidence={"note": "Created by paper_live_harness.py"},
    )

    GLOBAL_DB_STATE.append(candidate)
    logger.info(f"Seeded Candidate: {candidate.candidate_id} for SPY")
    return test_id


async def verify_execution(candidate_id):
    decisions = [o for o in GLOBAL_DB_STATE if isinstance(o, StrategyDecision) and o.candidate_id == candidate_id]

    if decisions:
        decision = decisions[0]
        logger.info(f"VERIFICATION PASS: Found Decision {decision.decision} (Exec: {decision.executed_successfully})")
        if decision.executed_successfully == "TRUE":
            logger.info("Order Execution marked SUCCESSFUL.")
        else:
            logger.warning(f"Order Execution marked as {decision.executed_successfully}")
    else:
        logger.error("VERIFICATION FAIL: No StrategyDecision found for candidate.")


async def main():
    logger.info("Starting Paper Live Harness Smoke Test (MOCKED DB)...")

    # Seed Data
    candidate_id = await seed_candidate()

    logger.info("Running Strategist Cycle...")

    # Patch Everything
    with (
        patch("orion.run_agent.init_db", new=AsyncMock()),
        patch("orion.run_agent.async_session_factory", side_effect=mock_session_factory),
        patch("orion.execution.execution_engine.AlpacaTradingConnector") as MockTradingConnector,
        patch("orion.connectors.alpaca_market_connector.AlpacaMarketConnector") as MockMarketConnector,
        patch("orion.run_agent.StrategistAgent") as MockStrategist,
    ):
        # Mock Trading Connector (Limit Order)
        trade_instance = MockTradingConnector.return_value
        # Mock Account for Risk Sync (Equity > 0)
        mock_account = MagicMock()
        mock_account.equity = 100000.0
        mock_account.last_equity = 100000.0
        mock_account.buying_power = 200000.0
        mock_account.currency = "USD"
        trade_instance.client.get_account.return_value = mock_account
        trade_instance.client.get_all_positions.return_value = []

        trade_instance.submit_limit_order.return_value = MagicMock(id="test_order_id", status="accepted")

        # Mock Market Connector (Price Data)
        market_instance = MockMarketConnector.return_value
        market_instance.get_latest_price.return_value = 400.0

        # Mock Strategist Agent (Force Execution)
        agent_instance = MockStrategist.return_value
        agent_instance.run = AsyncMock(return_value={"decision": "EXECUTE", "rationale": "Smoke Test Forced Execution"})

        # Import run_agent here
        from orion.run_agent import run_strategist_cycle

        await run_strategist_cycle()

    # Verify
    await verify_execution(candidate_id)

    logger.info("Smoke Test Complete.")


if __name__ == "__main__":
    asyncio.run(main())
