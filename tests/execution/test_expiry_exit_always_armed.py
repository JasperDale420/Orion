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
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from orion.execution.exit_fallback_rules import TimeToExpiryRule
from orion.execution.position_monitor import _expiry_from_occ_symbol

pytestmark = pytest.mark.unit

_ET = ZoneInfo("America/New_York")


def _occ_for(days_out: int) -> str:
    """An OCC call symbol expiring `days_out` calendar days from today (ET).

    The rule counts days from the New York date, so the fixture must too —
    otherwise a suite run between 20:00 and 24:00 ET reads one day ahead.
    """
    d = (datetime.now(_ET).date() + timedelta(days=days_out)).strftime("%y%m%d")
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


@pytest.mark.parametrize(("min_dte", "fires_at_dte"), [(1, 0), (2, 1)])
def test_time_stop_is_reachable_during_a_trading_session(min_dte, fires_at_dte):
    """The exit must arm while the market is OPEN, on a session before expiry.

    Friday expiry, evaluated at 15:00 ET on the weekday `fires_at_dte` calendar
    days earlier: the rule has to be firing there (an executable intraday exit),
    and still quiet one day earlier (no premature dump of a valid position).
    """
    from datetime import date as _date

    expiry = datetime(2026, 7, 17, tzinfo=UTC)  # a Friday expiry

    class _Pos:
        expiry_date = expiry

    def _at_15_et(dte: int) -> datetime:
        day = _date(2026, 7, 17) - timedelta(days=dte)
        return datetime(day.year, day.month, day.day, 15, 0, tzinfo=_ET)

    def _frozen(moment: datetime):
        return patch(
            "orion.execution.exit_fallback_rules.datetime",
            **{"now.side_effect": lambda tz=None: moment.astimezone(tz or UTC)},
        )

    rule = TimeToExpiryRule(min_dte=min_dte)
    with _frozen(_at_15_et(fires_at_dte)):
        assert rule.should_exit(_Pos()) is not None, (
            f"min_dte={min_dte} must fire at dte={fires_at_dte}, while the session is open"
        )
    with _frozen(_at_15_et(fires_at_dte + 1)):
        assert rule.should_exit(_Pos()) is None, f"min_dte={min_dte} must stay quiet at dte={fires_at_dte + 1}"
