import asyncio
import logging
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv()

from orion.agents.strategist import StrategistAgent
from orion.execution.execution_engine import ExecutionEngine
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_gold import CandidateTrade, StrategyDecision

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("orion.runner")


async def run_strategist_cycle():
    # Ensure tables exist (esp new StrategyDecision)
    await init_db()

    agent = StrategistAgent()
    executor = ExecutionEngine()

    # Pre-sync risk manager handles in executor.initialize()
    # if executor.risk_manager and executor.connector:
    #     executor.risk_manager.sync_with_broker(executor.connector)

    # Load historical execution state
    await executor.initialize()

    while True:
        try:
            async with async_session_factory() as session:
                # Fetch unprocessed candidates
                # Left join to StrategyDecision where decision is NULL
                stmt = (
                    select(CandidateTrade)
                    .outerjoin(StrategyDecision, CandidateTrade.candidate_id == StrategyDecision.candidate_id)
                    .where(StrategyDecision.decision_id == None)
                    .order_by(CandidateTrade.timestamp_utc.desc())
                    .limit(5)
                )

                result = await session.execute(stmt)
                candidates = result.scalars().all()

                if not candidates:
                    logger.info("No new candidates found.")
                else:
                    logger.info(f"Processing {len(candidates)} new candidates...")

                for cand in candidates:
                    logger.info(f"--- Analyzing {cand.ticker} ({cand.direction}) ---")

                    decision_payload = await agent.run({"candidate": cand})

                    action = decision_payload.get("decision", "SKIP").upper()
                    rationale = decision_payload.get("rationale", "")

                    logger.info(f"Decision: {action}")
                    logger.info(f"Rationale: {rationale}")

                    # Execute
                    exec_status = "SKIPPED"
                    if action == "EXECUTE":
                        try:
                            await executor.execute_decision(decision_payload, cand)
                            exec_status = "TRUE"
                        except Exception as e:
                            logger.error(f"Execution handling failed: {e}")
                            exec_status = "FALSE"

                    # Persist Decision
                    # Create Decision Record
                    decision_record = StrategyDecision(
                        decision_id=str(uuid.uuid4()),
                        candidate_id=cand.candidate_id,
                        timestamp_utc=datetime.now(timezone.utc),
                        decision=action,
                        rationale=rationale,
                        executed_successfully=exec_status,
                    )
                    session.add(decision_record)

                    logger.info("-" * 30)

                # Commit all decisions
                await session.commit()
                if candidates:
                    logger.info("Cycle complete. Decisions persisted.")

            # Sleep between cycles
            await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Strategist Loop Error: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(run_strategist_cycle())
