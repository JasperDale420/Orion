import asyncio
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from orion.config import system_settings
from orion.core.enums import DecisionAction, DecisionStatus
from orion.execution.execution_engine import ExecutionEngine
from orion.execution.flow_helpers import (
    _scope_recent_flow_for_position,
    _should_apply_options_exit_rules,
    fetch_recent_flow_for_ticker,
)
from orion.execution.decision_persistence import (
    auto_skip_stale_candidates,
    fetch_pending_candidates,
    reconcile_orphaned_decisions,
    save_decision,
    update_decision_status,
)
from orion.processing.signal_engine import SignalEngine
from orion.shared.async_main import run_service
from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
from orion.jobs.seed_solvers import ensure_active_solvers_ready
from orion.storage.db import init_db, wait_for_db

# Configure Logger
logger = setup_struct_logger("orion.execution")

# Re-export for backward compatibility (tests import from here)
from orion.execution.flow_helpers import (  # noqa: E402, F401
    _coerce_bool,
    _fetch_recent_flow_from_heber,
    _flow_matches_contract_components,
    _normalize_flow_ticker,
    _normalize_put_call,
    _parse_option_chain_contract,
    _prefer_heber_recent_flow_source,
)
from orion.clients.heber_reader import get_heber_reader  # noqa: E402, F401


