"""Pure computation helpers for EOD review metrics.

Slippage analysis, Population Stability Index, volatility regime classification,
session labelling, and performance aggregation. No DB or I/O dependencies.
"""

from __future__ import annotations

import math
from datetime import time
from typing import Any
from zoneinfo import ZoneInfo


def classify_session(ts_utc: Any | None) -> str:
    """Classify a UTC timestamp into a market session label."""
    if ts_utc is None:
        return "UNKNOWN"
    try:
        et = ts_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return "UNKNOWN"

    t = et.time()
    if time(4, 0) <= t < time(9, 30):
        return "PRE"
    if time(9, 30) <= t < time(16, 0):
        return "REG"
    if time(16, 0) <= t < time(20, 0):
        return "POST"
    return "OFF"


def psi(baseline: list[float], current: list[float], *, bins: int = 10) -> float | None:
    """Population Stability Index between baseline and current distributions.

    Uses baseline quantiles to form bins (common PSI approach).
    Returns None if not computable (< 20 samples in either set).
    """
    b = [x for x in baseline if x is not None and math.isfinite(x)]
    c = [x for x in current if x is not None and math.isfinite(x)]
    if len(b) < 20 or len(c) < 20:
        return None
    b_sorted = sorted(b)
    edges: list[float] = []
    for i in range(1, bins):
        q = i / bins
        idx = int(q * (len(b_sorted) - 1))
        edges.append(b_sorted[idx])
    edges = sorted(set(edges))
    if not edges:
        return None

    def bin_idx(x: float) -> int:
        lo = 0
        hi = len(edges)
        while lo < hi:
            mid = (lo + hi) // 2
            if x <= edges[mid]:
                hi = mid
            else:
                lo = mid + 1
        return lo

    b_counts = [0] * (len(edges) + 1)
    c_counts = [0] * (len(edges) + 1)
    for x in b:
        b_counts[bin_idx(x)] += 1
    for x in c:
        c_counts[bin_idx(x)] += 1

    eps = 1e-6
    b_total = float(len(b))
    c_total = float(len(c))
    psi_val = 0.0
    for bc, cc in zip(b_counts, c_counts, strict=False):
        bp = max(bc / b_total, eps)
        cp = max(cc / c_total, eps)
        psi_val += (cp - bp) * math.log(cp / bp)
    return float(psi_val)


def adverse_slippage_bps(
    *, side: str | None, limit_price: float | None, fill_price: float | None
) -> float | None:
    """Compute adverse slippage in basis points."""
    if limit_price is None or fill_price is None:
        return None
    if limit_price <= 0:
        return None
    if not side:
        return None
    s = side.lower()
    if s in {"buy", "long"}:
        return (fill_price - limit_price) / limit_price * 10000.0
    if s in {"sell", "short"}:
        return (limit_price - fill_price) / limit_price * 10000.0
    return None


def index_orders_by_broker(orders: list[Any]) -> dict[str, Any]:
    return {o.broker_order_id: o for o in orders if o.broker_order_id}


def compute_baseline_slippage_rows(
    fills: list[Any], orders_by_broker: dict[str, Any]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for f in fills:
        order = orders_by_broker.get(f.broker_order_id)
        limit_price = order.limit_price if order is not None else None
        adverse_bps = adverse_slippage_bps(
            side=f.side, limit_price=limit_price, fill_price=f.filled_avg_price
        )
        rows.append({"adverse_slippage_bps": adverse_bps, "linked_order": order is not None})
    return rows


def summarize_slippage(slippage_rows: list[dict[str, Any]], fills_count: int) -> dict[str, Any]:
    bps_vals = [r["adverse_slippage_bps"] for r in slippage_rows if r.get("adverse_slippage_bps") is not None]
    linked = sum(1 for r in slippage_rows if r["linked_order"])
    return {
        "fills_count": fills_count,
        "linked_fills_count": linked,
        "unlinked_fills_count": len(slippage_rows) - linked,
        "mean_adverse_slippage_bps": (sum(bps_vals) / len(bps_vals)) if bps_vals else None,
        "worst_adverse_slippage_bps": max(bps_vals) if bps_vals else None,
    }


def compute_volatility_regimes(
    bars: list[Any],
) -> tuple[dict[str, float], dict[str, str], dict[str, Any]]:
    """Compute intraday realized vol per ticker using 1m closes.

    Returns (vol_by_ticker, regime_map, regime_stats).
    """
    closes_by_ticker: dict[str, list[float]] = {}
    for b in bars:
        if b.ticker and b.close is not None:
            closes_by_ticker.setdefault(b.ticker, []).append(float(b.close))

    vol_by_ticker: dict[str, float] = {}
    for tkr, closes in closes_by_ticker.items():
        if len(closes) < 30:
            continue
        rets = []
        prev = None
        for cpx in closes:
            if prev is not None and prev > 0 and cpx > 0:
                rets.append(math.log(cpx / prev))
            prev = cpx
        if len(rets) < 20:
            continue
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / max(len(rets) - 1, 1)
        vol_by_ticker[tkr] = math.sqrt(var) * math.sqrt(390.0)

    regime_map: dict[str, str] = {}
    regime_stats: dict[str, Any] = {}
    if vol_by_ticker:
        vols = sorted(vol_by_ticker.values())
        q1 = vols[int(0.33 * (len(vols) - 1))] if len(vols) > 1 else vols[0]
        q2 = vols[int(0.66 * (len(vols) - 1))] if len(vols) > 1 else vols[0]
        for tkr, v in vol_by_ticker.items():
            if v <= q1:
                regime_map[tkr] = "LOW_VOL"
            elif v <= q2:
                regime_map[tkr] = "MID_VOL"
            else:
                regime_map[tkr] = "HIGH_VOL"
        regime_stats.update(
            {"available": True, "bucket_quantiles": {"q33": q1, "q66": q2}, "computed": len(regime_map)}
        )
    else:
        regime_stats.update({"available": False, "computed": 0})

    return vol_by_ticker, regime_map, regime_stats


def aggregate_performance(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Group trade rows by key and compute PnL aggregates."""
    out: dict[str, Any] = {}
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        k = r.get(key) or "UNKNOWN"
        groups.setdefault(str(k), []).append(r)
    for k, rs in groups.items():
        pnls = [x["realized_pnl"] for x in rs if x.get("realized_pnl") is not None]
        out[k] = {
            "trades": len(rs),
            "pnl_count": len(pnls),
            "pnl_sum": float(sum(pnls)) if pnls else None,
            "pnl_mean": float(sum(pnls) / len(pnls)) if pnls else None,
        }
    return out


def percentile(vals: list[float], p: float) -> float | None:
    """Return the p-th percentile from a sorted list."""
    if not vals:
        return None
    s = sorted(vals)
    idx = int(p * (len(s) - 1))
    return float(s[idx])
