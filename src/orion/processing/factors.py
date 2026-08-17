"""Point-in-time option factors computed from decision-time inputs.

Every function here is pure: no I/O, no clock reads, no settings lookups. The
caller supplies everything, which is what makes the values reproducible from a
persisted decision trace weeks later.

Every function returns ``None`` instead of raising. A factor is a measurement,
and a measurement that cannot be taken is missing — never a substituted default
and never an exception on the order path. Non-finite results (NaN/inf) are also
returned as ``None``: they are not information, and they cannot be stored in a
PostgreSQL ``json`` column.

Literature the factor set is drawn from:

* Goyal, A. and Saretto, A. (2009). "Cross-section of option returns and
  volatility." *Journal of Financial Economics* 94(2), 310-326. — the
  realized-minus-implied volatility spread, here ``f_vrp``.
* Hu, G. and Jacobs, K. (2020). "Volatility and Expected Option Returns."
  *Journal of Financial and Quantitative Analysis* 55(3), 1025-1060. — expected
  call returns fall and put returns rise with underlying volatility, here
  ``f_hujacobs``.
* Pan, J. and Poteshman, A. M. (2006). "The Information in Option Volume for
  Future Stock Prices." *Review of Financial Studies* 19(3), 871-908. — signed,
  side-classified option volume predicts the underlying, here
  ``f_prior_flow_align``.
* Boyer, B. H. and Vorkink, K. (2014). "Stock Options as Lotteries."
  *Journal of Finance* 69(4), 1485-1527. — lottery-like far-OTM, short-dated
  contracts earn lower returns; the exposure coordinates are ``f_abs_delta``,
  ``f_moneyness_std`` and ``f_dte``.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from orion.execution.exit_fallback_rules import bucket_for_dte

__all__ = [
    "f_abs_delta",
    "f_bucket",
    "f_dte",
    "f_hujacobs",
    "f_moneyness_std",
    "f_premium_usd",
    "f_prior_flow_align",
    "f_rv20",
    "f_spread_pct",
    "f_vrp",
]

# Closes used by the realized-vol window, and the minimum that makes the
# estimate worth reporting.
RV_WINDOW = 20
RV_MIN_CLOSES = 15
TRADING_DAYS_PER_YEAR = 252
CALENDAR_DAYS_PER_YEAR = 365.0
# Default lookback for prior flow. 24h spans the previous session plus the
# overnight tape without reaching back into a second session's regime.
PRIOR_FLOW_WINDOW_HOURS = 24.0


def _num(value: Any) -> float | None:
    """Coerce to a finite float, or None. Booleans are not numbers here."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _side_sign(option_type: Any) -> float | None:
    """+1 for a call, -1 for a put, None for anything else.

    Accepts both the UW single-letter form ('C'/'P') and the Orion word form
    ('CALL'/'PUT'), in either case.
    """
    if not isinstance(option_type, str) or not option_type:
        return None
    head = option_type.strip().upper()[:1]
    if head == "C":
        return 1.0
    if head == "P":
        return -1.0
    return None


def _safe_log_ratio(numerator: float, denominator: float) -> float | None:
    """``log(numerator / denominator)``, or None when that is not defined.

    The ratio of two finite positive floats can still underflow to 0.0 or
    overflow to inf at the extremes of the float range, and ``math.log`` raises
    on both a zero and a negative argument. Guarding the ratio keeps the
    module's never-raise contract true for every finite input, not just for
    realistic ones.
    """
    if denominator == 0:
        return None
    ratio = numerator / denominator
    if not math.isfinite(ratio) or ratio <= 0:
        return None
    return _num(math.log(ratio))


