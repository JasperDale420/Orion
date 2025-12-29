import asyncio
import signal
from typing import Sequence

from orion.execution.execution_engine import ExecutionEngine
from orion.processing.signal_engine import SignalEngine
from orion.shared.logger import setup_struct_logger
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_gold import CandidateTrade, StrategyDecision
from orion.storage.models_signals import SignalLive
from orion.storage.models_trade_journal import TradeJournalEntry
from sqlalchemy import select

logger = setup_struct_logger("orion.execution")


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
        # Policy Execution
        decision = await self.signal_engine.decide(candidate)

        # Preflight
        if decision.decision == "EXECUTE":
            await self._run_preflight(candidate, decision)

        # Save Decision Draft
        await self._save_decision(decision, candidate)

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

    async def _run_preflight(self, candidate: CandidateTrade, decision: StrategyDecision) -> None:
        from orion.execution.signal_preflight import preflight_live_signal

        async with async_session_factory() as session:
            pre = await preflight_live_signal(
                session,
                candidate=candidate,
                decision=decision,
                risk_manager=self.execution_engine.risk_manager,
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
            decision.decision_trace_json["preflight"] = {k: v for k, v in (pre.extra or {}).items() if k != "rollups"}

    async def _fetch_pending_candidates(self, limit: int = 100) -> Sequence[CandidateTrade]:
        async with async_session_factory() as session:
            stmt = (
                select(CandidateTrade)
                .outerjoin(StrategyDecision, CandidateTrade.candidate_id == StrategyDecision.candidate_id)
                .where(StrategyDecision.candidate_id.is_(None))
                .order_by(CandidateTrade.timestamp_utc.asc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return result.scalars().all()

    async def _save_decision(self, decision: StrategyDecision, candidate: CandidateTrade) -> None:
        if decision.decision == "EXECUTE":
            self._validate_execute_fields(decision)

        async with async_session_factory() as session:
            session.add(decision)
            try:
                await session.commit()
                logger.info(f"Policy Decision Saved: {decision.ticker} {decision.decision} ({decision.reason})")
            except Exception as e:
                logger.error(f"Failed to save decision: {e}")
                await session.rollback()
                return

        if decision.decision == "EXECUTE":
            await self._persist_signal_live(decision, candidate)

    def _validate_execute_fields(self, decision: StrategyDecision) -> None:
        expected_return = None
        risk_score = None
        if isinstance(decision.decision_trace_json, dict):
            expected_return = decision.decision_trace_json.get("expected_return_bp")
            risk_score = decision.decision_trace_json.get("risk_score")
        if expected_return is None or risk_score is None or decision.p_take is None:
            decision.decision = "SKIP"
            decision.reason = "Missing required signal fields (expected_return/risk_score/p_take)"

    async def _persist_signal_live(self, decision: StrategyDecision, candidate: CandidateTrade) -> None:
        signal_id = f"sig_{candidate.candidate_id}"
        expected_return = None
        risk_score = None
        if isinstance(decision.decision_trace_json, dict):
            expected_return = decision.decision_trace_json.get("expected_return_bp")
            risk_score = decision.decision_trace_json.get("risk_score")

        if expected_return is None or risk_score is None or decision.p_take is None:
            logger.error(f"EXECUTE decision missing required fields, skipping persistence: {candidate.ticker}")
            decision.decision = "SKIP"
            decision.reason = "Missing required signal fields"
            return

        try:
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

    async def _update_decision_status(self, decision_id: str, status: str) -> None:
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
