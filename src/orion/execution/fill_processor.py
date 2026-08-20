"""Fill processing, position snapshot persistence, and partial fill tracking."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from orion.core.enums import OrderSide
from orion.execution.persistence import (
    _coerce_timestamp,
    is_fill_processed,
    mark_fill_processed_in_session,
    persist_fill_record_in_session,
)
from orion.labeler.constants import SECTOR_MAPPING
from orion.shared.db_utils import db_write
from orion.shared.decorators import db_retry
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger(__name__)


async def _write_fill_atomically(
    *,
    fill: Any,
    risk_manager: Any,
    fill_marker: str,
    client_oid: str,
    ticker: str,
    incremental_qty: float,
    filled_avg_price: float,
    side: str,
    filled_at: datetime | None,
) -> Any:
    """Persist the fill row, the risk-state update, and the processed-fill
    marker in ONE transaction — all three commit together, or none do.

    Replaces a prior design where the marker was written in its own
    transaction before the risk update, with a compensating delete on
    failure: that could not close two real gaps (2026-08-18/19 RCA) — a
    process crash between the marker commit and the risk update completing,
    and the compensating delete itself failing — both of which left a
    durable marker permanently suppressing a fill whose risk-state effect
    was never saved. Atomicity closes both: there is no window where the
    marker exists without the risk update, or vice versa.

    A narrower case remains even with atomicity: a commit-acknowledgement
    loss, where this transaction's commit succeeds on the DB server but the
    acknowledgement never reaches this process (a dropped connection at
    exactly that moment), so this call raises anyway and @db_retry retries.
    ``prepare_fill_for_session`` detects that retry (the marker it's about to
    write already exists) and returns an effect flagged ``already_durable``
    instead of colliding with it. If the ack-loss lands on the LAST retry
    attempt, there is no further attempt for that in-session check to run in
    — so after @db_retry gives up, one final out-of-band check (a fresh
    read, outside any transaction) asks whether the marker landed anyway
    before treating this as a genuine failure (2026-08-19 RCA, round 2).
    """

    async def write_all(session: Any) -> Any:
        effect = await risk_manager.prepare_fill_for_session(
            session, ticker, incremental_qty, filled_avg_price, side, fill_id=fill_marker, filled_at=filled_at
        )
        if effect is None:
            # risk_manager already has this fill_id (its own dedup) even
            # though our durable marker check above said otherwise — an
            # inconsistency that shouldn't arise from FillProcessor's own
            # calls, but nothing to write here either way.
            return None
        if not effect.already_durable:
            await persist_fill_record_in_session(session, fill)
            await mark_fill_processed_in_session(
                session, fill_marker, client_oid=client_oid, ticker=ticker, qty=incremental_qty
            )
        return effect

    @db_retry
    async def attempt() -> Any:
        return await db_write(write_all)

    try:
        return await attempt()
    except Exception:
        # Every @db_retry attempt raised. The LAST one may have actually
        # committed on the DB server with the acknowledgement lost in
        # transit — the same ambiguity write_all's in-session check already
        # recovers from on a RETRY, just with no retry left this time. One
        # last check, outside any transaction: if the marker is durably
        # there, this fill IS processed — recompute its effect and let the
        # caller apply it instead of leaving it stuck behind a marker that
        # looks unconfirmed but isn't. is_fill_processed itself re-raises on
        # its own failure (fails closed toward "don't know"), so a genuine
        # outage still propagates the original exception here.
        if await is_fill_processed(fill_marker):
            logger.warning(
                f"Fill {fill_marker} committed despite every write attempt raising "
                "(a commit-acknowledgement loss on the final retry) -- recovering from the durable marker.",
                extra={"event_type": "FILL_COMMIT_ACK_LOST_RECOVERED", "fill_marker": fill_marker},
            )
            return risk_manager.recompute_durable_fill_effect(
                ticker, incremental_qty, filled_avg_price, side, fill_marker, filled_at=filled_at
            )
        raise


class FillProcessor:
    """Processes broker fills and manages position snapshots.

    Tracks partial fills via an internal tracker and delegates
    persistence to the execution.persistence module.
    """

    def __init__(self, ledger: Any = None) -> None:
        self._partial_fill_tracker: dict[str, float] = {}
        self._ledger = ledger

    async def process_single_fill(self, fill: Any, risk_manager: Any, remove_pending_fn: Any) -> None:
        """Process a single fill event, updating risk state and persisting.

        Handles partial fills by tracking cumulative filled quantity and only
        processing the incremental amount since last update.

        Only processes fills for orders placed by Orion (client_order_id
        starts with the Orion prefix).
        """
        try:
            from orion.execution.attribution import is_orion_owned

            order_id = str(fill.get("id", "")) if isinstance(fill, dict) else str(fill.id)
            client_oid = (
                fill.get("client_order_id") if isinstance(fill, dict) else getattr(fill, "client_order_id", None)
            ) or order_id

            # Skip fills that don't belong to Orion
            if not is_orion_owned(client_oid):
                return

            if isinstance(fill, dict):
                filled_qty = float(fill.get("filled_qty", 0) or 0)
                total_qty = float(fill.get("qty", 0) or filled_qty)
                filled_avg_price = float(fill.get("filled_avg_price", 0) or 0)
                ticker = fill.get("symbol", "")
                side = fill.get("side", "")
                filled_at_raw = fill.get("filled_at") or fill.get("filled_at_utc")
            else:
                filled_qty = float(fill.filled_qty) if fill.filled_qty else 0.0
                total_qty = float(fill.qty) if fill.qty else filled_qty
                filled_avg_price = float(fill.filled_avg_price) if fill.filled_avg_price else 0.0
                ticker = fill.symbol
                side = str(fill.side)
                filled_at_raw = getattr(fill, "filled_at", None) or getattr(fill, "filled_at_utc", None)

            fill_marker = f"{order_id}:{filled_qty}"

            if await is_fill_processed(fill_marker):
                # This exact cumulative fill already landed durably — from a
                # normal re-poll, or from an earlier attempt in THIS process
                # that crashed after the atomic fill-write
                # (_write_fill_atomically) committed but before it reached
                # the pending-order cleanup below. Every later poll of the
                # same fill short-circuits here, so if the order is now
                # fully filled, this is the only remaining chance to clean
                # up its pending-order tracking row — remove_pending_order
                # is itself idempotent (no-ops once the row is gone) and
                # swallows its own persistence failures rather than raising,
                # so it is always safe to call again here (2026-08-19 RCA,
                # codex review).
                if filled_qty >= total_qty:
                    await remove_pending_fn(client_oid)
                return

            last_filled = self._partial_fill_tracker.get(order_id, 0.0)
            incremental_qty = filled_qty - last_filled

            if incremental_qty <= 0:
                return

            is_partial = filled_qty < total_qty
            fill_type = "PARTIAL" if is_partial else "COMPLETE"

            logger.info(
                f"Processing {fill_type} fill: {ticker} {side} {incremental_qty:.2f} @ {filled_avg_price:.2f} "
                f"(total: {filled_qty:.2f}/{total_qty:.2f})",
                extra={
                    "event_type": f"FILL_{fill_type}",
                    "order_id": order_id,
                    "ticker": ticker,
                    "incremental_qty": incremental_qty,
                    "filled_qty": filled_qty,
                    "total_qty": total_qty,
                },
            )

            # The broker's execution time decides which trading session the
            # realized P&L belongs to — a fill recovered late must not book
            # into today's daily-loss figure.
            filled_at = _coerce_timestamp(filled_at_raw)

            # The fill row, the risk-state update, and the processed-fill
            # marker land in ONE transaction — see _write_fill_atomically.
            # Nothing on self (the tracker) or risk_manager moves until that
            # transaction has actually committed, so a failure here leaves
            # nothing to roll back: the next poll (same process or after a
            # restart) retries the exact same incremental amount from scratch.
            effect = await _write_fill_atomically(
                fill=fill,
                risk_manager=risk_manager,
                fill_marker=fill_marker,
                client_oid=client_oid,
                ticker=ticker,
                incremental_qty=incremental_qty,
                filled_avg_price=filled_avg_price,
                side=side,
                filled_at=filled_at,
            )

            self._partial_fill_tracker[order_id] = filled_qty

            if effect is None:
                # risk_manager already had this fill_id — nothing landed
                # durably (see _write_fill_atomically), nothing to apply.
                return

            fill_outcome = await risk_manager.apply_fill_effect(effect)

            # Attribute a closing fill back to the originating entry's
            # trade-journal lot(s). The fill carries the OCC contract symbol and
            # the broker's CUMULATIVE filled qty/avg for the order; the allocator
            # resolves lots through the candidate's option_symbol and books only
            # the delta its per-order ledger has not seen, so a re-poll of the
            # same order allocates nothing (B2 RCA; 2026-08 OCC/underlying RCA).
            if getattr(fill_outcome, "is_closing", False):
                from orion.execution.persistence import allocate_exit_to_journal

                # The close fill's timestamp must reach exit_filled_at_utc — the
                # PnL reconciliation buckets journal realizations by EXIT day.
                # A journal write failure must not raise here: the fills row
                # (already durably written above) is what the EOD reconcile
                # heals the journal from.
                try:
                    await allocate_exit_to_journal(
                        contract=ticker,
                        order_id=order_id,
                        order_cum_qty=filled_qty,
                        order_cum_avg_price=filled_avg_price,
                        filled_at=filled_at,
                        source="live",
                    )
                except Exception as alloc_exc:
                    logger.error(
                        f"Journal exit allocation failed for {ticker} order {order_id}; EOD reconcile will retry",
                        extra={
                            "event_type": "EXIT_ALLOCATION_FAILED",
                            "ticker": ticker,
                            "order_id": order_id,
                            "error": str(alloc_exc),
                        },
                        exc_info=True,
                    )

            # Update sector exposure tracking
            sector = SECTOR_MAPPING.get(ticker)
            if sector:
                fill_cost = incremental_qty * filled_avg_price
                exposure_change = fill_cost if side.lower() == OrderSide.BUY else -fill_cost
                risk_manager.update_sector_exposure(sector, exposure_change)

            if not is_partial:
                await remove_pending_fn(client_oid)
                self._partial_fill_tracker.pop(order_id, None)

            # Write to empire-core ledger for EmpireUI
            if self._ledger is not None and not is_partial:
                try:
                    self._ledger.on_fill(
                        ticker=ticker,
                        client_order_id=client_oid,
                        filled_qty=filled_qty,
                        filled_avg_price=filled_avg_price,
                        broker_order_id=order_id,
                    )
                except Exception as ledger_exc:
                    logger.warning("ledger_fill_write_failed", error=str(ledger_exc))

        except Exception as e:
            fill_id_str = (
                str(fill.get("id", "unknown")) if isinstance(fill, dict) else str(getattr(fill, "id", "unknown"))
            )
            logger.error(f"Failed to process fill {fill_id_str}: {e}", exc_info=True)


@db_retry
async def maybe_snapshot_positions(
    gateway_client: Any,
    last_snapshot_ts: datetime | None,
    min_interval_seconds: int = 60,
) -> datetime | None:
    """Persist position snapshots from Data Gateway.

    Returns updated snapshot timestamp, or None if no snapshot was taken.
    """
    now = datetime.now(UTC)
    if last_snapshot_ts and (now - last_snapshot_ts) < timedelta(seconds=min_interval_seconds):
        return None

    try:
        positions = await gateway_client.get_positions()
    except Exception as e:
        logger.error(
            "Failed to fetch positions for snapshot",
            extra={"event_type": "POSITIONS_SNAPSHOT_FETCH_ERROR", "error": str(e)},
        )
        return None

    if not positions:
        return now

    try:
        from orion.storage.models_execution import PositionSnapshot

        rows = []
        for p in positions:
            snapshot = _create_position_snapshot_from_dict(p, now, PositionSnapshot)
            if snapshot:
                rows.append(snapshot)

        async def save_snapshots(session: Any) -> None:
            session.add_all(rows)

        await db_write(save_snapshots)

        logger.info("Positions snapshot persisted", extra={"event_type": "POSITIONS_SNAPSHOT", "count": len(rows)})
        return now
    except Exception as e:
        logger.error(
            "Failed to persist positions snapshot",
            extra={"event_type": "POSITIONS_SNAPSHOT_PERSIST_ERROR", "error": str(e)},
        )
        return None


def _create_position_snapshot_from_dict(p: dict[str, Any], now: datetime, model_class: Any) -> Any | None:
    """Create a PositionSnapshot model from a Gateway position dict."""
    symbol = p.get("symbol")
    if not symbol:
        return None

    def _maybe_float(v: Any) -> float | None:
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    qty = _maybe_float(p.get("qty")) or 0.0
    avg_entry = _maybe_float(p.get("avg_entry_price"))
    market_value = _maybe_float(p.get("market_value"))
    unrealized_pl = _maybe_float(p.get("unrealized_pl"))

    return model_class(
        id=str(uuid.uuid4()),
        snapshot_ts_utc=now,
        ticker=str(symbol),
        qty=float(qty),
        avg_entry_price=avg_entry,
        market_value=market_value,
        unrealized_pl=unrealized_pl,
        raw_json=p,
    )