async def run_execution_service(shutdown_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    logger.info("Starting Orion Execution Service (V1 Deterministic)...")

    # Ensure DB and solver inventory exist before the execution loop starts.
    # Wait out a transient DB outage (bounded) so a brief TimescaleDB blip
    # doesn't crash-loop the service on the launchd 30s throttle. Pass the
    # shutdown_event so a SIGTERM/SIGINT during the wait aborts startup promptly
    # instead of blocking for the full backoff.
    await wait_for_db(cancel_event=shutdown_event)
    if shutdown_event.is_set():
        logger.info("Shutdown requested during DB wait; exiting before startup.")
        return
    await init_db()

    # Repair decisions orphaned by a crash in the finalize→status-update gap:
    # an order finalized at the broker (accepted/REJECTED) whose StrategyDecision
    # is still PENDING. Best-effort — a transient DB error here must not block
    # startup of the execution loop.
    try:
        await reconcile_orphaned_decisions()
    except Exception as e:
        logger.warning(
            "reconcile_orphaned_decisions_failed",
            extra={"event_type": "ORPHAN_RECONCILE_FAILED", "error": str(e)},
        )

    solver_inventory = await ensure_active_solvers_ready(system_settings.orion_stage)
    logger.info(
        "Solver inventory ready",
        active_solver_count=solver_inventory.active_solver_count,
        seeded=solver_inventory.seeded,
        baseline_solver_id=solver_inventory.baseline_solver_id,
    )

    # 1. Initialize Engines
    signal_engine = SignalEngine()
    execution_engine = ExecutionEngine()

    # Initialize Position Manager and Exit Rules
    from orion.execution.position_manager import PositionManager
    from orion.processing.rules.exit_rules import get_default_exit_rules

    position_manager = PositionManager()
    exit_rules = get_default_exit_rules()

    # Refuse to start if another `execution` instance is already running —
    # the architecture's in-memory state (pending_orders, processed_fill_ids,
    # _partial_fill_tracker, _closing_symbols) assumes a single process per
    # service. A stale lease (>120s without renewal) is treated as a crashed
    # prior run and overwritten.
    await execution_engine.acquire_service_lease("execution")

    # Initialize history for execution error tracking
    await execution_engine.initialize()
    await signal_engine.initialize()
    await position_manager.initialize()

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
            #    Sweep stale candidates first so the pending pool doesn't
            #    accumulate forever-pending rows that fetch_pending_candidates
            #    silently filters out. Best-effort: a transient DB error here
            #    must not break the loop.
            try:
                await auto_skip_stale_candidates()
            except Exception as e:
                logger.warning(
                    "auto_skip_stale_candidates_failed",
                    extra={"event_type": "AUTO_SKIP_FAILED", "error": str(e)},
                )

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
                if decision.decision == DecisionAction.EXECUTE:
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
                        decision.decision = DecisionAction.SKIP
                        decision.executed_successfully = DecisionStatus.SKIPPED
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
                exec_status = DecisionStatus.SKIPPED
                if decision.decision == DecisionAction.EXECUTE:
                    try:
                        await execution_engine.execute_order(decision, candidate)

                        if decision.executed_successfully == DecisionStatus.TRUE:
                            exec_status = DecisionStatus.TRUE
                        else:
                            exec_status = DecisionStatus.FALSE

                    except Exception as exe:
                        logger.error(f"Execution Exception: {exe}")
                        exec_status = DecisionStatus.FALSE

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

                # Guard: skip if a close order is already in progress for this symbol
                if position_manager.is_closing(position.ticker):
                    logger.info(
                        f"Rule-based exit skipped: {position.ticker} already has a close in progress",
                        extra={"event_type": "EXIT_RULE_DUPLICATE_BLOCKED", "ticker": position.ticker},
                    )
                    continue

                # Fetch recent flow for this ticker (last 30 min)
                recent_flow = await fetch_recent_flow_for_ticker(position.ticker, minutes=30)
                scoped_flow = _scope_recent_flow_for_position(position, recent_flow)

                for rule in exit_rules:
                    exit_sig = rule.should_exit(position, scoped_flow, context={})
                    if exit_sig:
                        logger.info(
                            f"Exit signal triggered: {position.ticker} - {exit_sig.rule_id}: {exit_sig.reason}",
                            extra={"event_type": "EXIT_SIGNAL", "ticker": position.ticker, "rule_id": exit_sig.rule_id},
                        )
                        if exit_sig.urgency == "IMMEDIATE":
                            if not position_manager.mark_closing(position.ticker):
                                break  # Another close already in progress

                            try:
                                closed = await execution_engine.close_position(
                                    ticker=position.ticker,
                                    qty=position.qty,
                                    exit_signal=exit_sig,
                                    direction=position.direction,
                                )
                                if closed:
                                    position_manager.remove_position(position.candidate_id)
                            finally:
                                position_manager.unmark_closing(position.ticker)
                            break  # Exit on first immediate signal
        except Exception as exit_err:
            logger.error(f"Exit rule evaluation error: {exit_err}")

        elapsed = loop.time() - start_time
        sleep_time = max(0.1, 1.0 - elapsed)

        # Smart Sleep: Wait for sleep_time OR shutdown_event
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_time)
            break  # Shutdown set
        except TimeoutError:
            pass  # Sleep done, continue loop

    if shutdown_event.is_set():
        logger.info("Execution Service Stopped.")
    else:
        # Defensive: while-loop should only exit via shutdown_event. If we
        # land here without that flag set, something silently broke the
        # loop condition — surface it as CRITICAL so the operator sees a
        # signal instead of a "clean" exit code masquerading as healthy.
        logger.critical(
            "execution_main_loop_exited_without_shutdown_signal",
            extra={"event_type": "MAIN_LOOP_UNEXPECTED_EXIT"},
        )


async def main() -> None:
    """In-loop entry point: install signal handlers and run the execution loop.

    The ``__main__`` path uses ``run_service`` for the same plumbing plus
    process-level crash logging. This coroutine exists for callers that already
    own an event loop and drive the service directly.
    """
    import signal

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received. Stopping execution loop...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await run_execution_service(shutdown_event)


if __name__ == "__main__":
    # run_service owns signal handlers (set the shutdown event), silent Ctrl-C
    # exit, and structured crash logging with a non-zero exit code so docker
    # restart_policy correctly reports failure (was previously ec=0 in some
    # restart-loop incidents when the loop returned silently). init_database
    # is False because run_execution_service runs wait_for_db() + init_db()
    # itself, gated on the shutdown event.
    run_service("orion.execution", run_execution_service, init_database=False)
