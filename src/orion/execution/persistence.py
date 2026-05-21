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


# Status sentinel for a row written BEFORE the broker round-trip. A startup
# reconciler queries by this status to find orders that may have landed on the
# broker without a matching DB finalize (process killed mid-Gateway-call).
PENDING_SUBMIT_STATUS = "PENDING_SUBMIT"
# Status sentinel for finalize when the Gateway call raised. Distinct from
# broker-side rejection statuses (e.g., 'rejected') so reconciler logic can
# tell "we never heard back" from "broker said no".
REJECTED_STATUS = "REJECTED"


@db_retry
async def persist_pending_order(
    *,
    decision: StrategyDecision,
    candidate: CandidateTrade,
    client_order_id: str,
    side: str,
    qty: float,
    limit_price: float | None,
) -> None:
    """Persist a PENDING_SUBMIT order row BEFORE the broker round-trip.

    Two-phase persistence (RCA 2026-05-21): the previous flow wrote the
    `orders` row only after `client.create_order()` returned, so a crash in
    that window (lease-conflict crash-loop, SIGTERM, asyncio cancel) left
    the broker holding the order with zero matching DB row. Writing here
    guarantees a durable tracking row exists before any network call, so a
    startup reconciler can resolve PENDING_SUBMIT rows against the Gateway
    by ``client_order_id``.

    Call ``persist_order_finalize`` after the Gateway returns to fill in
    ``broker_order_id`` + ``status`` (or ``REJECTED`` + error_message).
    """

    async def save_pending(session: Any) -> None:
        from orion.storage.models_execution import OrderRecord
        from orion.storage.models_trade_journal import TradeJournalEntry

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
                broker_order_id=None,
                status=PENDING_SUBMIT_STATUS,
                error_message=None,
                raw_json={},
            )
        )

        # PRD SS12.4: journal entry pre-write — broker_order_id filled in by finalize.
        try:
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
                    broker_order_id=None,
                    raw_json={"order_status": PENDING_SUBMIT_STATUS},
                )
            )
        except Exception as e:
            logger.error(
                "Failed to upsert trade journal on pending order persist",
                extra={"event_type": "TRADE_JOURNAL_UPSERT_ERROR", "error": str(e)},
            )

    try:
        async with async_session_factory() as session:
            await save_pending(session)
            await session.commit()
    except Exception as e:
        logger.error(
            "Failed to persist pending order",
            extra={
                "event_type": "PENDING_ORDER_PERSIST_ERROR",
                "client_order_id": client_order_id,
                "error": str(e),
            },
        )


@db_retry
async def persist_order_finalize(
    *,
    client_order_id: str,
    broker_order: Any | None,
    error_message: str | None,
) -> None:
    """Finalize the PENDING_SUBMIT row by ``client_order_id`` after Gateway returns.

    On success: stamps ``broker_order_id`` + broker-reported ``status`` +
    ``raw_json`` onto the existing row.

    On failure (broker_order is None, error_message set): stamps
    ``status='REJECTED'`` + ``error_message`` so reconciler can distinguish
    "never submitted" from "broker said no" without scanning logs.
    """
    from orion.storage.models_execution import OrderRecord
    from orion.storage.models_trade_journal import TradeJournalEntry

    broker_order_id: str | None = None
    status: str | None = None
    raw: Any = {}
    if broker_order is not None:
        if isinstance(broker_order, dict):
            broker_order_id = str(broker_order.get("id", "")) or None
            status = str(broker_order.get("status", "")) or None
            raw = broker_order
        else:
            broker_order_id = str(getattr(broker_order, "id", None) or "") or None
            status = str(getattr(broker_order, "status", None) or "") or None
            raw = broker_order.model_dump(mode="json") if hasattr(broker_order, "model_dump") else {}

    if status is None and error_message is not None:
        status = REJECTED_STATUS

    async def update_row(session: Any) -> int:
        order_stmt = (
            update(OrderRecord)
            .where(OrderRecord.client_order_id == client_order_id)
            .values(
                broker_order_id=broker_order_id,
                status=status,
                error_message=error_message,
                raw_json=raw,
            )
        )
        result = await session.execute(order_stmt)
        rowcount = int(result.rowcount or 0)

        # Mirror the OrderRecord update onto the trade journal so downstream
        # joins on broker_order_id still resolve.
        journal_stmt = (
            update(TradeJournalEntry)
            .where(TradeJournalEntry.client_order_id == client_order_id)
            .values(
                broker_order_id=broker_order_id,
                raw_json={"order_status": status, "order_error": error_message},
            )
        )
        await session.execute(journal_stmt)
        return rowcount

    try:
        rowcount = await db_write(update_row)
        if rowcount == 0:
            # No PENDING_SUBMIT row matched — either persist_pending_order failed
            # earlier (logged at that site) or someone called finalize without a
            # prior pending write. Surface so the reconciler doesn't silently miss it.
            logger.warning(
                "Order finalize matched 0 rows by client_order_id",
                extra={
                    "event_type": "ORDER_FINALIZE_NO_PENDING_ROW",
                    "client_order_id": client_order_id,
                    "status": status,
                    "has_broker_order_id": broker_order_id is not None,
                },
            )
    except Exception as e:
        logger.error(
            "Failed to finalize order",
            extra={
                "event_type": "ORDER_FINALIZE_ERROR",
                "client_order_id": client_order_id,
                "error": str(e),
            },
        )


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
