"""Tests for OrionLedgerAdapter."""

from __future__ import annotations

import tempfile
import os

import pytest

from empire_core.ledger import LedgerWriter
from orion.core.ledger_adapter import OrionLedgerAdapter, STRATEGY, _symbol


@pytest.fixture()
def writer(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    return LedgerWriter(db_path=db_path, system="orion")


@pytest.fixture()
def adapter(writer):
    return OrionLedgerAdapter(writer)


# ------------------------------------------------------------------
# Symbol formatting
# ------------------------------------------------------------------


@pytest.mark.unit
class TestSymbolFormat:
    def test_symbol_format(self):
        assert _symbol("AAPL") == "equity:AAPL"

    def test_symbol_format_lowercase(self):
        assert _symbol("msft") == "equity:msft"


# ------------------------------------------------------------------
# on_order_placed
# ------------------------------------------------------------------


@pytest.mark.unit
class TestOnOrderPlaced:
    def test_records_market_order(self, adapter, writer):
        order_id = adapter.on_order_placed(
            decision_id="dec-1",
            ticker="AAPL",
            side="buy",
            qty=10,
            client_order_id="co-1",
        )
        assert order_id is not None

        orders = writer.get_orders()
        assert len(orders) == 1
        o = orders[0]
        assert o["symbol"] == "equity:AAPL"
        assert o["side"] == "buy"
        assert o["qty"] == 10.0
        assert o["order_type"] == "market"
        assert o["strategy"] == STRATEGY
        assert o["correlation_id"] == "dec-1"
        assert o["limit_price"] is None

    def test_records_limit_order(self, adapter, writer):
        order_id = adapter.on_order_placed(
            decision_id="dec-2",
            ticker="TSLA",
            side="sell",
            qty=5,
            client_order_id="co-2",
            limit_price=250.0,
        )
        orders = writer.get_orders()
        assert len(orders) == 1
        assert orders[0]["order_type"] == "limit"
        assert orders[0]["limit_price"] == 250.0

    def test_multiple_orders_tracked(self, adapter):
        id1 = adapter.on_order_placed("d1", "AAPL", "buy", 10, "co-1")
        id2 = adapter.on_order_placed("d2", "TSLA", "sell", 5, "co-2")
        assert id1 != id2
        assert "co-1" in adapter._order_ids
        assert "co-2" in adapter._order_ids


# ------------------------------------------------------------------
# on_fill — buy side
# ------------------------------------------------------------------


@pytest.mark.unit
class TestOnFillBuy:
    def test_buy_fill_opens_trade(self, adapter, writer):
        adapter.on_order_placed("dec-1", "AAPL", "buy", 10, "co-1")
        adapter.on_fill("AAPL", "co-1", 10, 150.0, broker_order_id="br-1")

        # Order marked filled
        orders = writer.get_orders()
        assert orders[0]["status"] == "filled"
        assert orders[0]["broker_order_id"] == "br-1"

        # Trade opened
        trades = writer.get_trades()
        assert len(trades) == 1
        t = trades[0]
        assert t["symbol"] == "equity:AAPL"
        assert t["side"] == "buy"
        assert t["qty"] == 10.0
        assert t["entry_price"] == 150.0
        assert t["exit_price"] is None

        # Position created
        positions = writer.get_positions()
        assert len(positions) == 1
        p = positions[0]
        assert p["symbol"] == "equity:AAPL"
        assert p["qty"] == 10.0
        assert p["avg_entry_price"] == 150.0

    def test_open_trade_tracked_internally(self, adapter):
        adapter.on_order_placed("dec-1", "AAPL", "buy", 10, "co-1")
        adapter.on_fill("AAPL", "co-1", 10, 150.0)
        assert "equity:AAPL" in adapter._open_trades


# ------------------------------------------------------------------
# on_fill — sell side (close trade)
# ------------------------------------------------------------------


@pytest.mark.unit
class TestOnFillSell:
    def test_sell_fill_closes_trade(self, adapter, writer):
        # Buy first
        adapter.on_order_placed("dec-1", "AAPL", "buy", 10, "co-buy")
        adapter.on_fill("AAPL", "co-buy", 10, 150.0)

        # Then sell
        adapter.on_order_placed("dec-2", "AAPL", "sell", 10, "co-sell")
        adapter.on_fill("AAPL", "co-sell", 10, 160.0)

        # Trade should be closed with P&L
        trades = writer.get_trades()
        assert len(trades) == 1
        t = trades[0]
        assert t["exit_price"] == 160.0
        assert t["pnl_gross"] == pytest.approx(100.0)  # (160 - 150) * 10
        assert t["pnl_net"] == pytest.approx(100.0)

        # Position should be removed
        positions = writer.get_positions()
        assert len(positions) == 0

        # Internal tracking cleared
        assert "equity:AAPL" not in adapter._open_trades

    def test_sell_negative_pnl(self, adapter, writer):
        adapter.on_order_placed("dec-1", "NVDA", "buy", 20, "co-buy")
        adapter.on_fill("NVDA", "co-buy", 20, 200.0)

        adapter.on_order_placed("dec-2", "NVDA", "sell", 20, "co-sell")
        adapter.on_fill("NVDA", "co-sell", 20, 190.0)

        trades = writer.get_trades()
        assert trades[0]["pnl_gross"] == pytest.approx(-200.0)  # (190 - 200) * 20

    def test_sell_without_open_trade(self, adapter, writer):
        """Selling when no open trade exists should still record fill and close position."""
        adapter.on_order_placed("dec-1", "AAPL", "sell", 10, "co-sell")
        adapter.on_fill("AAPL", "co-sell", 10, 150.0)

        orders = writer.get_orders()
        assert orders[0]["status"] == "filled"


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


@pytest.mark.unit
class TestErrorHandling:
    def test_fill_unknown_order_raises(self, adapter):
        with pytest.raises(ValueError, match="Unknown client_order_id"):
            adapter.on_fill("AAPL", "nonexistent", 10, 150.0)


# ------------------------------------------------------------------
# Full round-trip
# ------------------------------------------------------------------


@pytest.mark.unit
class TestRoundTrip:
    def test_buy_then_sell_two_tickers(self, adapter, writer):
        # Buy AAPL and TSLA
        adapter.on_order_placed("d1", "AAPL", "buy", 10, "co-1")
        adapter.on_order_placed("d2", "TSLA", "buy", 5, "co-2")
        adapter.on_fill("AAPL", "co-1", 10, 150.0)
        adapter.on_fill("TSLA", "co-2", 5, 250.0)

        assert len(writer.get_positions()) == 2
        assert len(adapter._open_trades) == 2

        # Sell AAPL only
        adapter.on_order_placed("d3", "AAPL", "sell", 10, "co-3")
        adapter.on_fill("AAPL", "co-3", 10, 160.0)

        positions = writer.get_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "equity:TSLA"

        trades = writer.get_trades()
        # The AAPL trade should be closed
        aapl_trade = [t for t in trades if t["symbol"] == "equity:AAPL"][0]
        assert aapl_trade["pnl_gross"] == pytest.approx(100.0)

        # TSLA trade still open
        tsla_trade = [t for t in trades if t["symbol"] == "equity:TSLA"][0]
        assert tsla_trade["exit_price"] is None
