"""Database persistence for execution records (orders, fills, trade journal)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update

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
        logger.error(f"Failed to check processed fills for {order_id}: {e}", exc_info=True)
        # Unknown is not "unprocessed": processing against an unavailable
        # idempotency ledger can double-count P&L after a restart.
        raise


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
        logger.error(f"Failed to mark fill {order_id} as processed: {e}", exc_info=True)
        raise


async def has_processed_fill_for_order(broker_order_id: str) -> bool:
    """True if ANY fill for this broker order_id has already been processed.

    The idempotency marker is ``f"{order_id}:{filled_qty}"`` (see
    ``FillProcessor.process_single_fill``), so a partial fill recorded earlier
    lives under a different marker than the order's final cumulative quantity.
    The missed-CLOSE recovery uses this to refuse re-feeding an order it has
    already counted ANY fill for — pushing the full order through the processor
    after a partial already landed would double-count the close (the
    entry-recovery path gets this guarantee for free by only ever touching
    pre-fill orders).
    """
    from orion.storage.models_risk import ProcessedFill

    try:

        async def check(session: Any) -> bool:
            # ponytail: broker order ids are UUIDs (no LIKE metacharacters), so
            # the unescaped "{id}:%" prefix match is safe.
            stmt = select(ProcessedFill.fill_id).where(ProcessedFill.fill_id.like(f"{broker_order_id}:%"))
            result = await session.execute(stmt)
            return result.first() is not None

        return await db_query(check)
    except Exception as e:
        # Fail SAFE toward "already processed": a missed lookup must skip the
        # recovery, never risk double-counting a close.
        logger.error(f"Failed to check processed fills for order {broker_order_id}: {e}")
        return True


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
        # Re-raise after logging. This is the load-bearing half of the
        # two-phase guarantee (RCA 2026-05-21): if the durable PENDING_SUBMIT
        # row cannot be written, the caller MUST NOT proceed to the broker —
        # otherwise we recreate the orphaned-order scenario this function
        # exists to prevent. The caller (_submit_options_order) wraps this
        # call and compensates (drops the pending reservation + intended
        # greeks, marks the decision failed) on raise. @db_retry has already
        # exhausted its attempts by the time we get here.
        logger.error(
            "Failed to persist pending order",
            extra={
                "event_type": "PENDING_ORDER_PERSIST_ERROR",
                "client_order_id": client_order_id,
                "error": str(e),
            },
        )
        raise


@db_retry
async def persist_exit_order_rejection(
    *,
    client_order_id: str,
    ticker: str,
    side: str,
    qty: float,
    limit_price: float | None,
    error_message: str,
) -> None:
    """Persist a REJECTED ``orders`` row for a CLOSE the broker refused.

    Close/exit failures previously only logged, so a position the system
    tried-and-failed to close was indistinguishable in the DB from one it
    simply held (RCA 2026-06-05: 5 MU wash-trade close rejections left ZERO
    rows in ``orders`` while the orders table showed only the two filled
    entries). Writing the rejection makes exit failures auditable alongside
    entry rejections. Best-effort — never raises into the close path.
    """

    async def save_rejection(session: Any) -> None:
        from orion.storage.models_execution import OrderRecord

        session.add(
            OrderRecord(
                id=str(uuid.uuid4()),
                decision_id=None,
                candidate_id=None,
                ticker=ticker,
                side=side,
                qty=float(qty),
                limit_price=float(limit_price) if limit_price is not None else None,
                client_order_id=client_order_id,
                broker_order_id=None,
                status=REJECTED_STATUS,
                error_message=error_message[:1000],
                raw_json={"order_kind": "exit"},
            )
        )

    try:
        async with async_session_factory() as session:
            await save_rejection(session)
            await session.commit()
    except Exception as e:
        logger.error(
            "Failed to persist exit order rejection",
            extra={
                "event_type": "EXIT_REJECTION_PERSIST_ERROR",
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
        logger.error(
            "Failed to persist fill record",
            extra={"event_type": "FILL_PERSIST_ERROR", "error": str(e)},
            exc_info=True,
        )
        # The processed-fill marker is written after this call. Propagating
        # keeps a failed fill write from being permanently marked complete.
        raise


@dataclass(frozen=True)
class ExitAllocationResult:
    """Outcome of allocating one closing order's cumulative fill across journal lots."""

    allocated_qty: float = 0.0
    closed_rows: int = 0
    unmatched_qty: float = 0.0


