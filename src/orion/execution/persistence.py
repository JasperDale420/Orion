"""Database persistence for execution records (orders, fills, trade journal)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from orion.shared.db_utils import db_query, db_write
from orion.shared.decorators import db_retry
from orion.shared.logger import setup_struct_logger
from orion.storage.db import async_session_factory
from orion.storage.models_gold import CandidateTrade, StrategyDecision

logger = setup_struct_logger(__name__)


def _coerce_timestamp(value: Any) -> datetime | None:
    """Coerce a Gateway timestamp (ISO-8601 string or datetime) to a timezone-aware datetime.

    Gateway-returned orders have `filled_at` as an ISO-8601 string (e.g.
    "2026-05-20T15:00:53.956207Z"), but the `fills.filled_at_utc` column is
    TIMESTAMP WITH TIME ZONE and asyncpg requires a datetime instance. Without
    this coercion every Gateway-fed persist_fill_record call raises a DataError
    that the outer try/except swallows — silently losing all fill rows.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "Could not parse timestamp",
                extra={"event_type": "TIMESTAMP_PARSE_ERROR", "value": s},
            )
            return None
    return None


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

            journal_decision_id = decision.decision_id or str(uuid.uuid4())
            await session.merge(
                TradeJournalEntry(
                    decision_id=journal_decision_id,
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
async def persist_order_status_update(
    *,
    broker_order_id: str,
    status: str,
    filled_qty: float | None = None,
    filled_avg_price: float | None = None,
) -> int:
    """Update OrderRecord.status keyed on broker_order_id.

    Returns row count updated (0 if no matching order — silent success;
    upstream code is responsible for logging unmatched ids if it cares).

    `filled_qty` and `filled_avg_price` are accepted for API symmetry with
    the broker payload but are not persisted onto OrderRecord directly — the
    `fills` table is the source of truth for fill quantities/prices.
    """
    from orion.storage.models_execution import OrderRecord

    async def update_status(session: Any) -> int:
        stmt = update(OrderRecord).where(OrderRecord.broker_order_id == broker_order_id).values(status=status)
        result = await session.execute(stmt)
        return int(result.rowcount or 0)

    try:
        return await db_write(update_status)
    except Exception as e:
        logger.error(
            "Failed to update order status",
            extra={
                "event_type": "ORDER_STATUS_UPDATE_ERROR",
                "broker_order_id": broker_order_id,
                "status": status,
                "error": str(e),
            },
        )
        return 0


@db_retry
async def persist_fill_record(fill: Any) -> None:
    """Persist a fill record and update the trade journal."""

    async def save_fill_and_update_journal(session: Any) -> None:
        from orion.storage.models_execution import FillRecord

        if isinstance(fill, dict):
            broker_order_id = str(fill.get("id", ""))
            ticker = fill.get("symbol", "")
            client_oid = fill.get("client_order_id")
            qty = float(fill.get("filled_qty", 0) or 0)
            price = float(fill.get("filled_avg_price", 0) or 0)
            side = fill.get("side") or None
            filled_at = _coerce_timestamp(fill.get("filled_at") or fill.get("filled_at_utc"))
            raw = fill
        else:
            broker_order_id = str(getattr(fill, "id", ""))
            ticker = getattr(fill, "symbol", None) or ""
            client_oid = getattr(fill, "client_order_id", None)
            qty = float(getattr(fill, "filled_qty", 0) or 0)
            price = float(getattr(fill, "filled_avg_price", 0) or 0)
            side = str(getattr(fill, "side", "")) or None
            filled_at = _coerce_timestamp(getattr(fill, "filled_at", None) or getattr(fill, "filled_at_utc", None))
            raw = fill.model_dump(mode="json") if hasattr(fill, "model_dump") else {}

        stmt = select(FillRecord).where(FillRecord.broker_order_id == broker_order_id)
        existing = (await session.execute(stmt)).scalars().first()
        if existing:
            existing.ticker = ticker
            existing.client_order_id = str(client_oid) if client_oid else None
            existing.filled_qty = qty
            existing.filled_avg_price = price or None
            existing.side = side
            existing.filled_at_utc = filled_at
            existing.raw_json = raw
        else:
            session.add(
                FillRecord(
                    id=str(uuid.uuid4()),
                    ticker=ticker,
                    broker_order_id=broker_order_id,
                    client_order_id=str(client_oid) if client_oid else None,
                    filled_qty=qty,
                    filled_avg_price=price or None,
                    side=side,
                    filled_at_utc=filled_at,
                    raw_json=raw,
                )
            )

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


# ── Phase 3 of exit-pipeline RCA: persist running-window position stats ──


async def upsert_position_running_stats(
    symbol: str,
    max_return_pct: float,
    max_drawdown_pct: float,
) -> None:
    """Upsert the per-symbol running-stats row.

    Called on every `sync_positions` tick that observes a new peak or
    trough for the symbol. Silently swallows errors — the live exit
    pipeline must keep running even if this side-write fails. The next
    tick retries with current values.
    """
    from orion.storage.models_execution import PositionRunningStats

    async def write(session: Any) -> None:
        existing = await session.get(PositionRunningStats, symbol)
        now = datetime.now(UTC)
        if existing is None:
            session.add(
                PositionRunningStats(
                    symbol=symbol,
                    max_return_pct=max_return_pct,
                    max_drawdown_pct=max_drawdown_pct,
                    last_updated_utc=now,
                )
            )
        else:
            existing.max_return_pct = max_return_pct
            existing.max_drawdown_pct = max_drawdown_pct
            existing.last_updated_utc = now

    try:
        await db_write(write)
    except Exception as exc:
        # Defensive: any DB blip must NOT break the position monitor
        # loop. Log at WARNING (not ERROR) so we can see drift without
        # alerting; the next sync_positions tick retries with the
        # in-memory peak/trough still intact.
        logger.warning(
            "Failed to upsert position_running_stats",
            extra={
                "event_type": "POSITION_RUNNING_STATS_UPSERT_FAILED",
                "symbol": symbol,
                "error": str(exc),
            },
        )


async def load_position_running_stats(symbol: str) -> tuple[float, float] | None:
    """Load persisted (max_return_pct, max_drawdown_pct) for symbol.

    Returns None when no row exists — caller falls back to the
    in-memory seeding (`max(0, unrealized_pnl_pct)`, etc.).
    """
    from orion.storage.models_execution import PositionRunningStats

    async def read(session: Any) -> Any:
        return await session.get(PositionRunningStats, symbol)

    try:
        row = await db_query(read)
    except Exception as exc:
        logger.warning(
            "Failed to load position_running_stats",
            extra={
                "event_type": "POSITION_RUNNING_STATS_LOAD_FAILED",
                "symbol": symbol,
                "error": str(exc),
            },
        )
        return None
    if row is None:
        return None
    return float(row.max_return_pct), float(row.max_drawdown_pct)
