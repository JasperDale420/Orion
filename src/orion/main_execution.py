import asyncio
import os
import re
import signal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select

from orion.clients.heber_reader import get_heber_reader
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
_PREFER_HEBER_FALSE_VALUES = {"0", "false", "no", "off", "n"}


def _should_apply_options_exit_rules(position: Any) -> bool:
    """Guard options-only exit policy from being applied to equity positions."""
    option_chain = getattr(position, "option_chain", None)
    if isinstance(option_chain, str):
        return bool(option_chain.strip())
    return bool(option_chain)


def _scope_recent_flow_for_position(position: Any, recent_flow: List[Any]) -> List[Any]:
    """
    Scope ticker-level recent flow to the tracked option contract when possible.

    This reduces cross-contract contamination for same-underlying positions.
    """
    if not _should_apply_options_exit_rules(position):
        return recent_flow

    position_chain = (getattr(position, "option_chain", None) or "").strip()
    if not position_chain:
        return recent_flow

    position_contract = _parse_option_chain_contract(position_chain)
    scoped = []
    for flow in recent_flow:
        flow_chain = (getattr(flow, "option_chain", None) or "").strip()
        if flow_chain == position_chain:
            scoped.append(flow)
            continue
        if (
            not flow_chain
            and position_contract is not None
            and _flow_matches_contract_components(flow, position_contract)
        ):
            scoped.append(flow)
    return scoped


def _parse_option_chain_contract(option_chain: str) -> Optional[Tuple[str, str, float]]:
    """
    Parse OCC option chain to comparable contract components.

    Returns tuple: (expiry_yyyy_mm_dd, put_call, strike_float)
    """
    match = re.search(r"(\d{6})([PC])(\d{8})$", option_chain.strip().upper())
    if not match:
        return None
    yymmdd, put_call, strike_raw = match.groups()
    expiry = f"20{yymmdd[0:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
    strike = int(strike_raw) / 1000.0
    return (expiry, put_call, strike)


def _flow_matches_contract_components(flow: Any, contract: Tuple[str, str, float]) -> bool:
    expiry, put_call, strike = contract
    flow_expiry = str(getattr(flow, "expiry", "") or "").strip()
    flow_put_call = str(getattr(flow, "put_call", "") or "").strip().upper()
    flow_strike_raw = getattr(flow, "strike", None)

    if not flow_expiry or not flow_put_call or flow_strike_raw is None:
        return False
    try:
        flow_strike = float(flow_strike_raw)
    except (TypeError, ValueError):
        return False

    return flow_expiry == expiry and flow_put_call == put_call and abs(flow_strike - strike) < 1e-6


def _prefer_heber_recent_flow_source() -> bool:
    raw = os.getenv("ORION_EXECUTION_PREFER_HEBER_RECENT_FLOW", "1").strip().lower()
    return raw not in _PREFER_HEBER_FALSE_VALUES


def _first_existing_column(df: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def _normalize_flow_ticker(value: Any) -> str | None:
    if value is None:
        return None
    ticker = str(value).strip().upper()
    if not ticker:
        return None
    if ":" in ticker:
        ticker = ticker.split(":")[-1]
    return ticker or None


def _normalize_put_call(value: Any) -> str:
    if value is None:
        return ""
    token = str(value).strip().upper()
    if token in {"C", "CALL"}:
        return "C"
    if token in {"P", "PUT"}:
        return "P"
    return token


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


async def _fetch_recent_flow_from_heber(ticker: str, minutes: int) -> List[Any] | None:
    reader = get_heber_reader()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=minutes)
    try:
        frame = await asyncio.to_thread(
            reader.read_flow,
            symbols=[ticker],
            asof_time=now,
            start_time=cutoff,
        )
    except Exception as exc:
        logger.warning(f"Heber recent flow read failed for {ticker}: {exc}")
        return None

    if frame.empty:
        return []

    ticker_col = _first_existing_column(frame, ("ticker", "symbol", "instrument_key"))
    ts_col = _first_existing_column(frame, ("flow_ts_utc", "ts_event", "timestamp"))
    premium_col = _first_existing_column(frame, ("premium_usd", "premium"))
    if ticker_col is None or ts_col is None or premium_col is None:
        return []

    put_call_col = _first_existing_column(frame, ("put_call", "call_put"))
    aggressor_col = _first_existing_column(frame, ("aggressor", "aggressor_ind", "side"))
    sweep_col = _first_existing_column(frame, ("is_sweep", "sweep"))
    option_chain_col = _first_existing_column(frame, ("option_chain", "option_symbol"))
    expiry_col = _first_existing_column(frame, ("expiry",))
    strike_col = _first_existing_column(frame, ("strike",))
    underlying_col = _first_existing_column(frame, ("underlying_price", "underlying"))
    event_id_col = _first_existing_column(frame, ("event_id", "id"))

    work = frame.copy()
    work["_event_ts"] = pd.to_datetime(work[ts_col], utc=True, errors="coerce")
    work = work.dropna(subset=["_event_ts"]).sort_values("_event_ts", ascending=False).head(100)

    ticker_upper = ticker.upper()
    rows: List[Any] = []
    for idx, row in work.iterrows():
        flow_ticker = _normalize_flow_ticker(row.get(ticker_col))
        if flow_ticker != ticker_upper:
            continue

        premium = pd.to_numeric(row.get(premium_col), errors="coerce")
        if pd.isna(premium):
            continue

        underlying_price = None
        if underlying_col is not None:
            underlying_price_value = pd.to_numeric(row.get(underlying_col), errors="coerce")
            if not pd.isna(underlying_price_value):
                underlying_price = float(underlying_price_value)

        strike = None
        if strike_col is not None:
            strike_value = pd.to_numeric(row.get(strike_col), errors="coerce")
            if not pd.isna(strike_value):
                strike = float(strike_value)

        option_chain = str(row.get(option_chain_col)).strip() if option_chain_col and row.get(option_chain_col) else ""
        event_id = str(row.get(event_id_col)).strip() if event_id_col and row.get(event_id_col) else f"heber_flow_{idx}"

        rows.append(
            SimpleNamespace(
                event_id=event_id,
                ticker=flow_ticker,
                flow_ts_utc=row["_event_ts"].to_pydatetime(),
                premium_usd=float(premium),
                put_call=_normalize_put_call(row.get(put_call_col)) if put_call_col else "",
                aggressor=str(row.get(aggressor_col)).strip().upper()
                if aggressor_col and row.get(aggressor_col)
                else "",
                is_sweep=_coerce_bool(row.get(sweep_col)) if sweep_col else False,
                underlying_price=underlying_price,
                option_chain=option_chain,
                expiry=row.get(expiry_col) if expiry_col else None,
                strike=strike,
            )
        )
    return rows


async def fetch_recent_flow_for_ticker(ticker: str, minutes: int = 30) -> List[Any]:
    """Fetch recent flow data for a ticker for exit rule evaluation."""
    if _prefer_heber_recent_flow_source():
        heber_rows = await _fetch_recent_flow_from_heber(ticker=ticker, minutes=minutes)
        if heber_rows:
            return heber_rows

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
                scoped_flow = _scope_recent_flow_for_position(position, recent_flow)

                for rule in exit_rules:
                    exit_sig = rule.should_exit(position, scoped_flow, context={})
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
