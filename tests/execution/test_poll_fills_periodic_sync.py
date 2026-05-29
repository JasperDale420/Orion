"""poll_fills periodically re-grounds open_positions against the broker and
persists position snapshots.

Why: `_sync_risk_from_gateway` (which ground-truths open_positions against the
broker) ran startup-only, so a long-running execution process drifted high as
position_monitor closes / option expiries failed to flow back as Orion fills
(observed 2026-05-29: execution count 15 vs broker 12). And
`_maybe_snapshot_positions` was defined but never called, leaving the
positions_snapshots table empty. poll_fills runs every loop iteration, so it's
the natural home for both — interval-gated for the resync, self-throttled for
the snapshot.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from orion.execution.execution_engine import ExecutionEngine
from orion.execution.risk.manager import RiskManager


def _engine():
    ee = ExecutionEngine()
    ee._gateway_available = True
    ee._gateway_check_ts = datetime.now(UTC)
    client = AsyncMock()
    client.get_account.return_value = {"equity": "50000", "last_equity": "50000"}
    client.get_orders.return_value = []
    ee._gateway_client = client
    ee._get_gateway_client = lambda: client
    ee.risk_manager = RiskManager()
    # Spy on the periodic-maintenance methods (their own behavior is tested
    # elsewhere); here we only pin that poll_fills drives them on cadence.
    ee._sync_risk_from_gateway = AsyncMock()
    ee._maybe_snapshot_positions = AsyncMock()
    return ee


@pytest.mark.asyncio
async def test_poll_fills_resyncs_positions_on_first_call():
    ee = _engine()
    assert ee._last_position_sync_ts is None
    await ee.poll_fills()
    ee._sync_risk_from_gateway.assert_awaited_once()
    assert ee._last_position_sync_ts is not None


@pytest.mark.asyncio
async def test_poll_fills_skips_resync_within_interval():
    ee = _engine()
    ee._last_position_sync_ts = datetime.now(UTC)  # just synced
    await ee.poll_fills()
    ee._sync_risk_from_gateway.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_fills_resyncs_after_interval_elapsed():
    ee = _engine()
    ee._last_position_sync_ts = datetime.now(UTC) - timedelta(seconds=ee._POSITION_SYNC_MIN_INTERVAL_SECONDS + 1)
    await ee.poll_fills()
    ee._sync_risk_from_gateway.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_fills_persists_position_snapshot():
    ee = _engine()
    await ee.poll_fills()
    ee._maybe_snapshot_positions.assert_awaited()


@pytest.mark.asyncio
async def test_poll_fills_resync_failure_does_not_break_polling():
    """A transient resync error must not abort fill polling."""
    ee = _engine()
    ee._sync_risk_from_gateway = AsyncMock(side_effect=RuntimeError("gateway blip"))
    # Should not raise.
    await ee.poll_fills()
    ee._maybe_snapshot_positions.assert_awaited()
