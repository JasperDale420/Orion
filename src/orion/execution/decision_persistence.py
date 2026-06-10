"""Decision persistence for the execution service.

Handles fetching pending candidates, saving strategy decisions,
and persisting signals_live / trade journal entries.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from orion.config import system_settings
from orion.core.enums import DecisionAction, DecisionStatus
from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.models_gold import CandidateTrade, StrategyDecision
from orion.storage.models_signals import SignalLive
from orion.storage.models_trade_journal import TradeJournalEntry

logger = setup_struct_logger("orion.execution.decision_persistence")


async def fetch_pending_candidates(limit: int = 100) -> list[CandidateTrade]:
    """Fetches CandidateTrades that have NOT been processed (no matching StrategyDecision).

    Candidates older than `max_data_lag_seconds` are excluded — they would be
    rejected by the preflight Data-Lag gate anyway, and processing them
    burns full ML-scoring cycles on doomed work, starving fresh candidates
    of execution time. With 60s ingestion cycles and ~20-30s per candidate
    in the ML+preflight chain, even a small backlog of stale candidates
    pushes fresh ones out of reach forever — observed live as 21
    consecutive Data-Lag SKIPs with growing lag (1957s → 3414s) and a
    pending pool of 115/157.
    """
    freshness_cutoff = datetime.now(UTC) - timedelta(seconds=float(system_settings.max_data_lag_seconds))

    async def query_candidates(session: Any) -> None:
        stmt = (
            select(CandidateTrade)
            .outerjoin(StrategyDecision, CandidateTrade.candidate_id == StrategyDecision.candidate_id)
            .where(StrategyDecision.candidate_id.is_(None))
            .where(CandidateTrade.timestamp_utc > freshness_cutoff)
            .order_by(CandidateTrade.timestamp_utc.asc())  # FIFO over the fresh set
            .limit(limit)
        )

        result = await session.execute(stmt)
        return result.scalars().all()

    return await db_query(query_candidates)


async def auto_skip_stale_candidates(batch_limit: int = 500) -> int:
    """Insert SKIP decisions for candidates older than `max_data_lag_seconds`
    that have no decision yet.

    Without this, fetch_pending_candidates' freshness filter leaves stale
    candidates as forever-pending rows: invisible to the execution loop
    but still counted in any "pending" tally and growing unbounded over
    time. This batch sweep gives them a proper SKIP decision row keyed
    by candidate_id (matching the FK relationship downstream tooling
    expects) so the table stays self-clean. Idempotent — once a stale
    candidate has a decision row it's filtered by the same outer-join
    on subsequent runs.
    """
    freshness_cutoff = datetime.now(UTC) - timedelta(seconds=float(system_settings.max_data_lag_seconds))
    now = datetime.now(UTC)

    async def insert_skips(session: Any) -> int:
        stmt = (
            select(CandidateTrade)
            .outerjoin(StrategyDecision, CandidateTrade.candidate_id == StrategyDecision.candidate_id)
            .where(StrategyDecision.candidate_id.is_(None))
            .where(CandidateTrade.timestamp_utc < freshness_cutoff)
            .limit(batch_limit)
        )
        result = await session.execute(stmt)
        stale = result.scalars().all()
        for c in stale:
            session.add(
                StrategyDecision(
                    decision_id=str(uuid.uuid4()),
                    candidate_id=c.candidate_id,
                    ticker=c.ticker,
                    strategy_version_id="auto_stale_skip",
                    decision=DecisionAction.SKIP.value,
                    reason="Stale at fetch: older than max_data_lag_seconds",
                    executed_successfully="SKIPPED",
                    timestamp_utc=now,
                )
            )
        return len(stale)

    swept = await db_write(insert_skips)
    if swept:
        logger.info("auto_skipped_stale_candidates", extra={"swept": swept})
    return swept


async def save_decision(decision: StrategyDecision, candidate: CandidateTrade) -> None:
    """Persist a strategy decision and associated signal/journal records."""
    # PRDv2 §11.2: EXECUTE decisions must carry expected_return, p_take, risk_score.
    if decision.decision == DecisionAction.EXECUTE:
        expected_return = None
        risk_score = None
        if isinstance(decision.decision_trace_json, dict):
            expected_return = decision.decision_trace_json.get("expected_return_bp")
            risk_score = decision.decision_trace_json.get("risk_score")
        if expected_return is None or risk_score is None or decision.p_take is None:
            decision.decision = DecisionAction.SKIP
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
            journal_decision_id = decision.decision_id or str(uuid.uuid4())
            session.add(
                TradeJournalEntry(
                    decision_id=journal_decision_id,
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


# OrderRecord.status value written before the broker round-trip. Any other
# non-NULL status means the order has reached a broker-terminal state.
_PENDING_SUBMIT_STATUS = "PENDING_SUBMIT"

# Broker statuses that mean the order reached the broker as a live or filled
# order — the decision did execute successfully. Lowercased for comparison.
_SUCCESS_BROKER_STATUSES = frozenset(
    {
        "filled",
        "partially_filled",
        "new",
        "accepted",
        "pending_new",
        "held",
        "accepted_for_bidding",
        "done_for_day",
        "calculated",
    }
)
# Broker statuses that mean the order did NOT result in a working/filled order.
_FAILED_BROKER_STATUSES = frozenset(
    {"rejected", "canceled", "cancelled", "expired", "suspended", "stopped", "replaced"}
)


async def reconcile_orphaned_decisions() -> int:
    """Repair decisions orphaned in the finalize→status-update gap at startup.

    ``_submit_options_order`` finalizes the OrderRecord (status + broker_order_id)
    *before* main_execution.py persists the decision's terminal status via
    ``update_decision_status``. A crash in that window leaves an order live at
    the broker (status='accepted'/'REJECTED') while its StrategyDecision is
    stuck at ``PENDING`` — invisible to reconciliation and unprocessed-looking.

    Find every order in a broker-terminal state (status set and not
    ``PENDING_SUBMIT``) whose linked decision is still ``PENDING``, and mirror
    the order's *final disposition* onto the decision: a live/filled order
    (``_SUCCESS_BROKER_STATUSES``) → ``TRUE``; a rejected/canceled/expired order
    (``_FAILED_BROKER_STATUSES``) → ``FALSE``. An unrecognised status is left
    untouched (logged) rather than guessed — we never assert success for an
    order we can't classify. Each repair is logged. Returns the count.
    """
    from orion.storage.models_execution import OrderRecord

    async def repair(session: Any) -> int:
        stmt = (
            select(OrderRecord.decision_id, OrderRecord.status, OrderRecord.client_order_id)
            .join(StrategyDecision, OrderRecord.decision_id == StrategyDecision.decision_id)
            .where(OrderRecord.decision_id.is_not(None))
            .where(OrderRecord.status.is_not(None))
            .where(OrderRecord.status != _PENDING_SUBMIT_STATUS)
            .where(StrategyDecision.executed_successfully == DecisionStatus.PENDING)
        )
        rows = (await session.execute(stmt)).all()

        repaired = 0
        for decision_id, order_status, client_order_id in rows:
            normalized = str(order_status).strip().lower()
            if normalized in _SUCCESS_BROKER_STATUSES:
                new_status = DecisionStatus.TRUE
            elif normalized in _FAILED_BROKER_STATUSES:
                new_status = DecisionStatus.FALSE
            else:
                # Unknown terminal status — don't guess success or failure.
                logger.warning(
                    "orphaned_decision_unknown_status",
                    extra={
                        "event_type": "ORPHANED_DECISION_UNKNOWN_STATUS",
                        "decision_id": decision_id,
                        "client_order_id": client_order_id,
                        "order_status": order_status,
                    },
                )
                continue
            dec_stmt = select(StrategyDecision).where(StrategyDecision.decision_id == decision_id)
            record = (await session.execute(dec_stmt)).scalars().first()
            if record is None:
                continue
            record.executed_successfully = new_status
            repaired += 1
            logger.info(
                "reconciled_orphaned_decision",
                extra={
                    "event_type": "ORPHANED_DECISION_RECONCILED",
                    "decision_id": decision_id,
                    "client_order_id": client_order_id,
                    "order_status": order_status,
                    "decision_status": str(new_status),
                },
            )
        return repaired

    repaired = await db_write(repair)
    if repaired:
        logger.warning(
            "orphaned_decisions_reconciled",
            extra={"event_type": "ORPHANED_DECISIONS_RECONCILED_TOTAL", "count": repaired},
        )
    return repaired