def _journal_multiplier(contract: str) -> float:
    """Dollars per 1.00 of quoted price: 100 for an OCC option contract, 1 for equity."""
    from orion.execution.attribution import is_occ_option_symbol

    return 100.0 if is_occ_option_symbol(contract) else 1.0


def _ledger(row: Any) -> list[dict[str, Any]]:
    raw = row.raw_json if isinstance(row.raw_json, dict) else {}
    legs = raw.get("exit_allocations")
    return list(legs) if isinstance(legs, list) else []


def _apply_exit_leg(
    row: Any, *, multiplier: float, order_id: str, qty: float, price: float, at: datetime | None, source: str
) -> bool:
    """Book one exit leg onto a journal lot. Returns True when the lot is now fully closed.

    Cumulative exit fields and the per-order ledger in ``raw_json`` are the
    durable state; ``realized_pnl`` is written only when the lot's whole entry
    quantity has been closed, so a partially closed lot keeps counting toward the
    entry caps (``count_open_journal_positions`` reads ``realized_pnl IS NULL``).
    The JSON column is reassigned rather than mutated in place — SQLAlchemy's
    plain ``JSON`` type does not track in-place mutation, and an unpersisted
    ledger would let a retry re-allocate the same order.
    """
    prev_qty = float(row.exit_filled_qty or 0.0)
    prev_avg = float(row.exit_filled_avg_price or 0.0)
    new_qty = prev_qty + qty
    new_avg = ((prev_qty * prev_avg) + (qty * price)) / new_qty if new_qty > 0 else 0.0

    legs = _ledger(row)
    legs.append(
        {
            "order_id": order_id,
            "qty": float(qty),
            "price": float(price),
            "at": at.isoformat() if isinstance(at, datetime) else None,
            "source": source,
        }
    )
    raw = dict(row.raw_json) if isinstance(row.raw_json, dict) else {}
    raw["exit_allocations"] = legs
    row.raw_json = raw

    row.exit_filled_qty = new_qty
    row.exit_filled_avg_price = new_avg
    row.exit_broker_order_id = order_id
    if at is not None:
        row.exit_filled_at_utc = at

    entry_qty = float(row.filled_qty or 0.0)
    if new_qty + 1e-9 >= entry_qty:
        row.realized_pnl = (new_avg - float(row.filled_avg_price or 0.0)) * entry_qty * multiplier
        row.notes = f"closed_by={order_id}"
        return True
    return False