def _as_utc(value: Any) -> datetime | None:
    """Coerce a datetime to UTC. Naive input is read as UTC."""
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def f_rv20(closes: Sequence[Any] | None) -> float | None:
    """Annualized realized volatility of the underlying from daily closes.

    ``stdev(ln(c_t / c_{t-1})) * sqrt(252)`` over the last ``RV_WINDOW`` closes,
    using the sample (ddof=1) standard deviation.

    Args:
        closes: Daily closes in chronological order, oldest first. Only the
            final 20 are used.

    Returns:
        The annualized volatility, or None when fewer than 15 closes are given
        or any close in the window is missing, non-positive or unparseable.
    """
    if not isinstance(closes, Sequence) or isinstance(closes, (str, bytes)):
        return None
    if len(closes) < RV_MIN_CLOSES:
        return None

    window = [_num(c) for c in closes[-RV_WINDOW:]]
    if any(c is None or c <= 0 for c in window):
        return None

    returns: list[float] = []
    for i in range(1, len(window)):
        step = _safe_log_ratio(window[i], window[i - 1])  # type: ignore[arg-type]
        if step is None:
            return None
        returns.append(step)
    if len(returns) < 2:
        return None
    return _num(statistics.stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR))


def f_vrp(rv20: Any, contract_iv: Any) -> float | None:
    """Volatility risk premium: ``log(rv20 / contract_iv)`` (Goyal-Saretto 2009).

    HIGH means realized vol has been running above the implied vol being
    charged, i.e. volatility is cheap for a buyer.

    Returns None unless both inputs are strictly positive and finite.
    """
    realized = _num(rv20)
    implied = _num(contract_iv)
    if realized is None or implied is None or realized <= 0 or implied <= 0:
        return None
    return _safe_log_ratio(realized, implied)


def f_hujacobs(rv20: Any, option_type: Any) -> float | None:
    """Hu-Jacobs (2020) volatility tilt: ``-rv20`` for calls, ``+rv20`` for puts.

    Expected call returns decrease and expected put returns increase with the
    volatility of the underlying, so the signed realized vol orders both wings
    in the same "higher is better" direction.

    Returns None on a missing/negative vol or an unrecognised option type.
    """
    realized = _num(rv20)
    sign = _side_sign(option_type)
    if realized is None or realized < 0 or sign is None:
        return None
    return _num(-sign * realized)


def f_abs_delta(delta: Any) -> float | None:
    """Absolute option delta — the Boyer-Vorkink (2014) lottery-exposure axis.

    A real 0.0 is present data, not missing. Returns None only when delta is
    absent or unparseable.
    """
    value = _num(delta)
    return None if value is None else abs(value)


def f_moneyness_std(*, strike: Any, spot: Any, iv: Any, dte_days: Any) -> float | None:
    """Standardized moneyness: ``ln(K/S) / (iv * sqrt(T))``, T in calendar years.

    Expresses how far out of the money the strike sits in units of the move the
    market is pricing over the contract's remaining life, so contracts with
    different tenors and vol levels are comparable.

    Returns None unless strike, spot, iv and the tenor are all strictly
    positive and finite.
    """
    k = _num(strike)
    s = _num(spot)
    sigma = _num(iv)
    days = _num(dte_days)
    if k is None or s is None or sigma is None or days is None:
        return None
    if k <= 0 or s <= 0 or sigma <= 0 or days <= 0:
        return None
    log_moneyness = _safe_log_ratio(k, s)
    denominator = sigma * math.sqrt(days / CALENDAR_DAYS_PER_YEAR)
    if log_moneyness is None or not math.isfinite(denominator) or denominator <= 0:
        return None
    return _num(log_moneyness / denominator)


def f_dte(expiration: datetime | None, as_of: datetime | None) -> int | None:
    """Calendar days to expiration: 0 means expires today, negative is expired.

    Date arithmetic, not timestamp subtraction — the same convention the
    execution engine uses to bucket and gate a candidate, so the recorded factor
    always matches the DTE the trade was actually sized against.
    """
    expiry_utc = _as_utc(expiration)
    now_utc = _as_utc(as_of)
    if expiry_utc is None or now_utc is None:
        return None
    return (expiry_utc.date() - now_utc.date()).days


