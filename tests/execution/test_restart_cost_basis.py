"""
Regression tests: on restart, _sync_risk_from_gateway must derive avg_entry
from Orion's own FillRecord, NOT from the broker's avg_entry_price.

On the shared Alpaca paper account, avg_entry_price is the blended cost basis
across ALL systems holding the same instrument.  After any restart that wipes
the in-memory positions dict, using the broker's value corrupts realized-PnL
and any risk/sizing logic that reads avg_entry.

Bug surfaced 2026-06-11 during broker-PnL reconciliation work.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from orion.execution.execution_engine import ExecutionEngine
from orion.storage.db import async_session_factory
from orion.storage.models_execution import FillRecord


# ── Shared stub ──────────────────────────────────────────────────────────────


class _RiskStub:
    """Minimal RiskManager stub for _sync_risk_from_gateway tests."""

    def __init__(self) -> None:
        self.positions: dict = {}
        self.ticker_exposures: dict = {}
        self.open_positions: int = 0
        self._equity_seeded = True
        self._peak_equity_seeded = True
        self.current_equity = 100_000.0
        self.starting_equity = 100_000.0
        self.peak_equity = 100_000.0
        self.current_daily_loss = 0.0

    def seed_equity_baseline(self, equity: float) -> None:  # noqa: D102
        pass

    async def evaluate_drawdown_kill_switch(self) -> None:  # noqa: D102
        pass


# ── DB helpers ────────────────────────────────────────────────────────────────


async def _insert_fill(
    broker_id: str,
    client_order_id: str,
    ticker: str,
    side: str,
    qty: float,
    price: float,
    filled_at: datetime | None = None,
) -> None:
    ts = filled_at or datetime.now(UTC)
    async with async_session_factory() as session:
        session.add(
            FillRecord(
                id=broker_id,
                broker_order_id=broker_id,
                client_order_id=client_order_id,
                ticker=ticker,
                side=side,
                filled_qty=qty,
                filled_avg_price=price,
                filled_at_utc=ts,
                raw_json={},
            )
        )
        await session.commit()


def _make_engine(risk: _RiskStub, monkeypatch, broker_positions, orion_tickers):
    """Return an ExecutionEngine stub wired to the given broker positions."""
    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine.risk_manager = risk

    client = MagicMock()
    client.get_account = AsyncMock(return_value={"equity": "100000", "last_equity": "100000"})
    client.get_positions = AsyncMock(return_value=broker_positions)

    monkeypatch.setattr(engine, "_check_gateway_available", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_get_gateway_client", lambda: client)
    monkeypatch.setattr(engine, "_fetch_orion_tickers", AsyncMock(return_value=orion_tickers))
    return engine


# ── _compute_cost_basis_from_fills ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cost_basis_single_buy_fill():
    """Single buy fill → avg_entry equals the fill price."""
    sym = "AAPL260529P00315000"
    await _insert_fill("b1", "orion_1", sym, "buy", 10.0, 2.00)

    engine = ExecutionEngine.__new__(ExecutionEngine)
    result = await engine._compute_cost_basis_from_fills()

    assert sym in result
    assert result[sym]["avg_entry"] == pytest.approx(2.00)
    assert result[sym]["qty"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_cost_basis_multiple_buys_weighted_average():
    """Two buy fills at different prices → weighted average."""
    sym = "SPY240919C00500000"
    t0 = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    await _insert_fill("bm1", "orion_m1", sym, "buy", 5.0, 2.00, t0)
    await _insert_fill("bm2", "orion_m2", sym, "buy", 5.0, 3.00, t0 + timedelta(minutes=5))

    engine = ExecutionEngine.__new__(ExecutionEngine)
    result = await engine._compute_cost_basis_from_fills()

    # (5*2 + 5*3) / 10 = 2.50
    assert result[sym]["avg_entry"] == pytest.approx(2.50)
    assert result[sym]["qty"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_cost_basis_partial_close_preserves_entry():
    """Selling part of a position does not change avg_entry."""
    sym = "TSLA241220P00200000"
    t0 = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    await _insert_fill("bp1", "orion_p1", sym, "buy", 10.0, 5.00, t0)
    await _insert_fill("bp2", "orion_p2", sym, "sell", 4.0, 8.00, t0 + timedelta(hours=1))

    engine = ExecutionEngine.__new__(ExecutionEngine)
    result = await engine._compute_cost_basis_from_fills()

    assert result[sym]["avg_entry"] == pytest.approx(5.00), "partial close must not shift avg_entry"
    assert result[sym]["qty"] == pytest.approx(6.0)


@pytest.mark.asyncio
async def test_cost_basis_full_close_then_reopen():
    """Full close zeroes the position; subsequent buy starts a fresh basis."""
    sym = "NVDA241220C00800000"
    t0 = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    await _insert_fill("bfr1", "orion_fr1", sym, "buy", 10.0, 5.00, t0)
    await _insert_fill("bfr2", "orion_fr2", sym, "sell", 10.0, 8.00, t0 + timedelta(hours=1))
    await _insert_fill("bfr3", "orion_fr3", sym, "buy", 5.0, 9.00, t0 + timedelta(hours=2))

    engine = ExecutionEngine.__new__(ExecutionEngine)
    result = await engine._compute_cost_basis_from_fills()

    assert result[sym]["avg_entry"] == pytest.approx(9.00), "re-opened position must use new entry price"
    assert result[sym]["qty"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_cost_basis_ignores_non_orion_fills():
    """Fills without orion_ prefix must not affect cost basis."""
    sym = "AMZN240101C00200000"
    t0 = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    await _insert_fill("bo1", "other_system_1", sym, "buy", 10.0, 99.00, t0)
    await _insert_fill("bo2", "orion_own", sym, "buy", 5.0, 3.00, t0 + timedelta(minutes=1))

    engine = ExecutionEngine.__new__(ExecutionEngine)
    result = await engine._compute_cost_basis_from_fills()

    assert sym in result
    assert result[sym]["avg_entry"] == pytest.approx(3.00), "non-orion fill must be excluded from cost basis"
    assert result[sym]["qty"] == pytest.approx(5.0)


# ── _sync_risk_from_gateway — contamination fix ───────────────────────────────


@pytest.mark.asyncio
async def test_restart_uses_fill_cost_basis_not_broker_avg_entry(monkeypatch):
    """
    Core bug: broker avg_entry_price is blended across all systems on the
    shared paper account.  After restart, risk_manager must use Orion's own
    fill-derived avg_entry (2.00), not the broker's contaminated value (3.50).
    """
    sym = "AAPL260529P00315000"
    await _insert_fill("bg1", "orion_g1", sym, "buy", 10.0, 2.00)

    risk = _RiskStub()
    engine = _make_engine(
        risk,
        monkeypatch,
        broker_positions=[{"symbol": sym, "qty": "10", "avg_entry_price": "3.50", "market_value": "4000"}],
        orion_tickers={sym},
    )

    await engine._sync_risk_from_gateway()

    pos = risk.positions.get(sym)
    assert pos is not None, f"position {sym} not loaded"
    assert pos["avg_entry"] == pytest.approx(2.00), (
        f"Expected fill-derived avg_entry=2.00, got {pos['avg_entry']!r}. "
        "Broker avg_entry_price (3.50) contaminates shared-account cost basis."
    )


@pytest.mark.asyncio
async def test_restart_realizes_pnl_correctly_after_cost_basis_fix(monkeypatch):
    """
    End-to-end PnL sanity: buy 10 at $2.00, restart, then sell 10 at $3.00.
    The symbol is an OCC option, so realized_pnl must be
    (3.00-2.00)*10*100 = $1,000 — premiums are quoted per share and traded in
    100-share contracts.

    If broker's contaminated avg_entry_price (say $5.00) were used instead,
    process_fill would compute (3.00-5.00)*10*100 = -$2,000 — wrong and
    kill-switch-triggering.
    """
    from orion.execution.risk.manager import RiskManager

    sym = "AAPL260529P00315000"
    await _insert_fill("bpnl1", "orion_pnl1", sym, "buy", 10.0, 2.00)

    risk = RiskManager()
    engine = _make_engine(
        risk,
        monkeypatch,
        broker_positions=[{"symbol": sym, "qty": "10", "avg_entry_price": "5.00", "market_value": "3000"}],
        orion_tickers={sym},
    )

    await engine._sync_risk_from_gateway()

    outcome = await risk.process_fill(sym, 10.0, 3.00, "sell", fill_id="close_pnl1")

    assert outcome.realized_pnl == pytest.approx(1000.00), (
        f"realized_pnl should be (3.00-2.00)*10*100=$1,000.00 using fill-derived entry; "
        f"got {outcome.realized_pnl!r} (likely used contaminated broker avg_entry_price)"
    )


@pytest.mark.asyncio
async def test_restart_falls_back_to_broker_when_no_fills(monkeypatch):
    """
    When FillRecord has no orion fills for a symbol (e.g. a position opened
    in a window where fills weren't yet persisted), fall back to broker value.
    """
    sym = "NVDA241220C00800000"

    risk = _RiskStub()
    engine = _make_engine(
        risk,
        monkeypatch,
        broker_positions=[{"symbol": sym, "qty": "5", "avg_entry_price": "12.50", "market_value": "7000"}],
        orion_tickers={sym},
    )

    await engine._sync_risk_from_gateway()

    pos = risk.positions.get(sym)
    assert pos is not None
    assert pos["avg_entry"] == pytest.approx(12.50), "no fills → fall back to broker avg_entry_price"