async def allocate_exit_in_session(
    session: Any,
    *,
    contract: str,
    order_id: str,
    order_cum_qty: float,
    order_cum_avg_price: float,
    filled_at: datetime | None,
    source: str,
) -> ExitAllocationResult:
    """Allocate a closing order's CUMULATIVE fill across the open journal lots of ``contract``.

    ``contract`` is what the broker fill carries — the OCC option symbol
    (``NVDA260708C00190000``) — while journal rows carry the underlying, so lots
    are resolved through the candidate's ``option_symbol`` (ticker equality is the
    fallback for lots with no candidate contract, e.g. equity).

    Idempotent per order: the broker reports cumulative ``filled_qty`` /
    ``filled_avg_price``; the new leg is the delta between those figures and what
    the ledger already holds for ``order_id`` across all lots of the contract, so
    a duplicate delivery (live path then EOD reconcile, or two live processes)
    allocates nothing, and a later partial fill at a different price yields the
    correct per-leg price rather than the cumulative average. Lots are filled
    oldest-first; each lot's P&L is computed from its own entry and exit legs.

    Rows are locked ``FOR UPDATE`` on PostgreSQL so concurrent allocators
    serialize per contract (SQLite ignores the clause; the DB-level retry wrapper
    is the only guard there).
    """
    from orion.storage.models_trade_journal import TradeJournalEntry

    stmt = (
        select(TradeJournalEntry, CandidateTrade.option_symbol)
        .outerjoin(CandidateTrade, CandidateTrade.candidate_id == TradeJournalEntry.candidate_id)
        .where(
            or_(CandidateTrade.option_symbol == contract, TradeJournalEntry.ticker == contract),
            TradeJournalEntry.broker_order_id.is_not(None),
            TradeJournalEntry.filled_at_utc.is_not(None),
            TradeJournalEntry.filled_qty.is_not(None),
        )
        .order_by(TradeJournalEntry.created_at_utc.asc(), TradeJournalEntry.decision_id.asc())
        .with_for_update(of=TradeJournalEntry)
    )
    rows = (await session.execute(stmt)).all()

    already_qty = 0.0
    already_value = 0.0
    for row, _ in rows:
        for leg in _ledger(row):
            if leg.get("order_id") == order_id:
                q = float(leg.get("qty") or 0.0)
                already_qty += q
                already_value += q * float(leg.get("price") or 0.0)

    leg_qty = float(order_cum_qty) - already_qty
    if leg_qty <= 1e-9:
        return ExitAllocationResult()
    leg_value = float(order_cum_qty) * float(order_cum_avg_price) - already_value
    leg_price = leg_value / leg_qty
    if leg_price <= 0:
        logger.warning(
            "Exit leg price derived from cumulative figures is non-positive; using cumulative average",
            extra={
                "event_type": "EXIT_ALLOCATION_LEG_PRICE_FALLBACK",
                "contract": contract,
                "order_id": order_id,
                "leg_price": leg_price,
            },
        )
        leg_price = float(order_cum_avg_price)

    multiplier = _journal_multiplier(contract)
    remaining = leg_qty
    closed = 0
    allocated = 0.0
    for row, _option_symbol in rows:
        if remaining <= 1e-9:
            break
        if row.realized_pnl is not None:
            continue
        capacity = float(row.filled_qty or 0.0) - float(row.exit_filled_qty or 0.0)
        if capacity <= 1e-9:
            continue
        take = min(capacity, remaining)
        if _apply_exit_leg(
            row,
            multiplier=multiplier,
            order_id=order_id,
            qty=take,
            price=leg_price,
            at=filled_at,
            source=source,
        ):
            closed += 1
        remaining -= take
        allocated += take

    if remaining > 1e-9:
        logger.warning(
            "Closing fill has no open journal lot to attribute to",
            extra={
                "event_type": "EXIT_ALLOCATION_UNMATCHED",
                "contract": contract,
                "order_id": order_id,
                "unmatched_qty": remaining,
                "source": source,
            },
        )
    return ExitAllocationResult(allocated_qty=allocated, closed_rows=closed, unmatched_qty=remaining)


async def allocate_exit_to_journal(
    *,
    contract: str,
    order_id: str,
    order_cum_qty: float,
    order_cum_avg_price: float,
    filled_at: datetime | None,
    source: str,
) -> ExitAllocationResult:
    """`allocate_exit_in_session` in its own write transaction (the live fill path)."""

    async def write(session: Any) -> ExitAllocationResult:
        return await allocate_exit_in_session(
            session,
            contract=contract,
            order_id=order_id,
            order_cum_qty=order_cum_qty,
            order_cum_avg_price=order_cum_avg_price,
            filled_at=filled_at,
            source=source,
        )

    return await db_write(write)


