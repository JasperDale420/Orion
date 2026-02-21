from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orion.config import system_settings
from orion.core.circuit_breaker import CircuitBreaker
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
    except (ValueError, TypeError):
        return None
    return ticker, period, _ensure_utc(ts)


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
    now_utc: Optional[datetime] = None,
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

    now = _ensure_utc(now_utc or datetime.now(timezone.utc))
    cand_ts = _ensure_utc(candidate.timestamp_utc)

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

    if limit_price is None:
        return PreflightResult(ok=False, reason="Missing limit_price", extra={})

    try:
        price = float(limit_price)
    except (ValueError, TypeError):
        return PreflightResult(ok=False, reason="Invalid limit_price", extra={"limit_price": limit_price})

    sl_pct = exec_params.get("stop_loss_pct", None)
    try:
        sl_pct_f = float(sl_pct) if sl_pct is not None else None
    except (ValueError, TypeError):
        sl_pct_f = None

    qty = float(risk_manager.calculate_size(entry_price=price, stop_loss_pct=sl_pct_f))
    if qty <= 0:
        return PreflightResult(ok=False, reason="Size 0", extra={"limit_price": price})

    side = "buy" if str(candidate.direction).upper() == "LONG" else "sell"
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

        rollup_snapshot[period] = {
            "ticker": row.ticker,
            "period": row.period,
            "timestamp_utc": _ensure_utc(row.timestamp_utc).isoformat(),
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
