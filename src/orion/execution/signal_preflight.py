from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orion.config import system_settings
from orion.core.circuit_breaker import CircuitBreaker
from orion.core.enums import OrderSide
from orion.shared.utils import ensure_utc as _ensure_utc
from orion.storage.models_gold import CandidateTrade, GoldTickerRollup, StrategyDecision


def _parse_rollup_id(rollup_id: str) -> tuple[str, str, datetime] | None:
    """
    rollup_id format: "{ticker}|{period}|{timestamp_utc_iso}"
    """
    parts = (rollup_id or "").split("|")
    if len(parts) != 3:
        return None
    ticker, period, ts_str = parts
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return None
    ts_utc = _ensure_utc(ts)
    assert ts_utc is not None  # ts is non-None here, so ensure_utc cannot return None
    return ticker, period, ts_utc


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    reason: str
    extra: dict[str, Any]


async def preflight_live_signal(
    session: AsyncSession,
    *,
    candidate: CandidateTrade,
    decision: StrategyDecision,
    risk_manager: Any,
    now_utc: datetime | None = None,
) -> PreflightResult:
    """
    PRD §11.2/§11.3: apply portfolio/risk/data checks *before* emitting signals_live.
    Also resolves rollup pointers into an auditable snapshot for decision_trace_json.

    `risk_manager` is expected to provide:
      - calculate_size(entry_price: float, stop_loss_pct: float | None = ...) -> float
      - check_order(ticker, quantity, price, side, timestamp=...) -> bool
    """
    if decision.decision != "EXECUTE":
        return PreflightResult(ok=False, reason="Decision is not EXECUTE", extra={})

    now = _ensure_utc(now_utc or datetime.now(UTC))
    cand_ts = _ensure_utc(candidate.timestamp_utc)
    assert now is not None and cand_ts is not None  # both inputs are non-None here

    lag_seconds = (now - cand_ts).total_seconds()
    if lag_seconds > float(system_settings.max_data_lag_seconds):
        return PreflightResult(
            ok=False,
            reason="Data Lag",
            extra={"lag_seconds": lag_seconds, "max_data_lag_seconds": float(system_settings.max_data_lag_seconds)},
        )

    cb = CircuitBreaker()
    if await cb.is_open():
        state = await cb.get_state()
        return PreflightResult(ok=False, reason="Circuit Breaker Open", extra={"circuit_breaker": state})

    exec_params = decision.execution_params or {}
    candidate_params = candidate.execution_params or {}
    limit_price = exec_params.get("limit_price", None)
    if limit_price is None:
        limit_price = candidate_params.get("limit_price", None)

    try:
        price = float(limit_price) if limit_price is not None else 0.0
    except Exception:
        price = 0.0

    # When limit_price is missing/zero (common for UW flow where underlying_price
    # isn't populated), fall back to candidate fields that carry price information.
    # The execution engine fetches live option chain prices anyway — this is just
    # so the preflight sizing sanity check has a reasonable value to work with.
    if price <= 0:
        price = float(getattr(candidate, "strike_price", 0) or 0)
    if price <= 0:
        price = float(getattr(candidate, "underlying_price", 0) or 0)
    if price <= 0:
        price = float(getattr(candidate, "premium", 0) or 0)

    if price <= 0:
        return PreflightResult(ok=False, reason="Missing limit_price", extra={})

    sl_pct = exec_params.get("stop_loss_pct", None)
    try:
        sl_pct_f = float(sl_pct) if sl_pct is not None else None
    except Exception:
        sl_pct_f = None

    qty = float(risk_manager.calculate_size(entry_price=price, stop_loss_pct=sl_pct_f))
    if qty <= 0:
        return PreflightResult(ok=False, reason="Size 0", extra={"limit_price": price})

    # Orion is options-only and only opens positions via BUY (calls for LONG
    # bets, puts for SHORT bets on the underlying). SHORT direction does not
    # mean shorting the contract; the existing `Shorting Disabled` kill switch
    # in the risk manager is preserved for the exit/close path in
    # position_monitor.
    side = OrderSide.BUY
    if not risk_manager.check_order(candidate.ticker, qty, price, side, timestamp=cand_ts):
        return PreflightResult(
            ok=False,
            reason="Risk Rejection",
            extra={"ticker": candidate.ticker, "qty": qty, "price": price, "side": side},
        )

    evidence = candidate.evidence or {}
    rollup_ids = evidence.get("rollup_ids") or []
    rollup_snapshot: dict[str, Any] = {}
    missing_rollups: list[dict[str, Any]] = []

    # Minimal required rollup context for "tradeable" signals.
    required_periods = {"5m"}

    for rid in rollup_ids:
        parsed = _parse_rollup_id(str(rid))
        if parsed is None:
            continue
        ticker, period, ts = parsed
        stmt = select(GoldTickerRollup).where(
            GoldTickerRollup.ticker == ticker,
            GoldTickerRollup.period == period,
            GoldTickerRollup.timestamp_utc == ts,
        )
        row = (await session.execute(stmt)).scalars().first()
        if row is None:
            missing_rollups.append({"ticker": ticker, "period": period, "timestamp_utc": ts.isoformat()})
            continue

        row_ts = _ensure_utc(row.timestamp_utc)
        assert row_ts is not None  # rollup row timestamp_utc is non-nullable
        rollup_snapshot[period] = {
            "ticker": row.ticker,
            "period": row.period,
            "timestamp_utc": row_ts.isoformat(),
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
            "vwap": row.vwap,
        }

    if system_settings.require_rollups_for_signals_live:
        if not rollup_ids:
            return PreflightResult(ok=False, reason="Missing rollup_ids", extra={})
        for p in required_periods:
            if p not in rollup_snapshot:
                return PreflightResult(
                    ok=False,
                    reason="Missing required rollups",
                    extra={"required_periods": sorted(required_periods), "missing_rollups": missing_rollups},
                )

    return PreflightResult(
        ok=True,
        reason="OK",
        extra={
            "rollups": rollup_snapshot,
            "missing_rollups": missing_rollups,
            "qty": qty,
            "price": price,
            "side": side,
        },
    )
