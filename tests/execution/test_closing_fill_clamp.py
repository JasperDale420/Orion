"""Out-of-order partial-fill PnL guard.

Closing-fill realized PnL is computed from ``old_qty`` at processing time.
If partial fills arrive out of order, a closing fill could be larger than the
position the risk manager currently knows about. Realizing PnL off that
oversized quantity would feed a wrong number into ``current_daily_loss`` — the
kill-switch input.

The guard clamps the realized-PnL quantity to ``old_qty`` (which the normal
``min(...)`` path already does) and logs an ERROR (``closing_fill_exceeds_position``)
so the reorder is visible.
"""

from __future__ import annotations

import json
import logging

import pytest


def _clamp_error_payloads(caplog) -> list[dict]:
    """Extract structured payloads of closing_fill_exceeds_position ERROR logs.

    The structlog pipeline renders the record into a JSON string by the time
    caplog sees it, so the structured kwargs live inside the message JSON's
    ``extra`` block rather than on a ``record.extra`` attribute.
    """
    payloads: list[dict] = []
    for r in caplog.records:
        if r.levelno != logging.ERROR:
            continue
        try:
            parsed = json.loads(r.getMessage())
        except (ValueError, TypeError):
            continue
        extra = parsed.get("extra", {})
        if isinstance(extra, dict) and extra.get("event_type") == "closing_fill_exceeds_position":
            payloads.append(extra)
    return payloads


@pytest.mark.asyncio
async def test_closing_fill_exceeds_position_clamps_pnl_and_logs(risk_manager_factory, caplog):
    rm = risk_manager_factory()
    rm.current_equity = 10000.0
    rm.current_daily_loss = 0.0

    # Open long 10 @ 100.
    await rm.process_fill("AAPL", 10, 100.0, "buy", fill_id="open_1")
    assert rm.positions["AAPL"]["qty"] == 10

    caplog.set_level(logging.ERROR, logger="orion.execution.risk.manager")

    # A closing fill of 15 @ 110 arrives while only 10 is known (out-of-order
    # partial delivery). PnL must clamp to the known 10 contracts:
    #   (110 - 100) * 10 = 100  — NOT (110 - 100) * 15 = 150.
    outcome = await rm.process_fill("AAPL", 15, 110.0, "sell", fill_id="oversized_close")

    # PnL clamped to old_qty (10), not the oversized fill qty (15).
    assert outcome.realized_pnl == pytest.approx(100.0)
    assert rm.current_equity == pytest.approx(10100.0)
    assert rm.current_daily_loss == pytest.approx(-100.0)

    # ERROR log emitted with the diagnostic event + fields.
    payloads = _clamp_error_payloads(caplog)
    assert len(payloads) == 1, "expected exactly one closing_fill_exceeds_position ERROR log"
    extra = payloads[0]
    assert extra["ticker"] == "AAPL"
    assert extra["old_qty"] == 10
    assert extra["fill_qty"] == -15  # signed: sell


@pytest.mark.asyncio
async def test_normal_closing_fill_does_not_log_clamp(risk_manager_factory, caplog):
    """A normal in-order close (fill <= position) must not trigger the guard."""
    rm = risk_manager_factory()
    rm.current_equity = 10000.0
    rm.current_daily_loss = 0.0

    await rm.process_fill("AAPL", 10, 100.0, "buy", fill_id="open_2")

    caplog.set_level(logging.ERROR, logger="orion.execution.risk.manager")
    outcome = await rm.process_fill("AAPL", 10, 110.0, "sell", fill_id="full_close")

    assert outcome.realized_pnl == pytest.approx(100.0)
    assert _clamp_error_payloads(caplog) == [], "normal close must not emit the clamp ERROR"
