"""Regression tests for round_to_options_tick.

Alpaca rejects options orders whose limit_price doesn't match the minimum
tick: $0.10 for prices >= $3.00, $0.05 for prices < $3.00. Live orders
from Orion produced 422 Unprocessable Entity for sub-penny / float-
precision prices like 0.605, 5.789999999999999, 3.925. This module
locks in the rounding behavior so that regression doesn't reappear.
"""

from __future__ import annotations

import pytest

from orion.execution.execution_engine import round_to_options_tick


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Mid-quote sub-penny under $3 -> 0.05 increment
        (0.605, 0.60),
        (0.275, 0.30),
        (1.025, 1.00),
        (2.99, 3.00),
        # Mid-quote sub-penny at/over $3 -> 0.10 increment
        (3.925, 3.90),
        (4.025, 4.00),
        (5.475, 5.50),
        (7.875, 7.90),
        (9.675, 9.70),
        (33.325, 33.30),
        # Float-precision artefacts (the canonical 422 trigger)
        (0.6000000000000001, 0.60),
        (5.789999999999999, 5.80),
        # Already-aligned prices stay put (modulo float repr)
        (0.05, 0.05),
        (0.10, 0.10),
        (3.00, 3.00),
        (3.10, 3.10),
        # Edge: zero / negative
        (0.0, 0.0),
        (-1.0, 0.0),
    ],
)
def test_round_to_options_tick_alpaca_increments(raw: float, expected: float) -> None:
    assert round_to_options_tick(raw) == pytest.approx(expected, abs=1e-9)


def test_boundary_at_three_dollars() -> None:
    """At exactly $3.00 we switch to the $0.10 grid."""
    assert round_to_options_tick(2.999999) == pytest.approx(3.00, abs=1e-9)
    assert round_to_options_tick(3.0) == pytest.approx(3.0, abs=1e-9)
    # On the $0.10 grid, prices snap to the nearest dime. Banker's rounding
    # on the boundary halves is fine — what matters is that the result is
    # always a legal Alpaca tick (multiple of 0.10 above $3, 0.05 below).
    assert round_to_options_tick(3.04) == pytest.approx(3.0, abs=1e-9)
    assert round_to_options_tick(3.06) == pytest.approx(3.1, abs=1e-9)
    assert round_to_options_tick(3.10) == pytest.approx(3.1, abs=1e-9)
