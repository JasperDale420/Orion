import asyncio
import signal
from typing import List

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select

from orion.execution.execution_engine import ExecutionEngine
from orion.processing.signal_engine import SignalEngine
from orion.shared.logger import setup_struct_logger
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_gold import CandidateTrade, StrategyDecision
from orion.storage.models_signals import SignalLive
from orion.storage.models_trade_journal import TradeJournalEntry

# Configure Logger
logger = setup_struct_logger("orion.execution")


async def fetch_pending_candidates(limit: int = 100) -> List[CandidateTrade]:
    """
    Fetches CandidateTrades that have NOT been processed (no matching StrategyDecision).
    """
    async with async_session_factory() as session:
        # Subquery to find processed IDs
        # (Naive approach for v1: Select candidates where candidate_id NOT IN (select candidate_id from strategy_decisions))
        # Better: Outer join?
        # For simple polling:

        stmt = (
            select(CandidateTrade)
            .outerjoin(StrategyDecision, CandidateTrade.candidate_id == StrategyDecision.candidate_id)
            .where(StrategyDecision.candidate_id.is_(None))
            .order_by(CandidateTrade.timestamp_utc.asc())  # FIFO
            .limit(limit)
        )

        result = await session.execute(stmt)
        return result.scalars().all()


async def save_decision(decision: StrategyDecision, candidate: CandidateTrade):
    # PRDv2 §11.2: EXECUTE decisions must carry expected_return, p_take, risk_score (for signals_live).
    if decision.decision == "EXECUTE":
        expected_return = None
        risk_score = None
        if isinstance(decision.decision_trace_json, dict):
            expected_return = decision.decision_trace_json.get("expected_return_bp")
            risk_score = decision.decision_trace_json.get("risk_score")
        if expected_return is None or risk_score is None or decision.p_take is None:
            decision.decision = "SKIP"
            decision.reason = "Missing required signal fields (expected_return/risk_score/p_take)"

    async with async_session_factory() as session:
        session.add(decision)
        try:
            await session.commit()
            logger.info(f"Policy Decision Saved: {decision.ticker} {decision.decision} ({decision.reason})")
        except Exception as e:
            logger.error(f"Failed to save decision: {e}")
            await session.rollback()
            return

    # PRD §11.2/§12.4: Persist signals_live + trade journal linkage for executable decisions.
    if decision.decision != "EXECUTE":
        return

    signal_id = f"sig_{candidate.candidate_id}"
    try:
        expected_return = None
        risk_score = None
        if isinstance(decision.decision_trace_json, dict):
            expected_return = decision.decision_trace_json.get("expected_return_bp")
            risk_score = decision.decision_trace_json.get("risk_score")

        # Fail-fast for PRDv2 §11.2 required fields.
        if expected_return is None or risk_score is None or decision.p_take is None:
            logger.error(
                "EXECUTE decision missing required fields for signals_live; skipping persistence/execution",
                extra={
                    "event_type": "SIGNAL_PERSIST_MISSING_FIELDS",
                    "ticker": candidate.ticker,
                    "candidate_id": candidate.candidate_id,
                    "expected_return": expected_return,
                    "risk_score": risk_score,
                    "p_take": decision.p_take,
                },
            )
            # Downgrade decision locally to prevent execution.
            decision.decision = "SKIP"
            decision.reason = "Missing required signal fields (expected_return/risk_score/p_take)"
            return

        async with async_session_factory() as session:
            session.add(
                SignalLive(
                    signal_id=signal_id,
                    timestamp_utc=decision.timestamp_utc,
                    ticker=candidate.ticker,
                    direction=candidate.direction,
                    rule_id=candidate.rule_id,
                    model_version=decision.model_version,
                    expected_return=float(expected_return),
                    p_take=decision.p_take,
                    risk_score=float(risk_score),
                    entry_logic={
                        "order_type": (decision.execution_params or {}).get("order_type"),
                        "time_in_force": (decision.execution_params or {}).get("time_in_force"),
                        "limit_price": (decision.execution_params or {}).get("limit_price"),
                    },
                    exit_rules={
                        "stop_loss_pct": (decision.execution_params or {}).get("stop_loss_pct"),
                        "take_profit_pct": (decision.execution_params or {}).get("take_profit_pct"),
                    },
                    evidence=candidate.evidence or {},
                    decision_trace_json=decision.decision_trace_json or {},
                )
            )
            session.add(
                TradeJournalEntry(
                    decision_id=decision.decision_id,
                    signal_id=signal_id,
                    candidate_id=candidate.candidate_id,
                    solver_id=decision.strategy_version_id,
                    ticker=candidate.ticker,
                    direction=candidate.direction,
                    evidence=candidate.evidence or {},
                    decision_trace_json=decision.decision_trace_json or {},
                    raw_json={"execution_params": decision.execution_params or {}},
                )
            )
            await session.commit()
    except Exception as e:
        logger.error(f"Failed to persist signals_live/trade journal: {e}")


