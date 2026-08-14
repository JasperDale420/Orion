"""The expiry exit must be armed for every option position, always.

`TimeToExpiryRule` silently no-ops when `position.expiry_date is None` — it
cannot evaluate what it cannot see. `expiry_date` was only ever populated from a
join back to the originating Orion decision, and `_fetch_entry_context` has
three fallbacks that return a context WITHOUT it: the join finding no row, the
fetch raising, and the fetch timing out. Any position hitting one of those rode
to expiry with no time-stop, and Alpaca auto-exercises ITM longs — which is how
Orion silently ended up holding 400 BABA shares from
`BABA260717C00105000` (7 exercises + 3 assignments in account history).

The expiry is encoded in the OCC symbol itself, so it is always derivable
without touching the database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orion.execution.exit_fallback_rules import TimeToExpiryRule
from orion.execution.position_monitor import _expiry_from_occ_symbol

pytestmark = pytest.mark.unit


def _occ_for(days_out: int) -> str:
    """An OCC call symbol expiring `days_out` days from today."""
    d = (datetime.now(UTC) + timedelta(days=days_out)).strftime("%y%m%d")
    return f"AAPL{d}C00312500"


def test_expiry_parsed_from_occ_symbol():
    got = _expiry_from_occ_symbol("BABA260717C00105000")
    assert got is not None
    assert (got.year, got.month, got.day) == (2026, 7, 17)
    assert got.tzinfo is not None, "must be tz-aware so the rule can subtract now(UTC)"


def test_equity_symbol_has_no_expiry():
    """Equity positions have no expiry — the rule must stay inert for them."""
    assert _expiry_from_occ_symbol("BABA") is None
    assert _expiry_from_occ_symbol("") is None
    assert _expiry_from_occ_symbol(None) is None


def test_rule_is_inert_without_expiry():
    """Pins the failure mode: no expiry_date means no time-stop at all."""

    class _Pos:
        expiry_date = None

    assert TimeToExpiryRule(min_dte=1).should_exit(_Pos()) is None


def test_rule_fires_on_symbol_derived_expiry_when_db_context_is_missing():
    """The regression: a position with NO decision row still gets a time-stop."""

    class _Pos:
        # What the fallback paths produce — entry_context.get("expiry_date") is
        # None — now backfilled from the OCC symbol.
        expiry_date = _expiry_from_occ_symbol(_occ_for(0))

    signal = TimeToExpiryRule(min_dte=1).should_exit(_Pos())
    assert signal is not None, "an option expiring today must trigger the time-stop"
    assert signal.urgency == "IMMEDIATE"


def test_rule_does_not_fire_on_a_far_dated_contract():
    class _Pos:
        expiry_date = _expiry_from_occ_symbol(_occ_for(30))

    assert TimeToExpiryRule(min_dte=1).should_exit(_Pos()) is None


def test_expiry_is_midnight_utc_matching_the_db_convention():
    """`candidate_trades.expiration_date` is stored at 00:00Z.

    End-of-day would push the rule's 24h-multiple comparison past the intended
    session, so the fallback must match the DB representation exactly or the
    two paths would exit on different days.
    """
    got = _expiry_from_occ_symbol("BABA260717C00105000")
    assert (got.hour, got.minute, got.second) == (0, 0, 0)


@pytest.mark.parametrize("min_dte", [1, 2])
def test_time_stop_is_reachable_during_a_trading_session(min_dte):
    """The exit must arm while the market is OPEN, not after the close.

    Friday expiry, evaluated at 15:00 ET (19:00 UTC) on the session min_dte days
    earlier — the rule has to be firing already, or the position has no
    executable exit left before expiry day.
    """
    from datetime import date as _date

    friday = _date(2026, 7, 17)  # a Friday expiry
    expiry = datetime(friday.year, friday.month, friday.day, tzinfo=UTC)

    class _Pos:
        expiry_date = expiry

    # 15:00 ET == 19:00 UTC, min_dte sessions before expiry.
    now = expiry - timedelta(days=min_dte) + timedelta(hours=19)
    remaining_days = (expiry - now).total_seconds() / 86400.0
    assert remaining_days <= min_dte, (
        f"at 15:00 ET {min_dte}d before a Friday expiry, remaining={remaining_days:.3f}d "
        f"must already be <= min_dte={min_dte} for the exit to be executable intraday"
    )