async def reconcile_exits_in_session(session: Any) -> int:
    """Attribute Orion-owned SELL fills recorded in ``fills`` to any journal lot still open.

    Heals closes the live path missed (a fill polled while the risk manager had
    no in-memory position, a restart between the broker fill and the poll, an
    out-of-order partial). Runs at EOD before the expiry sweep so a lot that was
    actually sold is never booked as expired. Idempotent: the allocator's
    per-order ledger caps every order at its cumulative filled quantity.

    Returns the number of lots fully closed by this pass.
    """
    from orion.execution.attribution import orion_order_id_sql_pattern
    from orion.storage.models_execution import FillRecord
    from orion.storage.models_trade_journal import TradeJournalEntry

    contract_expr = func.coalesce(CandidateTrade.option_symbol, TradeJournalEntry.ticker)
    stmt = (
        select(contract_expr.label("contract"), func.min(TradeJournalEntry.filled_at_utc).label("first_entry"))
        .select_from(TradeJournalEntry)
        .outerjoin(CandidateTrade, CandidateTrade.candidate_id == TradeJournalEntry.candidate_id)
        .where(
            TradeJournalEntry.broker_order_id.is_not(None),
            TradeJournalEntry.filled_at_utc.is_not(None),
            TradeJournalEntry.filled_qty.is_not(None),
            TradeJournalEntry.realized_pnl.is_(None),
        )
        .group_by(contract_expr)
    )
    open_contracts = list((await session.execute(stmt)).all())

    closed = 0
    for contract, first_entry in open_contracts:
        fstmt = (
            select(FillRecord)
            .where(
                FillRecord.ticker == contract,
                func.lower(FillRecord.side) == "sell",
                FillRecord.client_order_id.like(orion_order_id_sql_pattern()),
                FillRecord.filled_at_utc.is_not(None),
                FillRecord.filled_at_utc >= first_entry,
                FillRecord.filled_qty > 0,
            )
            .order_by(FillRecord.filled_at_utc.asc())
        )
        for fill in (await session.execute(fstmt)).scalars().all():
            avg_price = float(fill.filled_avg_price or 0.0)
            if avg_price <= 0:
                continue
            result = await allocate_exit_in_session(
                session,
                contract=contract,
                order_id=fill.broker_order_id,
                order_cum_qty=float(fill.filled_qty),
                order_cum_avg_price=avg_price,
                filled_at=fill.filled_at_utc,
                source="eod_reconcile",
            )
            closed += result.closed_rows
    if closed:
        logger.info(
            f"EOD fill reconcile closed {closed} journal lots from recorded sell fills",
            extra={"event_type": "JOURNAL_FILL_RECONCILE_DONE", "closed": closed},
        )
    return closed


async def reconcile_journal_exits_from_fills() -> int:
    """`reconcile_exits_in_session` in its own write transaction (the EOD close-of-books)."""
    return await db_write(reconcile_exits_in_session)


async def count_open_journal_positions() -> tuple[dict[str, int], dict[str, int]] | None:
    """Open (entered, not yet closed) journal positions, by bucket and by ticker.

    Used by the per-bucket / per-underlying entry caps. Counts only filled
    entries, so an order in the fill-latency window is briefly uncounted —
    acceptable, because the risk manager's global max_option_positions check
    (which includes pending orders) remains the hard backstop.

    Returns None when the count is unavailable (DB failure) — callers must
    FAIL CLOSED (skip the entry) rather than treat unknown as zero.
    """
    from orion.execution.exit_fallback_rules import bucket_for_dte
    from orion.storage.models_trade_journal import TradeJournalEntry

    async def read(session: Any) -> list[Any]:
        stmt = (
            select(
                TradeJournalEntry.ticker,
                TradeJournalEntry.filled_at_utc,
                CandidateTrade.expiration_date,
            )
            .join(CandidateTrade, CandidateTrade.candidate_id == TradeJournalEntry.candidate_id)
            .where(
                TradeJournalEntry.broker_order_id.is_not(None),
                TradeJournalEntry.filled_at_utc.is_not(None),
                TradeJournalEntry.realized_pnl.is_(None),
            )
        )
        return list((await session.execute(stmt)).all())

    try:
        rows = await db_query(read)
    except Exception as e:
        logger.error(
            "Open-position count failed; entry caps cannot be verified",
            extra={"event_type": "OPEN_POSITION_COUNT_ERROR", "error": str(e)},
        )
        return None

    by_bucket: dict[str, int] = {}
    by_ticker: dict[str, int] = {}
    for ticker, filled_at, expiration in rows:
        dte = None
        if expiration is not None and filled_at is not None:
            # Calendar-day DTE at entry — same convention as the entry gate.
            dte = (expiration.date() - filled_at.date()).days
        bucket = bucket_for_dte(dte)
        by_bucket[bucket] = by_bucket.get(bucket, 0) + 1
        by_ticker[ticker] = by_ticker.get(ticker, 0) + 1
    return by_bucket, by_ticker


