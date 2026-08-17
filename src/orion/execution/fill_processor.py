"""Fill processing, position snapshot persistence, and partial fill tracking."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from orion.core.enums import OrderSide
from orion.execution.persistence import (
    is_fill_processed,
    mark_fill_processed,
    persist_fill_record,
)
from orion.labeler.constants import SECTOR_MAPPING
from orion.shared.db_utils import db_write
from orion.shared.decorators import db_retry
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger(__name__)


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
                return

            last_filled = self._partial_fill_tracker.get(order_id, 0.0)
            incremental_qty = filled_qty - last_filled

            if incremental_qty <= 0:
                return

            self._partial_fill_tracker[order_id] = filled_qty
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

            fill_outcome = await risk_manager.process_fill(
                ticker, incremental_qty, filled_avg_price, side, fill_id=fill_marker
            )

            # Attribute a closing fill back to the originating entry's
            # trade-journal lot(s). The fill carries the OCC contract symbol and
            # the broker's CUMULATIVE filled qty/avg for the order; the allocator
            # resolves lots through the candidate's option_symbol and books only
            # the delta its per-order ledger has not seen, so a re-poll of the
            # same order allocates nothing (B2 RCA; 2026-08 OCC/underlying RCA).
            if getattr(fill_outcome, "is_closing", False):
                from orion.execution.persistence import _coerce_timestamp, allocate_exit_to_journal

                # The close fill's timestamp must reach exit_filled_at_utc — the
                # PnL reconciliation buckets journal realizations by EXIT day.
                # A journal write failure must not stop the fill row and its
                # processed marker from landing below: the fills row is what the
                # EOD reconcile heals the journal from.
                try:
                    await allocate_exit_to_journal(
                        contract=ticker,
                        order_id=order_id,
                        order_cum_qty=filled_qty,
                        order_cum_avg_price=filled_avg_price,
                        filled_at=_coerce_timestamp(filled_at_raw),
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

            await persist_fill_record(fill)
            await mark_fill_processed(fill_marker, client_oid=client_oid, ticker=ticker, qty=incremental_qty)

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
