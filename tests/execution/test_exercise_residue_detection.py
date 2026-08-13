"""Surface equity positions Orion created by having an option auto-exercised.

Orion is options-only on entry, but Alpaca auto-exercises ITM longs at expiry.
That creates an EQUITY position with no order and no fill, so it is missing from
`fills.ticker` — the set the position adapter filters on — and Orion cannot see
it, size against it, or exit it (the exit path is options-only).

Found 2026-08-13: the account held BABA 400 sh @ 110.35, which is Orion's own
`BABA260717C00105000` x4 exercised on 2026-07-17 (strike 105 + 5.35 premium =
110.35). It went unnoticed for four weeks.

Attribution has to work off local data, because the fills record for that
contract was lost. The discriminator is Orion's own option history: it traded
BABA options (11 candidates) and never traded NEUP (0) — NEUP is 3Roses'. So an
unattributed EQUITY position counts as likely exercise residue only when Orion
has traded options on that underlying.
"""

from __future__ import annotations

import pytest

from orion.execution.position_monitor import _is_exercise_residue_candidate

pytestmark = pytest.mark.unit


def test_equity_in_an_underlying_orion_traded_is_flagged():
    assert _is_exercise_residue_candidate("BABA", orion_option_underlyings={"BABA", "AAPL"}) is True


def test_equity_in_an_untraded_underlying_is_not_flagged():
    """NEUP is 3Roses' — Orion never traded NEUP options, so hands off."""
    assert _is_exercise_residue_candidate("NEUP", orion_option_underlyings={"BABA", "AAPL"}) is False


def test_option_contracts_are_never_residue():
    """An OCC contract is a normal option position, not exercise residue."""
    assert _is_exercise_residue_candidate("BABA260717C00105000", orion_option_underlyings={"BABA"}) is False


def test_empty_history_flags_nothing():
    assert _is_exercise_residue_candidate("BABA", orion_option_underlyings=set()) is False


def test_blank_symbol_is_safe():
    assert _is_exercise_residue_candidate("", orion_option_underlyings={"BABA"}) is False
    assert _is_exercise_residue_candidate(None, orion_option_underlyings={"BABA"}) is False
