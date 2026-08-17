"""Exit-side execution-cost measurement.

Transaction cost is the dominant, and until now unmeasured, term on the exit
leg: the entry records `decision_trace_json.entry_quote`, but nothing recorded
the market a close was submitted into, so realized slippage past a stop and the
realized effective spread were both unknowable.

Two pure helpers, no I/O:

* :func:`build_exit_quote` normalizes whatever quote the close path already had
  in hand into a JSON-safe record for ``exit_decisions.details['exit_quote']``.
* :func:`realized_exit_slippage` turns that record plus the close fill price
  into cost figures.

Sign convention: POSITIVE is cost. A sell filled below the reference price and a
buy filled above it both report a positive number; negative means the fill was
better than the reference (price improvement).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

# Keys every exit-quote record carries, so a consumer can rely on the shape.
EXIT_QUOTE_KEYS = ("bid", "ask", "mid", "spread_pct", "mark_used_by_rule", "decision_ts", "source")


def _price(value: Any) -> float | None:
    """A strictly positive, finite price, or None.

    Bools are not prices; NaN/Infinity are not either — and they would round-trip
    through the JSON column as literals Postgres cannot parse, failing the write
    that carries the record.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and out > 0 else None


def _is_sell(side: str) -> bool:
    """Broker sides arrive as 'sell', 'SELL', or 'OrderSide.SELL'. Anything that
    is not explicitly a buy is treated as a sell — every Orion option exit is a
    sell-to-close, so that is the safe default for an unlabelled fill."""
    return "buy" not in side.lower()


def build_exit_quote(
    quote: Mapping[str, Any] | None,
    *,
    mark_used_by_rule: float | None,
    source: str,
    decision_ts: datetime | None = None,
) -> dict[str, Any]:
    """Normalize the close-time market into a JSON-safe record.

    ``quote`` is the raw per-contract quote the close path fetched to price its
    limit, or None when no quote was available (the close then prices off the
    tracked mark — ``source`` must say so). ``mark_used_by_rule`` is the mark the
    exit rule evaluated its unrealized P&L against; comparing it to the fill is
    what separates a stale-mark exit from a genuinely wide spread.

    A mid is derived only from a two-sided quote: a one-sided or unparseable
    quote yields ``mid=None`` rather than a fabricated price.
    """
    bid = _price(quote.get("bid")) if quote else None
    ask = _price(quote.get("ask")) if quote else None
    mid = (bid + ask) / 2 if bid is not None and ask is not None else None
    return {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_pct": round((ask - bid) / mid, 4) if mid is not None and ask is not None and bid is not None else None,
        "mark_used_by_rule": _price(mark_used_by_rule),
        "decision_ts": (decision_ts or datetime.now(UTC)).isoformat(),
        "source": source,
    }


def realized_exit_slippage(
    exit_quote: Mapping[str, Any] | None,
    fill_price: float | None,
    side: str = "sell",
) -> dict[str, float | None]:
    """Realized cost of one close, measured against the quote it was decided on.

    Every field is None when the input needed for it is missing, so a
    mark-only quote still measures against the mark and a close with no usable
    fill price measures nothing at all (rather than reporting a zero that would
    drag an average toward "free").

    Returns per-unit-of-quoted-price figures (for an option contract, multiply
    by 100 for dollars per contract):

    ``slippage_vs_mid_usd`` / ``slippage_vs_mid_pct``
        Signed cost against the quote mid — the standard benchmark.
    ``slippage_vs_bid_usd``
        Signed cost against the bid: for a sell-to-close the bid is the touch we
        crossed to, so a negative number is price improvement inside the spread.
        For a buy-to-cover the bid is the FAR touch, so the figure is the full
        spread rather than the half — read it per side.
    ``effective_half_spread_usd``
        Unsigned |fill - mid|: the microstructure transaction-cost measure
        (Muravyev & Pearson), which does not net a good fill against a bad one.
    ``slippage_vs_mark_usd`` / ``slippage_vs_mark_pct``
        Signed cost against the mark the exit rule acted on. A large gap here
        with a small vs-mid gap means the rule fired on a stale mark.
    """
    none_result: dict[str, float | None] = {
        "slippage_vs_mid_usd": None,
        "slippage_vs_mid_pct": None,
        "slippage_vs_bid_usd": None,
        "effective_half_spread_usd": None,
        "slippage_vs_mark_usd": None,
        "slippage_vs_mark_pct": None,
    }
    fill = _price(fill_price)
    if not exit_quote or fill is None:
        return none_result

    # A sell that prints below the reference costs us; a buy that prints above
    # it costs us. `direction` folds that into one signed subtraction.
    direction = 1.0 if _is_sell(side) else -1.0
    mid = _price(exit_quote.get("mid"))
    bid = _price(exit_quote.get("bid"))
    mark = _price(exit_quote.get("mark_used_by_rule"))

    result = dict(none_result)
    if mid is not None:
        vs_mid = direction * (mid - fill)
        result["slippage_vs_mid_usd"] = vs_mid
        result["slippage_vs_mid_pct"] = vs_mid / mid
        result["effective_half_spread_usd"] = abs(mid - fill)
    if bid is not None:
        result["slippage_vs_bid_usd"] = direction * (bid - fill)
    if mark is not None:
        vs_mark = direction * (mark - fill)
        result["slippage_vs_mark_usd"] = vs_mark
        result["slippage_vs_mark_pct"] = vs_mark / mark
    return result
