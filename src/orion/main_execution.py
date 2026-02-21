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


def _nullable_float(value: Any) -> Optional[float]:
    """Coerce a value to float or return None if invalid/NaN."""
    numeric = pd.to_numeric(value, errors="coerce")
    return float(numeric) if not pd.isna(numeric) else None


def _optional_string(row: Any, col: Optional[str], *, upper: bool = False, default: str = "") -> str:
    """Extract a string from a row column, returning default if the column is absent or empty."""
    if col is None:
        return default
    raw = row.get(col)
    if not raw:
        return default
    result = str(raw).strip()
    return result.upper() if upper else result


def _row_to_flow_namespace(row: Any, idx: Any, col_map: dict[str, Optional[str]]) -> Optional[SimpleNamespace]:
    """Convert a raw DataFrame row to a SimpleNamespace flow object, filtering bad rows."""
    flow_ticker = _normalize_flow_ticker(row.get(col_map["ticker"]))
    if flow_ticker != col_map["_ticker_upper"]:
        return None

    premium = _nullable_float(row.get(col_map["premium"]))
    if premium is None:
        return None

    event_id = _optional_string(row, col_map["event_id"]) or f"heber_flow_{idx}"
    option_chain = _optional_string(row, col_map["option_chain"])

    return SimpleNamespace(
        event_id=event_id,
        ticker=flow_ticker,
        flow_ts_utc=row["_event_ts"].to_pydatetime(),
        premium_usd=premium,
        put_call=_normalize_put_call(row.get(col_map["put_call"])) if col_map["put_call"] else "",
        aggressor=_optional_string(row, col_map["aggressor"], upper=True),
        is_sweep=_coerce_bool(row.get(col_map["sweep"])) if col_map["sweep"] else False,
        underlying_price=_nullable_float(row.get(col_map["underlying"])) if col_map["underlying"] else None,
        option_chain=option_chain,
        expiry=row.get(col_map["expiry"]) if col_map["expiry"] else None,
        strike=_nullable_float(row.get(col_map["strike"])) if col_map["strike"] else None,
    )


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

    col_map: dict[str, Optional[str]] = {
        "ticker": ticker_col,
        "premium": premium_col,
        "put_call": _first_existing_column(frame, ("put_call", "call_put")),
        "aggressor": _first_existing_column(frame, ("aggressor", "aggressor_ind", "side")),
        "sweep": _first_existing_column(frame, ("is_sweep", "sweep")),
        "option_chain": _first_existing_column(frame, ("option_chain", "option_symbol")),
        "expiry": _first_existing_column(frame, ("expiry",)),
        "strike": _first_existing_column(frame, ("strike",)),
        "underlying": _first_existing_column(frame, ("underlying_price", "underlying")),
        "event_id": _first_existing_column(frame, ("event_id", "id")),
        "_ticker_upper": ticker.upper(),
    }

    work = frame.copy()
    work["_event_ts"] = pd.to_datetime(work[ts_col], utc=True, errors="coerce")
    work = work.dropna(subset=["_event_ts"]).sort_values("_event_ts", ascending=False).head(100)

    rows: List[Any] = []
    for idx, row in work.iterrows():
        ns = _row_to_flow_namespace(row, idx, col_map)
        if ns is not None:
            rows.append(ns)
    return rows


async def fetch_recent_flow_for_ticker(ticker: str, minutes: int = 30) -> List[Any]:
    """Fetch recent flow data for a ticker for exit rule evaluation."""
    if _prefer_heber_recent_flow_source():
        heber_rows = await _fetch_recent_flow_from_heber(ticker=ticker, minutes=minutes)
        if heber_rows is None:
            return []
        return heber_rows

    logger.info(
        "Recent flow read skipped because Heber source is disabled",
        extra={"event_type": "RECENT_FLOW_HEBER_DISABLED", "ticker": ticker, "minutes": minutes},
    )
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


def _extract_signal_fields(
    decision: StrategyDecision,
) -> Tuple[Optional[float], Optional[float]]:
    """Extract expected_return and risk_score from decision trace."""
    if not isinstance(decision.decision_trace_json, dict):
        return None, None
    return (
        decision.decision_trace_json.get("expected_return_bp"),
        decision.decision_trace_json.get("risk_score"),
    )


def _validate_execute_fields(
    expected_return: Optional[float],
    risk_score: Optional[float],
    p_take: Optional[float],
) -> bool:
    """Return True when all required EXECUTE signal fields are present."""
    return expected_return is not None and risk_score is not None and p_take is not None


async def save_decision(decision: StrategyDecision, candidate: CandidateTrade) -> None:
    # PRDv2 §11.2: EXECUTE decisions must carry expected_return, p_take, risk_score (for signals_live).
    if decision.decision == "EXECUTE":
        er, rs = _extract_signal_fields(decision)
        if not _validate_execute_fields(er, rs, decision.p_take):
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
        expected_return, risk_score = _extract_signal_fields(decision)

        # Fail-fast for PRDv2 §11.2 required fields.
        if not _validate_execute_fields(expected_return, risk_score, decision.p_take):
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


async def update_decision_status(decision_id: str, status: str) -> None:
    async def update_status(session: Any) -> None:
        stmt = select(StrategyDecision).where(StrategyDecision.decision_id == decision_id)
        result = await session.execute(stmt)
        record = result.scalars().first()
        if record:
            record.executed_successfully = status

    await db_write(update_status)


