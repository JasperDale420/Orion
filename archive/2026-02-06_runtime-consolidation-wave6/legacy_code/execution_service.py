import asyncio
import signal
from typing import Any, List, Sequence

from orion.execution.execution_engine import ExecutionEngine
from orion.processing.signal_engine import SignalEngine
from orion.shared.db_utils import db_query, db_write
from orion.shared.decorators import db_retry
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db
from orion.storage.models_gold import CandidateTrade, StrategyDecision
from orion.storage.models_signals import SignalLive
from orion.storage.models_trade_journal import TradeJournalEntry
from sqlalchemy import select

logger = setup_struct_logger("orion.execution")

# Initialize metrics
try:
    from orion.shared.metrics import init_metrics

    _metrics = init_metrics()
except ImportError:
    _metrics = None


class ExecutionService:
    def __init__(self) -> None:
        self.shutdown_event = asyncio.Event()
        self.signal_engine = SignalEngine()
        self.execution_engine = ExecutionEngine()

    async def run(self) -> None:
        # Graceful Shutdown Setup
        loop = asyncio.get_running_loop()

        def _signal_handler() -> None:
            logger.info("Shutdown signal received. Stopping execution loop...")
            self.shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        logger.info("Starting Orion Execution Service (V1 Deterministic)...")

        # Initialize Engines
        await self.execution_engine.initialize()
        await self.signal_engine.initialize()
        await init_db()

        logger.info("Engines Initialized. Entering Service Loop.")

        # Backfill queue with unprocessed candidates (restart recovery)
        await self._backfill_queue()

        while not self.shutdown_event.is_set():
            start_time = loop.time()
            try:
                await self._run_cycle()
            except Exception as e:
                logger.error(f"Main Loop Error: {e}")
                await asyncio.sleep(5.0)

            elapsed = loop.time() - start_time
            sleep_time = max(0.1, 1.0 - elapsed)

            try:
                await asyncio.wait_for(self.shutdown_event.wait(), timeout=sleep_time)
            except asyncio.TimeoutError:
                pass

        logger.info("Execution Service Stopped.")

    async def _run_cycle(self) -> None:
        # 1. Poll Fills
        await self.execution_engine.poll_fills()

        # 2. Check Circuit Breaker
        if await self._check_circuit_breaker():
            await asyncio.sleep(5.0)
            return

        # 3. Poll Pending Candidates
        candidates = await self._fetch_pending_candidates()

        # Metrics: track queue depth
        if _metrics:
            from orion.shared.candidate_queue import CandidateQueue

            queue = await CandidateQueue.get_instance()
            _metrics.execution_queue_depth.set(queue.qsize())

        if not candidates:
            await asyncio.sleep(1.0)
            return

        logger.info(f"Processing {len(candidates)} new candidates...")
        for candidate in candidates:
            await self._process_candidate(candidate)

    async def _check_circuit_breaker(self) -> bool:
        from orion.core.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()
        if await cb.is_open():
            state = await cb.get_state()
            logger.warning(f"CIRCUIT BREAKER OPEN: {state.get('reason')}. Pausing execution.")
            return True
        return False

    async def _process_candidate(self, candidate: CandidateTrade) -> None:
        # Track latency from candidate creation to processing
        import time

        start_time = time.time()

        # Policy Execution
        decision = await self.signal_engine.decide(candidate)

        # Preflight
        if decision.decision == "EXECUTE":
            await self._run_preflight(candidate, decision)

        # Save Decision Draft
        await self._save_decision(decision)

        # Execute
        exec_status = "SKIPPED"
        if decision.decision == "EXECUTE":
            try:
                await self.execution_engine.execute_order(decision, candidate)
                if decision.executed_successfully == "TRUE":
                    exec_status = "TRUE"
                else:
                    exec_status = "FALSE"
            except Exception as exe:
                logger.error(f"Execution Exception: {exe}")
                exec_status = "FALSE"

        # Update Decision
        await self._update_decision_status(decision.decision_id, exec_status)

        # Metrics: track decisions and orders
        if _metrics:
            _metrics.execution_decisions_total.labels(decision_type=decision.decision).inc()
            if decision.decision == "EXECUTE":
                status_label = "success" if exec_status == "TRUE" else "failure"
                _metrics.execution_orders_total.labels(status=status_label).inc()

            # Track latency
            latency = time.time() - start_time
            _metrics.execution_latency_seconds.labels(ticker=candidate.ticker).observe(latency)

    async def _run_preflight(self, candidate: CandidateTrade, decision: StrategyDecision) -> None:
        from orion.execution.signal_preflight import preflight_live_signal

        async def run_check(session: Any) -> Any:
            return await preflight_live_signal(
                session,
                candidate=candidate,
                decision=decision,
                risk_manager=self.execution_engine.risk_manager,
            )

        pre = await db_query(run_check)

        if not pre.ok:
            decision.decision = "SKIP"
            decision.executed_successfully = "SKIPPED"
            decision.reason = f"Preflight reject: {pre.reason}"
            decision.decision_trace_json = decision.decision_trace_json or {}
            decision.decision_trace_json["preflight_reject"] = {"reason": pre.reason, **(pre.extra or {})}
        else:
            decision.decision_trace_json = decision.decision_trace_json or {}
            decision.decision_trace_json["rollups"] = (pre.extra or {}).get("rollups", {})
            decision.decision_trace_json["preflight"] = {k: v for k, v in (pre.extra or {}).items() if k != "rollups"}

    async def _fetch_pending_candidates(self, limit: int = 100) -> Sequence[CandidateTrade]:
        from orion.shared.candidate_queue import CandidateQueue

        queue = await CandidateQueue.get_instance()
        candidate_ids = []

        # Drain queue up to limit
        for _ in range(limit):
            cid = await queue.pop(timeout=0.1)
            if cid is None:
                break
            candidate_ids.append(cid)

        if not candidate_ids:
            return []

        async def fetch_by_ids(session: Any) -> List[Any]:
            stmt = select(CandidateTrade).where(CandidateTrade.candidate_id.in_(candidate_ids))
            result = await session.execute(stmt)
            return result.scalars().all()

        return await db_query(fetch_by_ids)

    async def _backfill_queue(self) -> None:
        """Backfill queue with unprocessed candidates on startup."""

        try:

            async def fetch_unprocessed(session: Any) -> List[Any]:
                # Find candidates without decisions
                stmt = (
                    select(CandidateTrade)
                    .outerjoin(StrategyDecision, CandidateTrade.candidate_id == StrategyDecision.candidate_id)
                    .where(StrategyDecision.candidate_id.is_(None))
                    .order_by(CandidateTrade.timestamp_utc.asc())
                    .limit(1000)  # Limit backfill to avoid overwhelming queue
                )
                result = await session.execute(stmt)
                return result.scalars().all()

            candidates = await db_query(fetch_unprocessed)

            from orion.shared.candidate_queue import CandidateQueue

            queue = await CandidateQueue.get_instance()
            for c in candidates:
                await queue.push(c.candidate_id)
            logger.info(
                f"Backfilled queue with {len(candidates)} unprocessed candidates",
                extra={"event_type": "QUEUE_BACKFILL", "count": len(candidates)},
            )
        except Exception as e:
            logger.error(f"Failed to backfill queue: {e}")

    @db_retry
    async def _save_decision(self, decision: StrategyDecision) -> None:
        """
        Persist decision to DB.
        """
        if decision.decision == "EXECUTE":
            self._validate_execute_fields(decision)

        async def save_op(session: Any) -> None:
            session.add(decision)
            logger.info(
                f"Saved decision {decision.decision_id} ({decision.decision})",
                extra={"event_type": "DECISION_SAVED", "decision_id": decision.decision_id},
            )

        await db_write(save_op)

        # The logic for _persist_signal_live needs to be moved to _process_candidate
        # or another method that has access to both decision and candidate.
        # This change only implements the requested modification to _save_decision.

    def _validate_execute_fields(self, decision: StrategyDecision) -> None:
        expected_return = None
        risk_score = None
        if isinstance(decision.decision_trace_json, dict):
            expected_return = decision.decision_trace_json.get("expected_return_bp")
            risk_score = decision.decision_trace_json.get("risk_score")
        if expected_return is None or risk_score is None or decision.p_take is None:
            decision.decision = "SKIP"
            decision.reason = "Missing required signal fields (expected_return/risk_score/p_take)"

    @db_retry
    async def _persist_signal_live(self, decision: StrategyDecision, candidate: CandidateTrade) -> None:
        expected_return = None
        risk_score = None
        if isinstance(decision.decision_trace_json, dict):
            expected_return = decision.decision_trace_json.get("expected_return_bp")
            risk_score = decision.decision_trace_json.get("risk_score")

        if expected_return is None or risk_score is None or decision.p_take is None:
            logger.error(f"EXECUTE decision missing required fields, skipping persistence: {candidate.ticker}")
            decision.decision = "SKIP"
            decision.reason = "Missing required signal fields"

            # Log rejection to TradeJournal
            from datetime import datetime, timezone

            async def log_rejection(session: Any) -> None:
                session.add(
                    TradeJournalEntry(
                        journal_id=f"reject_{candidate.candidate_id}",
                        signal_id=None,
                        candidate_id=candidate.candidate_id,
                        ticker=candidate.ticker,
                        direction=candidate.direction,
                        entry_time=datetime.now(timezone.utc),
                        entry_price=None,
                        position_size=None,
                        exit_time=None,
                        exit_price=None,
                        realized_pnl_usd=None,
                        fees_usd=None,
                        net_pnl_usd=None,
                        trade_metadata={"rejection_reason": decision.reason},
                    )
                )

            await db_write(log_rejection)
            return

        try:

            async def log_skip(session: Any) -> None:
                session.add(
                    TradeJournalEntry(
                        journal_id=f"skip_{candidate.candidate_id}",
                        signal_id=None,
                        candidate_id=candidate.candidate_id,
                        ticker=candidate.ticker,
                        direction=candidate.direction,
                        entry_time=datetime.now(timezone.utc),
                        entry_price=None,
                        position_size=None,
                        exit_time=None,
                        exit_price=None,
                        realized_pnl_usd=None,
                        fees_usd=None,
                        net_pnl_usd=None,
                        trade_metadata={"skip_reason": "preflight_failed"},
                    )
                )

            await db_write(log_skip)
        except Exception as e:
            logger.error(f"Failed to log preflight skip: {e}")
        """
        Copy relevant decision trace and params to signals_live for downstream consumers.
        """

        async def save_signal(session: Any) -> None:
            sig = SignalLive(
                signal_id=f"live_{decision.decision_id}",
                ticker=decision.ticker,
                timestamp_utc=decision.timestamp_utc,
                decision_id=decision.decision_id,
                candidate_id=decision.candidate_id,
                solver_id=decision.strategy_version_id,
                decision_trace_json=decision.decision_trace_json or {},
                execution_params=decision.execution_params or {},
            )
            session.add(sig)
            logger.info(f"Emitted Signal Live for {decision.ticker} ({decision.decision_id})")

        await db_write(save_signal)

    @db_retry
    async def _persist_trade_journal(self, decision: StrategyDecision, exec_status: str, notes: str) -> None:
        """
        Log execution outcome to trade journal.
        """

        async def save_journal(session: Any) -> None:
            entry = TradeJournalEntry(
                ticker=decision.ticker,
                timestamp_utc=decision.timestamp_utc,
                decision_id=decision.decision_id,
                exec_status=exec_status,
                notes=notes,
            )
            session.add(entry)

        await db_write(save_journal)

    @db_retry
    async def _update_decision_status(self, decision_id: str, status: str) -> None:
        async def update_status(session: Any) -> None:
            stmt = select(StrategyDecision).where(StrategyDecision.decision_id == decision_id)
            result = await session.execute(stmt)
            record = result.scalars().first()
            if record:
                record.executed_successfully = status

        try:
            await db_write(update_status)
        except Exception as e:
            logger.error(f"Failed to update execution status: {e}")
