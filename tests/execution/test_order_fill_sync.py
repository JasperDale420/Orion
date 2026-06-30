"""Tests for poll_fills order/fill sync — Task 4.2 (FOLLOWUPS #4)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from orion.execution.execution_engine import ExecutionEngine


def _make_engine() -> ExecutionEngine:
    """Construct an ExecutionEngine bypassing __init__ so we control attributes."""
    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine._gateway_available = True
    engine._lease_service_id = None
    engine._lease_run_id = None
    engine._last_fill_poll_ts = datetime.now(UTC)  # skip account-equity poll
    engine._last_order_poll_ts = None
    # __new__ bypasses __init__; these tests target order polling, so set the
    # periodic-position-sync state and stub the maintenance methods (covered by
    # test_poll_fills_periodic_sync) so poll_fills' tail doesn't run here.
    engine._last_position_sync_ts = datetime.now(UTC)
    engine.last_positions_snapshot_ts = None
    engine._sync_risk_from_gateway = AsyncMock()
    engine._maybe_snapshot_positions = AsyncMock()
    engine._fill_processor = MagicMock()
    engine._fill_processor.process_single_fill = AsyncMock()
    engine.risk_manager = MagicMock()
    engine._remove_pending_order_compat = AsyncMock()
    return engine


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filled_order_triggers_fill_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A filled orion-attributed order routes through FillProcessor and the
    orders.status updater."""
    engine = _make_engine()

    filled_order = {
        "id": "broker-filled-1",
        "client_order_id": "orion_abc123",
        "status": "filled",
        "filled_qty": "10",
        "filled_avg_price": "2.50",
        "symbol": "AAPL",
        "side": "buy",
    }

    client = MagicMock()
    client.get_orders = AsyncMock(return_value=[filled_order])
    client.get_account = AsyncMock(return_value={"equity": 100000.0})
    client.get_clock = AsyncMock(return_value={})  # gateway-available check in poll_fills
    monkeypatch.setattr(engine, "_get_gateway_client", lambda: client)

    persist_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("orion.execution.execution_engine.persist_order_status_update", persist_mock)

    await engine.poll_fills()

    # FillProcessor was invoked once with the filled order.
    assert engine._fill_processor.process_single_fill.await_count == 1
    call_args = engine._fill_processor.process_single_fill.await_args
    assert call_args.args[0] is filled_order

    # orders.status was updated to "filled".
    assert persist_mock.await_count == 1
    kwargs = persist_mock.await_args.kwargs
    assert kwargs["broker_order_id"] == "broker-filled-1"
    assert kwargs["status"] == "filled"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_order_poll_skips_non_orion_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Orders without the orion_ prefix are filtered before any DB writes or
    fill processing — shared-account safety."""
    engine = _make_engine()

    orion_order = {
        "id": "broker-orion-1",
        "client_order_id": "orion_xyz",
        "status": "filled",
        "filled_qty": "5",
        "filled_avg_price": "1.25",
    }
    cerberus_order = {
        "id": "broker-cerb-1",
        "client_order_id": "cerb_999",
        "status": "filled",
        "filled_qty": "20",
        "filled_avg_price": "150.0",
    }
    threeRoses_order = {
        "id": "broker-3r-1",
        "client_order_id": "3roses_42",
        "status": "filled",
        "filled_qty": "100",
        "filled_avg_price": "50.0",
    }

    client = MagicMock()
    client.get_orders = AsyncMock(return_value=[orion_order, cerberus_order, threeRoses_order])
    client.get_account = AsyncMock(return_value={"equity": 100000.0})
    client.get_clock = AsyncMock(return_value={})  # gateway-available check in poll_fills
    monkeypatch.setattr(engine, "_get_gateway_client", lambda: client)

    persist_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("orion.execution.execution_engine.persist_order_status_update", persist_mock)

    await engine.poll_fills()

    # Only the orion-prefixed order made it through.
    assert engine._fill_processor.process_single_fill.await_count == 1
    assert persist_mock.await_count == 1
    assert engine._fill_processor.process_single_fill.await_args.args[0] is orion_order
    assert persist_mock.await_args.kwargs["broker_order_id"] == "broker-orion-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_order_poll_throttle_skips_within_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling poll_fills twice within the throttle window only fires
    get_orders once."""
    engine = _make_engine()

    filled_order = {
        "id": "broker-throt-1",
        "client_order_id": "orion_throt",
        "status": "filled",
        "filled_qty": "1",
        "filled_avg_price": "1.0",
    }

    client = MagicMock()
    client.get_orders = AsyncMock(return_value=[filled_order])
    client.get_account = AsyncMock(return_value={"equity": 100000.0})
    client.get_clock = AsyncMock(return_value={})  # gateway-available check in poll_fills
    monkeypatch.setattr(engine, "_get_gateway_client", lambda: client)

    persist_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("orion.execution.execution_engine.persist_order_status_update", persist_mock)

    await engine.poll_fills()
    await engine.poll_fills()  # immediately again — should be throttled

    assert client.get_orders.await_count == 1
    assert engine._fill_processor.process_single_fill.await_count == 1
