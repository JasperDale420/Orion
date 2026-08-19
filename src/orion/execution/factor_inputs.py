"""Decision-time factor inputs, assembly, and the optional shadow gate.

The pure factor formulas live in :mod:`orion.processing.factors`. This module is
the execution-side adapter: it reads the two historical inputs those formulas
need out of the database, assembles the factor record the decision trace
carries, and evaluates the optional gate.

Nothing here may block or fail an order. Both reads are indexed, row-bounded and
time-bounded; any failure degrades to a None factor plus a WARNING.

Point-in-time discipline: both lookbacks are anchored to the candidate's own
timestamp, not to the wall clock at execution. Flow is read strictly before that
timestamp, and daily closes strictly before the session it falls in, so a factor
recomputed from a persisted trace weeks later reproduces exactly.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from orion.config import system_settings
from orion.processing.factors import (
    PRIOR_FLOW_WINDOW_HOURS,
    RV_WINDOW,
    f_abs_delta,
    f_bucket,
    f_dte,
    f_hujacobs,
    f_moneyness_std,
    f_premium_usd,
    f_prior_flow_align,
    f_rv20,
    f_spread_pct,
    f_vrp,
)
from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
from orion.shared.utils import ensure_utc
from orion.storage.models import BronzeEvent
from orion.storage.models_gold import CandidateTrade, GoldTickerRollup

logger = setup_struct_logger(__name__)

# Wall-clock ceiling per read. The two reads are issued concurrently, so this is
# also the worst case the whole factor step can add to the order path.
QUERY_TIMEOUT_SECONDS = 2.0
# One ticker's 24h of UW flow runs to a few hundred prints; the cap bounds the
# payload a pathological day could produce. Ordered newest-first, so a truncated
# read keeps the most recent prints.
PRIOR_FLOW_ROW_LIMIT = 500
# A few more closes than the realized-vol window needs, to absorb missing days.
DAILY_CLOSE_ROW_LIMIT = RV_WINDOW + 5

FACTOR_NAMES = (
    "f_prior_flow_align",
    "f_rv20",
    "f_vrp",
    "f_hujacobs",
    "f_abs_delta",
    "f_moneyness_std",
    "f_dte",
    "f_premium_usd",
    "f_spread_pct",
    "f_bucket",
)


def _premium(payload: Mapping[str, Any]) -> float:
    """Dollar premium off a UW flow payload, 0.0 when absent or unparseable."""
    raw = payload.get("premium_usd")
    if raw is None:
        raw = payload.get("premium")
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


async def fetch_prior_flow_prints(
    ticker: str | None,
    as_of: datetime | None,
    *,
    window_hours: float = PRIOR_FLOW_WINDOW_HOURS,
) -> list[dict[str, Any]] | None:
    """UW flow prints on ``ticker`` in ``[as_of - window_hours, as_of)``.

    A print counts only if it had both happened *and* arrived by ``as_of``.
    Filtering on event time alone would admit a print that the vendor stamped
    before the candidate but that Orion did not receive until afterwards —
    information the candidate could not have been formed from, and the single
    largest source of accidental look-ahead in a flow-driven factor.

    The side split UW reports is an aggressor label, not separate ask/bid
    premium fields, so it is projected here: an ASK-aggressor print's whole
    premium is ask-side, a BID-aggressor print's is bid-side, and a MID print
    has no classifiable side and contributes to neither.

    Returns the print list in the shape ``f_prior_flow_align`` expects, or None
    when the read fails or times out.
    """
    anchor = ensure_utc(as_of)
    if not ticker or anchor is None:
        return None
    floor = anchor - timedelta(hours=window_hours)

    async def _read(session: Any) -> Sequence[Any]:
        stmt = (
            select(BronzeEvent.event_ts_utc, BronzeEvent.payload)
            .where(
                BronzeEvent.event_type == "UW_FLOW",
                BronzeEvent.ticker == ticker,
                BronzeEvent.event_ts_utc >= floor,
                BronzeEvent.event_ts_utc < anchor,
                BronzeEvent.received_ts_utc <= anchor,
            )
            .order_by(BronzeEvent.event_ts_utc.desc())
            .limit(PRIOR_FLOW_ROW_LIMIT)
        )
        return (await session.execute(stmt)).all()

    try:
        rows = await asyncio.wait_for(db_query(_read), timeout=QUERY_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning("factor_prior_flow_read_failed", ticker=ticker, error=str(exc))
        return None

    prints: list[dict[str, Any]] = []
    for event_ts, payload in rows:
        if not isinstance(payload, Mapping):
            continue
        side = str(payload.get("aggressor") or payload.get("aggressor_ind") or "").strip().upper()
        premium = _premium(payload)
        prints.append(
            {
                "ticker": ticker,
                "ts": ensure_utc(event_ts),
                "put_call": payload.get("put_call") or payload.get("call_put"),
                "ask_prem": premium if side == "ASK" else 0.0,
                "bid_prem": premium if side == "BID" else 0.0,
            }
        )
    return prints


async def fetch_daily_closes(
    ticker: str | None,
    as_of: datetime | None,
    *,
    limit: int = DAILY_CLOSE_ROW_LIMIT,
) -> list[float] | None:
    """Daily closes for ``ticker`` from sessions strictly before ``as_of``'s.

    Daily rollup bars are stamped at the session's UTC midnight, so the bar
    covering the candidate's own session is still forming when the candidate
    fires. Cutting at that midnight keeps the candidate's own (and every later)
    close out of the realized-vol window.

    Returns closes oldest-first, or None when the read fails or times out.
    """
    anchor = ensure_utc(as_of)
    if not ticker or anchor is None:
        return None
    session_floor = anchor.replace(hour=0, minute=0, second=0, microsecond=0)

    async def _read(session: Any) -> Sequence[Any]:
        stmt = (
            select(GoldTickerRollup.close)
            .where(
                GoldTickerRollup.ticker == ticker,
                GoldTickerRollup.period == "1d",
                GoldTickerRollup.timestamp_utc < session_floor,
            )
            .order_by(GoldTickerRollup.timestamp_utc.desc())
            .limit(limit)
        )
        return (await session.execute(stmt)).scalars().all()

    try:
        closes = await asyncio.wait_for(db_query(_read), timeout=QUERY_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning("factor_daily_closes_read_failed", ticker=ticker, error=str(exc))
        return None

    return [float(c) for c in reversed(closes)]


async def compute_candidate_factors(
    candidate: CandidateTrade,
    entry_quote: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the shadow factor record for a candidate about to be ordered.

    Args:
        candidate: The candidate being executed. Its ``timestamp_utc`` is the
            point-in-time anchor for both historical lookbacks.
        entry_quote: The decision-time quote/chain snapshot (bid, ask, iv,
            delta, underlying_price). Spot falls back to the candidate's own
            signal-time underlying price when the chain does not carry one.
        now: Decision clock, for DTE. Defaults to the current UTC time.

    Returns:
        A dict keyed by ``FACTOR_NAMES`` whose values are JSON-native (float,
        int, str or None). Never raises, and never returns a partial key set.
    """
    factors: dict[str, Any] = dict.fromkeys(FACTOR_NAMES)
    try:
        as_of = ensure_utc(candidate.timestamp_utc)
        decision_ts = ensure_utc(now) or datetime.now(UTC)
        ticker = candidate.ticker
        option_type = candidate.option_type

        prints, closes = await asyncio.gather(
            fetch_prior_flow_prints(ticker, as_of),
            fetch_daily_closes(ticker, as_of),
        )

        rv20 = f_rv20(closes)
        iv = entry_quote.get("iv")
        spot = entry_quote.get("underlying_price")
        if spot is None:
            spot = candidate.underlying_price
        dte = f_dte(candidate.expiration_date, decision_ts)

        factors.update(
            {
                "f_prior_flow_align": f_prior_flow_align(prints, ticker=ticker, as_of=as_of, option_type=option_type),
                "f_rv20": rv20,
                "f_vrp": f_vrp(rv20, iv),
                "f_hujacobs": f_hujacobs(rv20, option_type),
                "f_abs_delta": f_abs_delta(entry_quote.get("delta")),
                "f_moneyness_std": f_moneyness_std(strike=candidate.strike_price, spot=spot, iv=iv, dte_days=dte),
                "f_dte": dte,
                "f_premium_usd": f_premium_usd(candidate.premium),
                "f_spread_pct": f_spread_pct(entry_quote.get("bid"), entry_quote.get("ask")),
                "f_bucket": f_bucket(dte),
            }
        )
    except Exception as exc:
        logger.warning(
            "candidate_factors_failed",
            ticker=getattr(candidate, "ticker", None),
            option_symbol=getattr(candidate, "option_symbol", None),
            error=str(exc),
        )
    return factors


def factor_gate_reason(
    factors: Mapping[str, Any],
    gates: Mapping[str, Mapping[str, float]] | None = None,
) -> str | None:
    """SKIP reason when a computed factor falls outside its configured band.

    Gates are off unless ``ORION_FACTOR_GATES`` configures them, which keeps the
    factor set in pure shadow while its evidence is being collected. A factor
    that could not be computed never fires a gate — a missing measurement is not
    a reason to pass on a trade.

    Returns the first breached gate's reason, or None.
    """
    active = system_settings.factor_gates if gates is None else gates
    if not active:
        return None

    for name, bounds in active.items():
        value = factors.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            logger.debug("factor_gate_no_value", factor=name)
            continue
        low = bounds.get("min")
        high = bounds.get("max")
        if (low is not None and value < low) or (high is not None and value > high):
            return f"Factor gate: {name}={value:.3f} outside [{low},{high}]"
    return None
