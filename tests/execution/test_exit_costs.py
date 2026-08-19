"""Exit-side execution-cost math (`orion.execution.exit_costs`).

Entry-side cost is already measurable (`decision_trace_json.entry_quote`); these
helpers are the exit-side counterpart. `build_exit_quote` normalizes whatever
quote the close path had in hand into a JSON-safe record; `realized_exit_slippage`
turns that record plus the close fill into the cost figures (slippage vs mid, vs
the bid touch, vs the mark the exit rule evaluated, and the effective half-spread).

Sign convention throughout: POSITIVE means cost (we sold below the reference /
bought above it), negative means price improvement.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from orion.execution.exit_costs import build_exit_quote, realized_exit_slippage

pytestmark = pytest.mark.unit


def test_build_exit_quote_from_two_sided_quote() -> None:
    q = build_exit_quote(
        {"bid": 1.00, "ask": 1.10},
        mark_used_by_rule=1.08,
        source="gateway_option_quote",
        decision_ts=datetime(2026, 8, 16, 14, 30, tzinfo=UTC),
    )

    assert q["bid"] == 1.00
    assert q["ask"] == 1.10
    assert q["mid"] == pytest.approx(1.05)
    assert q["spread_pct"] == pytest.approx(0.0952, abs=1e-4)
    assert q["mark_used_by_rule"] == 1.08
    assert q["source"] == "gateway_option_quote"
    assert q["decision_ts"] == "2026-08-16T14:30:00+00:00"


def test_build_exit_quote_is_json_serialisable() -> None:
    """`details` is a JSON column — a datetime in the payload would fail the write."""
    q = build_exit_quote({"bid": 1.0, "ask": 1.2}, mark_used_by_rule=1.1, source="gateway_option_quote")
    assert json.loads(json.dumps(q))["mid"] == pytest.approx(1.1)


def test_build_exit_quote_without_a_quote_records_the_mark_and_says_so() -> None:
    """No fresh quote on the close path: the mark is all we have, and `source`
    must make that unambiguous so the measurement isn't read as a real market."""
    q = build_exit_quote(None, mark_used_by_rule=2.5, source="tracked_mark")

    assert q["bid"] is None
    assert q["ask"] is None
    assert q["mid"] is None
    assert q["spread_pct"] is None
    assert q["mark_used_by_rule"] == 2.5
    assert q["source"] == "tracked_mark"


@pytest.mark.parametrize(
    "quote",
    [
        {"bid": 0, "ask": 1.10},  # zero bid: nothing to sell into
        {"bid": 1.00, "ask": None},  # missing ask: not a two-sided market
        {"bid": "n/a", "ask": "n/a"},  # unparseable
        {"bid": True, "ask": True},  # bools are not prices
        {"bid": float("nan"), "ask": 1.10},  # NaN/Infinity are not JSON literals Postgres accepts
        {"bid": 1.00, "ask": float("inf")},
    ],
)
def test_build_exit_quote_rejects_one_sided_or_unparseable_quotes(quote: dict[str, object]) -> None:
    q = build_exit_quote(quote, mark_used_by_rule=1.0, source="gateway_option_quote")
    assert q["mid"] is None
    assert q["spread_pct"] is None


def test_realized_exit_slippage_sell_below_mid_is_a_cost() -> None:
    q = build_exit_quote({"bid": 0.94, "ask": 1.06}, mark_used_by_rule=1.10, source="gateway_option_quote")

    s = realized_exit_slippage(q, 0.95, side="sell")

    assert s["slippage_vs_mid_usd"] == pytest.approx(0.05)  # mid 1.00, sold 0.95
    assert s["slippage_vs_mid_pct"] == pytest.approx(0.05)
    assert s["slippage_vs_bid_usd"] == pytest.approx(-0.01)  # filled a cent ABOVE the bid
    assert s["effective_half_spread_usd"] == pytest.approx(0.05)
    assert s["slippage_vs_mark_usd"] == pytest.approx(0.15)  # the rule thought it was worth 1.10
    assert s["slippage_vs_mark_pct"] == pytest.approx(0.1364, abs=1e-4)


def test_realized_exit_slippage_buy_to_cover_above_mid_is_a_cost() -> None:
    q = build_exit_quote({"bid": 0.94, "ask": 1.06}, mark_used_by_rule=1.00, source="gateway_option_quote")

    s = realized_exit_slippage(q, 1.05, side="buy")

    assert s["slippage_vs_mid_usd"] == pytest.approx(0.05)
    assert s["slippage_vs_bid_usd"] == pytest.approx(0.11)  # paid the far touch plus
    assert s["effective_half_spread_usd"] == pytest.approx(0.05)


def test_realized_exit_slippage_sell_above_mid_is_price_improvement() -> None:
    q = build_exit_quote({"bid": 0.94, "ask": 1.06}, mark_used_by_rule=None, source="gateway_option_quote")

    s = realized_exit_slippage(q, 1.02, side="sell")

    assert s["slippage_vs_mid_usd"] == pytest.approx(-0.02)
    assert s["effective_half_spread_usd"] == pytest.approx(0.02)  # magnitude, not signed
    assert s["slippage_vs_mark_usd"] is None
    assert s["slippage_vs_mark_pct"] is None


@pytest.mark.parametrize("fill_price", [None, 0.0, -1.0])
def test_realized_exit_slippage_without_a_usable_fill_price_is_all_none(fill_price: float | None) -> None:
    q = build_exit_quote({"bid": 0.94, "ask": 1.06}, mark_used_by_rule=1.0, source="gateway_option_quote")
    assert all(v is None for v in realized_exit_slippage(q, fill_price).values())


def test_realized_exit_slippage_with_mark_only_quote_reports_mark_fields_only() -> None:
    """The tracked-mark fallback still measures slippage vs the rule's mark —
    that is the number the exit rule actually acted on."""
    q = build_exit_quote(None, mark_used_by_rule=2.00, source="tracked_mark")

    s = realized_exit_slippage(q, 1.80, side="sell")

    assert s["slippage_vs_mid_usd"] is None
    assert s["slippage_vs_bid_usd"] is None
    assert s["effective_half_spread_usd"] is None
    assert s["slippage_vs_mark_usd"] == pytest.approx(0.20)
    assert s["slippage_vs_mark_pct"] == pytest.approx(0.10)


def test_realized_exit_slippage_with_no_quote_is_all_none() -> None:
    assert all(v is None for v in realized_exit_slippage(None, 1.0).values())
    assert all(v is None for v in realized_exit_slippage({}, 1.0).values())


def test_realized_exit_slippage_tolerates_broker_side_spellings() -> None:
    """Alpaca sides reach `fills.side` as 'sell', 'SELL', or 'OrderSide.SELL'."""
    q = build_exit_quote({"bid": 0.94, "ask": 1.06}, mark_used_by_rule=None, source="gateway_option_quote")

    for spelling in ("sell", "SELL", "OrderSide.SELL"):
        assert realized_exit_slippage(q, 0.95, side=spelling)["slippage_vs_mid_usd"] == pytest.approx(0.05)
    for spelling in ("buy", "BUY", "OrderSide.BUY"):
        assert realized_exit_slippage(q, 1.05, side=spelling)["slippage_vs_mid_usd"] == pytest.approx(0.05)
