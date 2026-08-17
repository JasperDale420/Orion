"""Option realized P&L and order sizing must carry the 100x contract multiplier.

An options quote is priced per share while quantity is in contracts, so every
(qty, price) -> dollars conversion owes a 100x factor. `RiskManager` omitted it,
which understated `current_daily_loss` — the kill-switch input — by 100x: a real
$3,879 loss booked as $38.79. `jobs/reconcile_pnl.py` and the DTBP check in
`execution_engine.py` already apply the multiplier, so the risk manager was the
sole dissenter.

Equity symbols must stay unmultiplied, so each option case is paired with an
equity case that pins the existing behavior.
"""

import pytest
from datetime import UTC, datetime

from orion.config import RiskSettings

OPTION = "AAPL260708C00312500"  # OCC: AAPL 2026-07-08 $312.50 call
EQUITY = "AAPL"


# A same-day broker timestamp: closing gains credit today's daily figure only
# when the fill can be placed in the current session.
_FILLED_AT = datetime.now(UTC)


def _order_cfg() -> RiskSettings:
    """Explicit limits: other suites mutate the shared risk_settings singleton."""
    return RiskSettings(max_order_size_usd=5000.0, max_ticker_exposure_pct=0.10, max_positions=5)


@pytest.mark.asyncio
async def test_option_realized_profit_applies_contract_multiplier(risk_manager_factory):
    rm = risk_manager_factory()
    rm.current_equity = 10000.0
    rm.current_daily_loss = 0.0

    # Buy 1 contract @ $2.00 premium, sell @ $3.00 => $1.00 x 1 x 100 = $100.
    await rm.process_fill(OPTION, 1, 2.00, "buy", fill_id="opt_buy_1", filled_at=_FILLED_AT)
    await rm.process_fill(OPTION, 1, 3.00, "sell", fill_id="opt_sell_1", filled_at=_FILLED_AT)

    assert rm.current_equity == pytest.approx(10100.0)
    assert rm.current_daily_loss == pytest.approx(-100.0)


@pytest.mark.asyncio
async def test_option_realized_loss_reaches_kill_switch_at_true_magnitude(risk_manager_factory):
    """The regression that mattered: a real $500 loss must book as $500."""
    rm = risk_manager_factory()
    rm.current_equity = 10000.0
    rm.current_daily_loss = 0.0

    # Buy 2 contracts @ $5.00 ($1,000 premium), sell @ $2.50 => -$500.
    await rm.process_fill(OPTION, 2, 5.00, "buy", fill_id="opt_buy_2", filled_at=_FILLED_AT)
    await rm.process_fill(OPTION, 2, 2.50, "sell", fill_id="opt_sell_2", filled_at=_FILLED_AT)

    assert rm.current_daily_loss == pytest.approx(500.0)
    assert rm.current_equity == pytest.approx(9500.0)


@pytest.mark.asyncio
async def test_equity_realized_pnl_has_no_multiplier(risk_manager_factory):
    """Guard against over-applying the fix to equities."""
    rm = risk_manager_factory()
    rm.current_equity = 10000.0
    rm.current_daily_loss = 0.0

    await rm.process_fill(EQUITY, 10, 100.0, "buy", fill_id="eq_buy_1", filled_at=_FILLED_AT)
    await rm.process_fill(EQUITY, 10, 110.0, "sell", fill_id="eq_sell_1", filled_at=_FILLED_AT)

    assert rm.current_equity == pytest.approx(10100.0)
    assert rm.current_daily_loss == pytest.approx(-100.0)


@pytest.mark.asyncio
async def test_option_short_cover_applies_multiplier(risk_manager_factory):
    """Sell-to-open then buy-to-cover realizes (entry - cover) x qty x 100."""
    rm = risk_manager_factory()
    rm.current_equity = 10000.0
    rm.current_daily_loss = 0.0

    await rm.process_fill(OPTION, 1, 3.00, "sell", fill_id="opt_short_1", filled_at=_FILLED_AT)
    await rm.process_fill(OPTION, 1, 2.00, "buy", fill_id="opt_cover_1", filled_at=_FILLED_AT)

    assert rm.current_equity == pytest.approx(10100.0)


def test_check_order_takes_caller_denominated_usd_not_raw_premium(risk_manager_factory):
    """Pin the production contract: ExecutionEngine passes the UNDERLYING ticker
    and ``notional = option_price * 100``, so check_order must treat `price` as
    already-USD and must NOT re-apply the contract multiplier.

    1 contract at an $80.00 premium is $8,000 notional, over the $5,000 limit.
    """
    rm = risk_manager_factory()
    rm.current_equity = 100000.0
    rm.current_daily_loss = 0.0

    notional = 80.00 * 100  # exactly what execution_engine.py computes
    assert rm.check_order(EQUITY, 1, notional, "buy", risk_override=_order_cfg()) is False


def test_check_order_normal_orion_sizing_still_passes(risk_manager_factory):
    """Normal Orion sizing (~$500 premium) must remain allowed."""
    rm = risk_manager_factory()
    rm.current_equity = 100000.0
    rm.current_daily_loss = 0.0

    notional = 5.00 * 100  # $500 position
    assert rm.check_order(EQUITY, 1, notional, "buy", risk_override=_order_cfg()) is True


def test_check_order_does_not_double_multiply_an_occ_symbol(risk_manager_factory):
    """Regression guard: an OCC symbol must not trigger a second x100.

    $500 of notional stays $500 regardless of which symbol form is passed;
    a double-multiply would read $50,000 and reject.
    """
    rm = risk_manager_factory()
    rm.current_equity = 100000.0
    rm.current_daily_loss = 0.0

    assert rm.check_order(OPTION, 1, 5.00 * 100, "buy", risk_override=_order_cfg()) is True
