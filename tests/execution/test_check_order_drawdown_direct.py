"""Regression coverage that `check_order` directly catches drawdown breaches.

H-11 from predict 260430-1751-execution-reliability claimed that `check_order`
relied on `_check_system_health` (10s-cached CB-via-DB read) to catch drawdown
breaches, leaving a gap window. Code reading shows the claim is wrong:

    check_order → _check_loss_limits → _drawdown_breached(cfg)

`_drawdown_breached` reads `self.peak_equity` and `self.current_equity` from
in-memory state that `process_fill` has already updated. So the same-process
drawdown gap RE-1 worried about does NOT exist.

These tests pin that behavior so a future refactor can't remove the direct
check and silently re-introduce the imagined gap.
"""

from __future__ import annotations

import pytest

from orion.config import RiskSettings
from orion.execution.risk.manager import RiskManager


def _make_rm_at_drawdown(drawdown_pct: float, max_drawdown_pct: float = 0.05) -> RiskManager:
    """Construct a RiskManager already in `drawdown_pct` drawdown."""
    cfg = RiskSettings(max_daily_loss=1e9, max_drawdown_pct=max_drawdown_pct)
    rm = RiskManager(config=cfg)
    rm.peak_equity = 100_000.0
    rm.current_equity = 100_000.0 * (1 - drawdown_pct)
    rm.starting_equity = rm.current_equity
    return rm


@pytest.mark.unit
def test_check_order_rejects_when_drawdown_exceeds_limit() -> None:
    """check_order must return False when drawdown >= max_drawdown_pct, without
    relying on any external circuit-breaker DB read."""
    rm = _make_rm_at_drawdown(drawdown_pct=0.06, max_drawdown_pct=0.05)

    allowed = rm.check_order(ticker="AAPL", quantity=1.0, price=100.0, side="buy")

    assert allowed is False


@pytest.mark.unit
def test_check_order_allows_when_drawdown_below_limit() -> None:
    """check_order must return True when drawdown is below the configured limit."""
    rm = _make_rm_at_drawdown(drawdown_pct=0.02, max_drawdown_pct=0.05)

    allowed = rm.check_order(ticker="AAPL", quantity=1.0, price=100.0, side="buy")

    assert allowed is True


@pytest.mark.unit
def test_check_order_rejects_at_drawdown_threshold_boundary() -> None:
    """At exactly max_drawdown_pct, the breach is detected (`>=`)."""
    rm = _make_rm_at_drawdown(drawdown_pct=0.05, max_drawdown_pct=0.05)

    allowed = rm.check_order(ticker="AAPL", quantity=1.0, price=100.0, side="buy")

    assert allowed is False


@pytest.mark.unit
def test_check_order_drawdown_check_uses_in_memory_state() -> None:
    """Pin the contract: drawdown check uses `self.peak_equity` and
    `self.current_equity` directly. Mutating them between calls flips the
    decision without needing any DB or CB-state refresh.
    """
    cfg = RiskSettings(max_daily_loss=1e9, max_drawdown_pct=0.05)
    rm = RiskManager(config=cfg)

    rm.peak_equity = 100_000.0
    rm.current_equity = 99_000.0  # 1% drawdown — well below limit
    assert rm.check_order(ticker="AAPL", quantity=1.0, price=100.0, side="buy") is True

    # Simulate process_fill mutating equity in-memory (e.g. closing fill at loss)
    rm.current_equity = 90_000.0  # 10% drawdown — over limit
    assert rm.check_order(ticker="AAPL", quantity=1.0, price=100.0, side="buy") is False

    # Recovery is also seen immediately
    rm.current_equity = 96_000.0  # 4% drawdown — back below limit
    assert rm.check_order(ticker="AAPL", quantity=1.0, price=100.0, side="buy") is True


@pytest.mark.unit
def test_check_order_skips_drawdown_when_max_drawdown_disabled() -> None:
    """When max_drawdown_pct <= 0, drawdown gating is disabled (per
    `_drawdown_breached`)."""
    cfg = RiskSettings(max_daily_loss=1e9, max_drawdown_pct=0.0)
    rm = RiskManager(config=cfg)
    rm.peak_equity = 100_000.0
    rm.current_equity = 50_000.0  # 50% drawdown — but disabled

    assert rm.check_order(ticker="AAPL", quantity=1.0, price=100.0, side="buy") is True
