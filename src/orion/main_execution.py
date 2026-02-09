import asyncio
import signal
from datetime import datetime, timedelta, timezone
from typing import Any, List

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select

from orion.execution.execution_engine import ExecutionEngine
from orion.processing.signal_engine import SignalEngine
from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db
from orion.storage.models_gold import CandidateTrade, StrategyDecision
from orion.storage.models_signals import SignalLive
from orion.storage.models_silver import SilverOptionFlow
from orion.storage.models_trade_journal import TradeJournalEntry

# Configure Logger
logger = setup_struct_logger("orion.execution")


def _should_apply_options_exit_rules(position: Any) -> bool:
    """Guard options-only exit policy from being applied to equity positions."""
    option_chain = getattr(position, "option_chain", None)
    if isinstance(option_chain, str):
        return bool(option_chain.strip())
    return bool(option_chain)


async def fetch_recent_flow_for_ticker(ticker: str, minutes: int = 30) -> List[Any]:
    """Fetch recent flow data for a ticker for exit rule evaluation."""

    async def query_flow(session: Any) -> List[Any]:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        stmt = (
            select(SilverOptionFlow)
            .where(SilverOptionFlow.ticker == ticker)
            .where(SilverOptionFlow.flow_ts_utc >= cutoff)
            .order_by(SilverOptionFlow.flow_ts_utc.desc())
            .limit(100)
        )
        result = await session.execute(stmt)
        return result.scalars().all()

    try:
        return await db_query(query_flow)
    except Exception as e:
        logger.error(f"Failed to fetch recent flow for {ticker}: {e}")
        return []


async def fetch_pending_candidates(limit: int = 100) -> List[CandidateTrade]:
    """
    Fetches CandidateTrades that have NOT been processed (no matching StrategyDecision).
    """

    async def query_candidates(session: Any) -> None:
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

    return await db_query(query_candidates)


async def save_decision(decision: StrategyDecision, candidate: CandidateTrade) -> None:
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

    async def persist_decision(session: Any) -> None:
        session.add(decision)

    await db_write(persist_decision)
    logger.info(f"Policy Decision Saved: {decision.ticker} {decision.decision} ({decision.reason})")

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

        async def persist_signal_and_journal(session: Any) -> None:
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
            # PRDv2 §12.4 Linkage to Trade Journal
            session.add(
                TradeJournalEntry(
                    decision_id=decision.decision_id,
                    signal_id=signal_id,
                    candidate_id=candidate.candidate_id,
                    solver_id=decision.strategy_version_id,
                    ticker=candidate.ticker,
                    direction=str(candidate.direction),
                    evidence=candidate.evidence or {},
                    decision_trace_json=decision.decision_trace_json or {},
                )
            )

        await db_write(persist_signal_and_journal)
    except Exception as e:
        logger.error(f"Failed to persist signals_live/trade journal: {e}")


async def get_pending_candidates() -> list:
    """Get candidates not yet executed."""

    async def fetch_candidates(session: Any) -> None:
        result = await session.execute(
            select(CandidateTrade).where(CandidateTrade.status == "pending").order_by(CandidateTrade.created_at_utc)
        )
        return result.scalars().all()

    return await db_query(fetch_candidates)


async def update_candidate_status(candidate_id: str, status: str) -> None:
    """Update candidate execution status."""

    async def update_status(session: Any) -> None:
        result = await session.execute(select(CandidateTrade).where(CandidateTrade.candidate_id == candidate_id))
        candidate = result.scalars().first()
        if candidate:
            candidate.status = status
            candidate.updated_at_utc = datetime.now(timezone.utc)

    await db_write(update_status)


async def update_decision_status(decision_id: str, status: str) -> None:
    async def update_status(session: Any) -> None:
        stmt = select(StrategyDecision).where(StrategyDecision.decision_id == decision_id)
        result = await session.execute(stmt)
        record = result.scalars().first()
        if record:
            record.executed_successfully = status

    await db_write(update_status)


async def main() -> None:
    # Graceful Shutdown Setup
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received. Stopping execution loop...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("Starting Orion Execution Service (V1 Deterministic)...")

    # 1. Initialize Engines
    signal_engine = SignalEngine()
    execution_engine = ExecutionEngine()

    # Initialize Position Manager and Exit Rules
    from orion.execution.position_manager import PositionManager
    from orion.processing.rules.exit_rules import get_default_exit_rules

    position_manager = PositionManager()
    exit_rules = get_default_exit_rules()

    # Initialize history for execution error tracking
    await execution_engine.initialize()
    await signal_engine.initialize()
    await position_manager.initialize()

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

                    async def run_preflight(session: Any, candidate: Any = candidate, decision: Any = decision) -> Any:
                        return await preflight_live_signal(
                            session,
                            candidate=candidate,
                            decision=decision,
                            risk_manager=execution_engine.risk_manager,
                        )

                    pre = await db_query(run_preflight)
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

        # Position Manager: Check exit rules for open positions
        try:
            for position in position_manager.get_open_positions():
                if not _should_apply_options_exit_rules(position):
                    logger.debug(
                        f"Skipping options exit rules for non-option position: {position.ticker}",
                        extra={"event_type": "EXIT_RULE_SKIP_NON_OPTION", "ticker": position.ticker},
                    )
                    continue

                # Fetch recent flow for this ticker (last 30 min)
                recent_flow = await fetch_recent_flow_for_ticker(position.ticker, minutes=30)

                for rule in exit_rules:
                    exit_sig = rule.should_exit(position, recent_flow, context={})
                    if exit_sig:
                        logger.info(
                            f"Exit signal triggered: {position.ticker} - {exit_sig.rule_id}: {exit_sig.reason}",
                            extra={"event_type": "EXIT_SIGNAL", "ticker": position.ticker, "rule_id": exit_sig.rule_id},
                        )
                        if exit_sig.urgency == "IMMEDIATE":
                            closed = await execution_engine.close_position(
                                ticker=position.ticker,
                                qty=position.qty,
                                exit_signal=exit_sig,
                            )
                            if closed:
                                position_manager.remove_position(position.ticker)
                            break  # Exit on first immediate signal
        except Exception as exit_err:
            logger.error(f"Exit rule evaluation error: {exit_err}")

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