async def realize_expired_journal_rows() -> int:
    """Realize journal lots whose option expired without (fully) closing.

    An expired contract produces no closing fill, so the fill-driven path never
    releases the lot. Expiry is the linked candidate's ``expiration_date``; the
    UNSOLD remainder of the lot is booked at a total loss of its entry premium
    one day after expiry (grace for settlement / late fills to land first),
    on top of whatever the sold legs already realized. Runs after
    ``reconcile_journal_exits_from_fills`` so a lot that was in fact sold is
    never swept. Rows are tagged ``expired_worthless`` (the exact string the
    P&L reconciliation and bucket metrics key on) whenever any quantity expired.

    Assumes expired-worthless: an ITM position is exited by the time-stop long
    before expiry; anything that reaches expiry unsold is zero-bid dust.

    Returns the number of lots realized.
    """
    try:
        count = await db_write(sweep_expired_in_session)
        if count:
            logger.info(
                f"Expiry sweep realized {count} expired-worthless journal entries",
                extra={"event_type": "JOURNAL_EXPIRY_SWEEP_DONE", "count": count},
            )
        return int(count or 0)
    except Exception as e:
        # Raise rather than return 0: a swallowed failure is indistinguishable
        # from "nothing to sweep", and the EOD caller would record the session
        # as closed and never retry — leaving expired rows to jam the entry caps
        # (the exact 2026-07-31 failure this sweep exists to prevent).
        logger.error(
            "Expiry sweep failed",
            extra={"event_type": "JOURNAL_EXPIRY_SWEEP_ERROR", "error": str(e)},
        )
        raise


async def sweep_expired_in_session(session: Any) -> int:
    """The expiry sweep body; see `realize_expired_journal_rows`."""
    from orion.storage.models_trade_journal import TradeJournalEntry

    cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    stmt = (
        select(TradeJournalEntry, CandidateTrade.expiration_date)
        .join(CandidateTrade, CandidateTrade.candidate_id == TradeJournalEntry.candidate_id)
        .where(
            TradeJournalEntry.broker_order_id.is_not(None),
            TradeJournalEntry.filled_at_utc.is_not(None),
            TradeJournalEntry.realized_pnl.is_(None),
            CandidateTrade.expiration_date.is_not(None),
            CandidateTrade.expiration_date < cutoff - timedelta(days=1),
        )
        .with_for_update(of=TradeJournalEntry)
    )
    rows = (await session.execute(stmt)).all()
    realized = 0
    for entry, expiration_date in rows:
        if not entry.filled_qty or not entry.filled_avg_price:
            continue  # no entry fill data — can't compute the loss
        remaining = float(entry.filled_qty) - float(entry.exit_filled_qty or 0.0)
        if remaining <= 1e-9:
            continue
        # A candidate with an expiration is an option contract: 100 shares per 1.00.
        _apply_exit_leg(
            entry,
            multiplier=100.0,
            order_id="expired",
            qty=remaining,
            price=0.0,
            at=expiration_date,
            source="expired",
        )
        entry.notes = "expired_worthless"
        realized += 1
        logger.info(
            "Realized expired-worthless journal entry",
            extra={
                "event_type": "JOURNAL_EXPIRED_WORTHLESS",
                "ticker": entry.ticker,
                "decision_id": entry.decision_id,
                "realized_pnl": entry.realized_pnl,
                "expired_qty": remaining,
                "expired": str(expiration_date),
            },
        )
    return realized


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
