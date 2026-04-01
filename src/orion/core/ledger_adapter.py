"""Orion Ledger Adapter — bridges Orion execution events to the empire_core LedgerWriter."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from empire_core.ledger import LedgerWriter

from orion.core.enums import OrderSide

logger = logging.getLogger(__name__)


STRATEGY = "orion_signal"


def _symbol(ticker: str) -> str:
    """Convert a ticker to the canonical instrument key format.

    Options symbols (containing digits or OCC format) get the option prefix;
    plain tickers get the equity prefix.
    """
    # OCC option symbols contain digits and are typically 15+ chars
    if any(c.isdigit() for c in ticker) and len(ticker) > 6:
        return f"option:OCC:{ticker}"
    return f"equity:{ticker}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OrionLedgerAdapter:
    """Translates Orion order/fill events into standardised ledger records.

    Maintains internal maps so that fills can be correlated back to the
    originating order and any open trade can be closed on a sell fill.
    """

    def __init__(self, writer: LedgerWriter) -> None:
        self._writer = writer
        # client_order_id -> ledger order_id
        self._order_ids: dict[str, str] = {}
        # client_order_id -> side ("buy" / "sell")
        self._order_sides: dict[str, str] = {}
        # symbol -> (trade_id, entry_price, qty)
        self._open_trades: dict[str, tuple[str, float, float]] = {}
        self._hydrate_open_trades()

    def _hydrate_open_trades(self) -> None:
        """Load open trades from DB so exits work across process restarts."""
        try:
            trades = self._writer.get_trades(limit=500)
            for t in trades:
                if t.get("exit_time") is not None:
                    continue
                symbol = t.get("symbol", "")
                self._open_trades[symbol] = (
                    t["id"],
                    float(t.get("entry_price", 0)),
                    float(t.get("qty", 0)),
                )
            if self._open_trades:
                logger.info("ledger_open_trades_hydrated count=%d", len(self._open_trades))
        except Exception:
            logger.warning("ledger_hydration_failed", exc_info=True)

    # ------------------------------------------------------------------
    # Public hooks
    # ------------------------------------------------------------------

    def on_order_placed(
        self,
        decision_id: str,
        ticker: str,
        side: str,
        qty: float,
        client_order_id: str,
        limit_price: float | None = None,
    ) -> str:
        """Record an order submission and return the ledger order_id."""
        order_type = "limit" if limit_price is not None else "market"
        symbol = _symbol(ticker)

        order_id = self._writer.record_order(
            correlation_id=decision_id,
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            strategy=STRATEGY,
            limit_price=limit_price,
        )

        self._order_ids[client_order_id] = order_id
        self._order_sides[client_order_id] = side
        return order_id

    def on_fill(
        self,
        ticker: str,
        client_order_id: str,
        filled_qty: float,
        filled_avg_price: float,
        broker_order_id: str | None = None,
    ) -> None:
        """Record a fill and open/close a trade as appropriate."""
        symbol = _symbol(ticker)
        now = _now()

        order_id = self._order_ids.get(client_order_id)
        if order_id is None:
            raise ValueError(f"Unknown client_order_id: {client_order_id}")

        # Update order status
        self._writer.update_order_status(order_id, "filled", broker_order_id=broker_order_id)

        # Record the fill
        self._writer.record_fill(
            order_id=order_id,
            fill_price=filled_avg_price,
            fill_qty=filled_qty,
            commission=0.0,
            filled_at=now,
        )

        side = self._order_sides[client_order_id]

        if side == OrderSide.BUY:
            # Open a new trade and track a position
            trade_id = self._writer.open_trade(
                correlation_id=self._get_correlation_id(client_order_id),
                symbol=symbol,
                side=OrderSide.BUY,
                qty=filled_qty,
                entry_price=filled_avg_price,
                entry_time=now,
                strategy=STRATEGY,
            )
            self._open_trades[symbol] = (trade_id, filled_avg_price, filled_qty)

            self._writer.upsert_position(
                symbol=symbol,
                side=OrderSide.BUY,
                qty=filled_qty,
                avg_entry_price=filled_avg_price,
                strategy=STRATEGY,
            )

        elif side == OrderSide.SELL:
            # Close the open trade if one exists
            open_trade = self._open_trades.pop(symbol, None)
            if open_trade is not None:
                trade_id, entry_price, _qty = open_trade
                pnl_gross = (filled_avg_price - entry_price) * filled_qty
                self._writer.close_trade(
                    trade_id=trade_id,
                    exit_price=filled_avg_price,
                    exit_time=now,
                    pnl_gross=pnl_gross,
                    pnl_net=pnl_gross,
                )

            self._writer.close_position(symbol)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_correlation_id(self, client_order_id: str) -> str:
        """Retrieve the correlation_id (decision_id) for a given client order."""
        order_id = self._order_ids[client_order_id]
        orders = self._writer.get_orders(limit=500)
        for o in orders:
            if o["id"] == order_id:
                return o["correlation_id"]
        return client_order_id  # fallback