def f_premium_usd(premium: Any) -> float | None:
    """Candidate premium in dollars.

    For a UW-flow candidate this is the aggregate premium of the originating
    sweep, not a per-contract price. Returns None when absent, unparseable or
    negative.
    """
    value = _num(premium)
    return None if value is None or value < 0 else value


def f_spread_pct(bid: Any, ask: Any) -> float | None:
    """Quoted spread as a fraction of mid: ``(ask - bid) / ((bid + ask) / 2)``.

    A crossed quote (bid above ask) yields a negative value, which is recorded
    as-is so the study can see it rather than having it silently discarded.
    Returns None when either side is missing, negative or the mid is not
    positive.
    """
    bid_f = _num(bid)
    ask_f = _num(ask)
    if bid_f is None or ask_f is None or bid_f < 0 or ask_f < 0:
        return None
    mid = (bid_f + ask_f) / 2
    if mid <= 0:
        return None
    return _num((ask_f - bid_f) / mid)


def f_bucket(dte: Any) -> str | None:
    """The candidate's DTE bucket (0DTE / SHORT_SWING / SWING / POSITION).

    Unlike ``bucket_for_dte``, an unknown DTE returns None rather than the
    conservative SWING default: as a recorded factor, "we don't know" must not
    be indistinguishable from a real SWING contract.
    """
    value = _num(dte)
    return None if value is None else bucket_for_dte(int(value))


def f_prior_flow_align(
    prints: Sequence[Any] | None,
    *,
    ticker: str | None,
    as_of: datetime | None,
    option_type: str | None,
    window_hours: float = PRIOR_FLOW_WINDOW_HOURS,
) -> float | None:
    """Signed net premium of strictly prior flow on the same underlying.

    Pan-Poteshman (2006) side-classified flow, in dollars rather than contracts.
    Each print contributes ``(ask_prem - bid_prem)`` signed +1 for calls and -1
    for puts, giving a bullish-positive dollar total, which is then multiplied
    by the candidate's own direction. A POSITIVE result therefore means the
    prior tape agreed with this candidate, for a call or a put alike.

    Only prints in ``[as_of - window_hours, as_of)`` count. The upper bound is
    strict: flow stamped at or after the candidate's own timestamp is not
    information the candidate could have been formed from.

    Args:
        prints: Flow prints as mappings with keys ``ticker``, ``ts`` (datetime,
            naive read as UTC), ``put_call`` ('C'/'P'/'CALL'/'PUT'),
            ``ask_prem`` and ``bid_prem`` (dollars). Entries that are not usable
            are skipped rather than failing the whole factor.
        ticker: The candidate's underlying. Prints on other underlyings are
            ignored.
        as_of: The candidate's timestamp — the point-in-time anchor.
        option_type: The candidate's option type, which is its direction.
        window_hours: Lookback length.

    Returns:
        Signed dollars, 0.0 when no print qualifies, or None when the inputs
        needed to define the window and the alignment are missing.
    """
    candidate_sign = _side_sign(option_type)
    anchor = _as_utc(as_of)
    span = _num(window_hours)
    if prints is None or candidate_sign is None or anchor is None or not ticker:
        return None
    if span is None or span <= 0:
        return None
    if not isinstance(prints, Sequence) or isinstance(prints, (str, bytes)):
        return None

    floor = anchor - timedelta(hours=span)
    net = 0.0
    for entry in prints:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("ticker") != ticker:
            continue
        ts = _as_utc(entry.get("ts"))
        if ts is None or ts >= anchor or ts < floor:
            continue
        print_sign = _side_sign(entry.get("put_call"))
        if print_sign is None:
            continue
        ask_prem = _num(entry.get("ask_prem")) or 0.0
        bid_prem = _num(entry.get("bid_prem")) or 0.0
        net += print_sign * (ask_prem - bid_prem)

    return _num(candidate_sign * net)