async def update_decision_status(decision_id: str, status: str):
    async with async_session_factory() as session:
        stmt = select(StrategyDecision).where(StrategyDecision.decision_id == decision_id)
        result = await session.execute(stmt)
        record = result.scalars().first()
        if record:
            record.executed_successfully = status
            try:
                await session.commit()
            except Exception as e:
                logger.error(f"Failed to update execution status: {e}")


async def main():
    # Graceful Shutdown Setup
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received. Stopping execution loop...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("Starting Orion Execution Service (V1 Deterministic)...")

    # 1. Initialize Engines
    signal_engine = SignalEngine()
    execution_engine = ExecutionEngine()

    # Initialize history for execution error tracking
    await execution_engine.initialize()
    await signal_engine.initialize()

    # Ensure tables exist (if running standalone)
    await init_db()

    logger.info("Engines Initialized. Entering Service Loop.")

    while not shutdown_event.is_set():
        start_time = loop.time()

        try:
            # 1.5 Poll Fills (Real-time Risk Updates)
            await execution_engine.poll_fills()

            # 1.6 Check Circuit Breaker
            from orion.core.circuit_breaker import CircuitBreaker

            cb = CircuitBreaker()
            if await cb.is_open():
                state = await cb.get_state()
                logger.warning(f"CIRCUIT BREAKER OPEN: {state.get('reason')}. Pausing execution.")
                await asyncio.sleep(5.0)
                continue

            # 2. Poll Pending Candidates
            candidates = await fetch_pending_candidates()

            if not candidates:
                # Sleep and continue
                await asyncio.sleep(1.0)
                continue

            logger.info(f"Processing {len(candidates)} new candidates...")

            for candidate in candidates:
                # 3. Policy Execution
                decision = await signal_engine.decide(candidate)

                # 3.5 Pre-signal portfolio/risk/rollup filters (PRD §11.2)
                if decision.decision == "EXECUTE":
                    from orion.execution.signal_preflight import preflight_live_signal

                    async with async_session_factory() as session:
                        pre = await preflight_live_signal(
                            session,
                            candidate=candidate,
                            decision=decision,
                            risk_manager=execution_engine.risk_manager,
                        )
                    if not pre.ok:
                        decision.decision = "SKIP"
                        decision.executed_successfully = "SKIPPED"
                        decision.reason = f"Preflight reject: {pre.reason}"
                        decision.decision_trace_json = decision.decision_trace_json or {}
                        decision.decision_trace_json["preflight_reject"] = {"reason": pre.reason, **(pre.extra or {})}
                    else:
                        decision.decision_trace_json = decision.decision_trace_json or {}
                        decision.decision_trace_json["rollups"] = (pre.extra or {}).get("rollups", {})
                        decision.decision_trace_json["preflight"] = {
                            k: v for k, v in (pre.extra or {}).items() if k != "rollups"
                        }

                # 4. Save Decision Draft
                await save_decision(decision, candidate)

                # 5. Execute (if EXECUTE)
                exec_status = "SKIPPED"
                if decision.decision == "EXECUTE":
                    try:
                        # Fix: Call execute_order with decision object
                        await execution_engine.execute_order(decision, candidate)

                        # Check result in decision object itself (updated by engine)
                        if decision.executed_successfully == "TRUE":
                            exec_status = "TRUE"
                        else:
                            exec_status = "FALSE"

                    except Exception as exe:
                        logger.error(f"Execution Exception: {exe}")
                        exec_status = "FALSE"

                # 6. Update Decision Status
                await update_decision_status(decision.decision_id, exec_status)

        except Exception as e:
            logger.error(f"Main Loop Error: {e}")
            await asyncio.sleep(5.0)  # Backoff

        # Optional: Position Manager check would go here (Phase 2)

        elapsed = loop.time() - start_time
        sleep_time = max(0.1, 1.0 - elapsed)

        # Smart Sleep: Wait for sleep_time OR shutdown_event
        # If shutdown triggered during sleep, we exit immediately after
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_time)
            break  # Shutdown set
        except asyncio.TimeoutError:
            pass  # Sleep done, continue loop

    logger.info("Execution Service Stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
