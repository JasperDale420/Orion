"""Database persistence for execution records (orders, fills, trade journal)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from orion.shared.db_utils import db_query, db_write
from orion.shared.decorators import db_retry
from orion.shared.logger import setup_struct_logger
from orion.storage.db import async_session_factory
from orion.storage.models_gold import CandidateTrade, StrategyDecision

logger = setup_struct_logger(__name__)


async def is_fill_processed(order_id: str) -> bool:
    """Check if a fill has already been processed (idempotency guard)."""
    from orion.storage.models_risk import ProcessedFill

    try:

        async def check_fill(session: Any) -> bool:
            stmt = select(ProcessedFill).where(ProcessedFill.fill_id == order_id)
            result = await session.execute(stmt)
            row = result.scalars().first()
            if row is None:
                return False
            return str(getattr(row, "fill_id", "")) == str(order_id)

        return await db_query(check_fill)
    except Exception as e:
        logger.error(f"Failed to check processed fills for {order_id}: {e}")
        return False  # Fail safe — assume not processed to avoid skipping fills


@db_retry
async def mark_fill_processed(
    order_id: str, client_oid: str | None = None, ticker: str | None = None, qty: float | None = None
) -> None:
    """Mark a fill as processed (idempotency record)."""

    async def mark_fill(session: Any) -> None:
        from orion.storage.models_risk import ProcessedFill

        pf = ProcessedFill(
            fill_id=order_id,
            client_order_id=client_oid,
            ticker=ticker,
            qty=qty,
            processed_at_utc=datetime.now(UTC),
        )
        session.add(pf)

    try:
        await db_write(mark_fill)
    except Exception as e:
        logger.error(f"Failed to mark fill {order_id} as processed: {e}")


@db_retry
async def persist_order_record(
    *,
    decision: StrategyDecision,
    candidate: CandidateTrade,
    client_order_id: str,
    side: str,
    qty: float,
    limit_price: float | None,
    broker_order: Any | None,
    error_message: str | None,
) -> None:
    """Persist an order record and upsert the trade journal entry."""

    async def save_order_and_journal(session: Any) -> None:
        from orion.storage.models_execution import OrderRecord

        broker_order_id = None
        status = None
        raw = {}
        if broker_order is not None:
            if isinstance(broker_order, dict):
                broker_order_id = str(broker_order.get("id", "")) or None
                status = str(broker_order.get("status", "")) or None
                raw = broker_order
            else:
                broker_order_id = str(getattr(broker_order, "id", None) or "")
                status = str(getattr(broker_order, "status", None) or "")
                raw = broker_order.model_dump(mode="json") if hasattr(broker_order, "model_dump") else {}

        session.add(
            OrderRecord(
                id=str(uuid.uuid4()),
                decision_id=decision.decision_id,
                candidate_id=candidate.candidate_id,
                ticker=candidate.ticker,
                side=side,
                qty=float(qty),
                limit_price=float(limit_price) if limit_price is not None else None,
                client_order_id=client_order_id,
                broker_order_id=broker_order_id or None,
                status=status or None,
                error_message=error_message,
                raw_json=raw,
            )
        )

        # PRD SS12.4: Ensure a trade journal entry exists and links to order ids.
        try:
            from orion.storage.models_trade_journal import TradeJournalEntry

            await session.merge(
                TradeJournalEntry(
                    decision_id=decision.decision_id,
                    signal_id=f"sig_{candidate.candidate_id}",
                    candidate_id=candidate.candidate_id,
                    solver_id=decision.strategy_version_id,
                    ticker=candidate.ticker,
                    direction=candidate.direction,
                    evidence=candidate.evidence or {},
                    decision_trace_json=decision.decision_trace_json or {},
                    client_order_id=client_order_id,
                    broker_order_id=broker_order_id or None,
                    raw_json={"order_status": status or None, "order_error": error_message},
                )
            )
        except Exception as e:
            logger.error(
                "Failed to upsert trade journal on order persist",
                extra={"event_type": "TRADE_JOURNAL_UPSERT_ERROR", "error": str(e)},
            )

    try:
        async with async_session_factory() as session:
            await save_order_and_journal(session)
            await session.commit()
    except Exception as e:
        logger.error("Failed to persist order record", extra={"event_type": "ORDER_PERSIST_ERROR", "error": str(e)})


@db_retry
async def persist_fill_record(fill: Any) -> None:
    """Persist a fill record and update the trade journal."""

    async def save_fill_and_update_journal(session: Any) -> None:
        from sqlalchemy.dialects.postgresql import insert

        from orion.storage.models_execution import FillRecord

        if isinstance(fill, dict):
            broker_order_id = str(fill.get("id", ""))
            ticker = fill.get("symbol", "")
            client_oid = fill.get("client_order_id")
            qty = float(fill.get("filled_qty", 0) or 0)
            price = float(fill.get("filled_avg_price", 0) or 0)
            side = fill.get("side") or None
            filled_at = fill.get("filled_at") or fill.get("filled_at_utc")
            raw = fill
        else:
            broker_order_id = str(getattr(fill, "id", ""))
            ticker = getattr(fill, "symbol", None) or ""
            client_oid = getattr(fill, "client_order_id", None)
            qty = float(getattr(fill, "filled_qty", 0) or 0)
            price = float(getattr(fill, "filled_avg_price", 0) or 0)
            side = str(getattr(fill, "side", "")) or None
            filled_at = getattr(fill, "filled_at", None) or getattr(fill, "filled_at_utc", None)
            raw = fill.model_dump(mode="json") if hasattr(fill, "model_dump") else {}

        values = {
            "id": str(uuid.uuid4()),
            "ticker": ticker,
            "broker_order_id": broker_order_id,
            "client_order_id": str(client_oid) if client_oid else None,
            "filled_qty": qty,
            "filled_avg_price": price or None,
            "side": side,
            "filled_at_utc": filled_at,
            "raw_json": raw,
        }

        stmt = insert(FillRecord).values(values)
        stmt = stmt.on_conflict_do_nothing(index_elements=["broker_order_id"])
        await session.execute(stmt)

        # PRD SS12.4: Update trade journal fill pointers by broker_order_id.
        try:
            from orion.storage.models_trade_journal import TradeJournalEntry

            tj_stmt = select(TradeJournalEntry).where(TradeJournalEntry.broker_order_id == broker_order_id).limit(1)
            existing = (await session.execute(tj_stmt)).scalars().first()
            if existing:
                existing.filled_qty = qty
                existing.filled_avg_price = price or None
                existing.filled_at_utc = filled_at
        except Exception as e:
            logger.error(
                "Failed to update trade journal on fill persist",
                extra={"event_type": "TRADE_JOURNAL_FILL_UPDATE_ERROR", "error": str(e)},
            )

    try:
        await db_write(save_fill_and_update_journal)
    except Exception as e:
        logger.error("Failed to persist fill record", extra={"event_type": "FILL_PERSIST_ERROR", "error": str(e)})


async def persist_exit_decision(ticker: str, exit_signal: Any, client_order_id: str, order: Any) -> None:
    """Persist exit decision to database."""
    try:

        async def save_exit(session: Any) -> None:
            from orion.storage.models_gold import ExitDecision

            broker_order_id = None
            if isinstance(order, dict):
                broker_order_id = str(order.get("id", "")) or None
            elif order is not None:
                broker_order_id = str(getattr(order, "id", "")) if order else None

            session.add(
                ExitDecision(
                    exit_id=client_order_id,
                    ticker=ticker,
                    rule_id=exit_signal.rule_id,
                    exit_reason=exit_signal.reason,
                    urgency=exit_signal.urgency,
                    confidence=exit_signal.confidence,
                    details=exit_signal.details or {},
                    broker_order_id=broker_order_id,
                    exit_ts_utc=datetime.now(UTC),
                )
            )

        await db_write(save_exit)
    except Exception as e:
        logger.error(f"Failed to persist exit decision: {e}")