async def _apply_preflight(
    decision: StrategyDecision,
    candidate: CandidateTrade,
    execution_engine: ExecutionEngine,
) -> None:
    """Run preflight checks on EXECUTE decisions; downgrade to SKIP if rejected."""
    if decision.decision != "EXECUTE":
        return

    from orion.execution.signal_preflight import preflight_live_signal

    async def _run_preflight(session: Any) -> Any:
        return await preflight_live_signal(
            session,
            candidate=candidate,
            decision=decision,
            risk_manager=execution_engine.risk_manager,
        )

    pre = await db_query(_run_preflight)
    decision.decision_trace_json = decision.decision_trace_json or {}
    if not pre.ok:
        decision.decision = "SKIP"
        decision.executed_successfully = "SKIPPED"
        decision.reason = f"Preflight reject: {pre.reason}"
        decision.decision_trace_json["preflight_reject"] = {"reason": pre.reason, **(pre.extra or {})}
    else:
        decision.decision_trace_json["rollups"] = (pre.extra or {}).get("rollups", {})
        decision.decision_trace_json["preflight"] = {k: v for k, v in (pre.extra or {}).items() if k != "rollups"}


async def _execute_and_persist(
    decision: StrategyDecision,
    candidate: CandidateTrade,
    execution_engine: ExecutionEngine,
) -> None:
    """Execute decision if EXECUTE, then persist status."""
    exec_status = "SKIPPED"
    if decision.decision == "EXECUTE":
        try:
            await execution_engine.execute_order(decision, candidate)
            exec_status = "TRUE" if decision.executed_successfully == "TRUE" else "FALSE"
        except Exception as exe:
            logger.error(f"Execution Exception: {exe}")
            exec_status = "FALSE"

    await update_decision_status(decision.decision_id, exec_status)


async def _process_candidates(
    candidates: List[Any],
    signal_engine: SignalEngine,
    execution_engine: ExecutionEngine,
) -> None:
    """Process a batch of pending candidates through policy, preflight, and execution."""
    logger.info(f"Processing {len(candidates)} new candidates...")
    for candidate in candidates:
        decision = await signal_engine.decide(candidate)
        await _apply_preflight(decision, candidate, execution_engine)
        await save_decision(decision, candidate)
        await _execute_and_persist(decision, candidate, execution_engine)


async def _evaluate_exit_rules(
    position_manager: Any,
    exit_rules: List[Any],
    execution_engine: ExecutionEngine,
) -> None:
    """Run exit rule evaluation for all open positions."""
    for position in position_manager.get_open_positions():
        if not _should_apply_options_exit_rules(position):
            logger.debug(
                f"Skipping options exit rules for non-option position: {position.ticker}",
                extra={"event_type": "EXIT_RULE_SKIP_NON_OPTION", "ticker": position.ticker},
            )
            continue

        recent_flow = await fetch_recent_flow_for_ticker(position.ticker, minutes=30)
        scoped_flow = _scope_recent_flow_for_position(position, recent_flow)

        for rule in exit_rules:
            exit_sig = rule.should_exit(position, scoped_flow, context={})
            if not exit_sig:
                continue
            logger.info(
                f"Exit signal triggered: {position.ticker} - {exit_sig.rule_id}: {exit_sig.reason}",
                extra={"event_type": "EXIT_SIGNAL", "ticker": position.ticker, "rule_id": exit_sig.rule_id},
            )
            if exit_sig.urgency == "IMMEDIATE":
                closed = await execution_engine.close_position(
                    ticker=position.ticker,
                    qty=position.qty,
                    exit_signal=exit_sig,
                    direction=position.direction,
                )
                if closed:
                    position_manager.remove_position(position.candidate_id)
                break


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

    from orion.execution.position_manager import PositionManager
    from orion.processing.rules.exit_rules import get_default_exit_rules

    position_manager = PositionManager()
    exit_rules = get_default_exit_rules()

    await execution_engine.initialize()
    await signal_engine.initialize()
    await position_manager.initialize()
    await init_db()

    logger.info("Engines Initialized. Entering Service Loop.")

    while not shutdown_event.is_set():
        start_time = loop.time()

        try:
            await execution_engine.poll_fills()

            from orion.core.circuit_breaker import CircuitBreaker

            cb = CircuitBreaker()
            if await cb.is_open():
                state = await cb.get_state()
                logger.warning(f"CIRCUIT BREAKER OPEN: {state.get('reason')}. Pausing execution.")
                await asyncio.sleep(5.0)
                continue

            candidates = await fetch_pending_candidates()
            if not candidates:
                await asyncio.sleep(1.0)
                continue

            await _process_candidates(candidates, signal_engine, execution_engine)

        except Exception as e:
            logger.error(f"Main Loop Error: {e}")
            await asyncio.sleep(5.0)

        try:
            await _evaluate_exit_rules(position_manager, exit_rules, execution_engine)
        except Exception as exit_err:
            logger.error(f"Exit rule evaluation error: {exit_err}")

        elapsed = loop.time() - start_time
        sleep_time = max(0.1, 1.0 - elapsed)

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_time)
            break
        except asyncio.TimeoutError:
            pass

    logger.info("Execution Service Stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
